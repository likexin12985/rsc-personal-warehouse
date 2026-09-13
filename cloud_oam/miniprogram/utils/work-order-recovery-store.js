// Persist only recovery coordinates and digests, never an auth token,
// idempotency key, full command, quantity, material details or scanned codes.
const { uuid } = require('./work-order-query-contract')
const { canonical, KINDS } = require('./work-order-command')
const PREFIX = 'rsc_oam_work_order_command_v1:'
const FIELDS = ['v', 'kind', 'work_order_id', 'person_id', 'authorization_version', 'operation_type', 'trace_request_id', 'request_hash']
const DEFAULT_STATE = { active: new Set(), faults: new Set() }
function fail(message = '工单恢复存储不可用，请保留原记录，暂勿再次提交。') { throw new Error(message) }
// Return-receipt recovery is scoped to the exact parcel. Older work-order
// commands remain keyed only by work_order_id; a receiving marker adds the
// shipment id so two parcels from one work order cannot share a tombstone.
function keyOf(value) {
  const order = uuid(value.work_order_id)
  return value.shipment_id ? `${order}:${uuid(value.shipment_id)}` : order
}
function validateMarker(value) {
  const reversing = value && value.kind === 'work_order_reversal'
  const returning = value && value.kind === 'stock_return'
  const receiving = returning && value.operation_type === 'receive_return'
  const fields = returning ? FIELDS.concat('plan_hash', 'operation_id', ...(receiving ? ['shipment_id'] : [])) : reversing ? FIELDS.concat('plan_hash') : FIELDS
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).sort().join('|') !== fields.slice().sort().join('|')
    || value.v !== 1 || !(value.kind === 'work_order_material' && KINDS.includes(value.operation_type)
      || value.kind === 'work_order_replacement' && value.operation_type === 'replace'
      || value.kind === 'work_order_removed_registration' && value.operation_type === 'register_removed'
      || reversing && value.operation_type === 'reverse'
      || returning && ['submit_return', 'cancel_return', 'outbound_return', 'ship_return', 'receive_return'].includes(value.operation_type))
    || !Number.isSafeInteger(value.authorization_version) || value.authorization_version < 1
    || typeof value.trace_request_id !== 'string' || !/^wxreq-[a-f0-9]{36}$/.test(value.trace_request_id)
    || typeof value.request_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.request_hash)
    || ((reversing || returning && ['submit_return', 'outbound_return', 'ship_return', 'receive_return'].includes(value.operation_type)) && (typeof value.plan_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.plan_hash)))
    || (receiving && (typeof value.shipment_id !== 'string' || !/^[-0-9a-f]{36}$/i.test(value.shipment_id)))
    || (returning && (value.operation_type === 'submit_return' ? value.operation_id !== null : value.operation_type === 'cancel_return' && value.plan_hash !== null))) fail()
  return Object.freeze({ v: 1, kind: value.kind, work_order_id: uuid(value.work_order_id), person_id: uuid(value.person_id),
    authorization_version: value.authorization_version, operation_type: value.operation_type,
    trace_request_id: value.trace_request_id, request_hash: value.request_hash, ...(reversing || returning ? { plan_hash: value.plan_hash } : {}),
    ...(returning ? { operation_id: value.operation_type === 'submit_return' ? null : uuid(value.operation_id) } : {}),
    ...(receiving ? { shipment_id: uuid(value.shipment_id) } : {}) })
}
function createStore(options = {}) {
  const storage = options.storage === undefined ? (typeof wx === 'undefined' ? null : wx) : options.storage
  const state = options.state || DEFAULT_STATE
  function directory() {
    if (!storage || !['getStorageInfoSync', 'getStorageSync', 'setStorageSync', 'removeStorageSync'].every(name => typeof storage[name] === 'function')) fail()
    const info = storage.getStorageInfoSync()
    if (!info || !Array.isArray(info.keys) || info.keys.some(key => typeof key !== 'string') || new Set(info.keys).size !== info.keys.length) fail()
    return new Set(info.keys)
  }
  function read(value) {
    const id = keyOf(value)
    if (state.faults.has(id)) return { kind: 'unavailable' }
    try {
      const key = PREFIX + id, present = directory().has(key), raw = storage.getStorageSync(key)
      if (directory().has(key) !== present) fail()
      if (!present) { if (raw !== '') fail(); return { kind: 'missing' } }
      if (typeof raw !== 'string' || raw.length > 1600) fail()
      const checked = validateMarker(JSON.parse(raw))
      if (keyOf(checked) !== id) fail()
      return { kind: 'valid', value: checked }
    } catch (_) { state.faults.add(id); return { kind: 'unavailable' } }
  }
  function listPending(personId) {
    const person = uuid(personId)
    try {
      const keys = [...directory()].filter(key => key.startsWith(PREFIX)).sort()
      if (keys.length > 1000) fail()
      const items = [], snapshots = []
      let partial = state.faults.size > 0
      for (const key of keys) {
        let id, scope
        try {
          const suffix = key.slice(PREFIX.length), parts = suffix.split(':')
          id = uuid(parts[0])
          if (parts.length > 2) fail()
          scope = { work_order_id: id }
          if (parts.length === 2) scope.shipment_id = uuid(parts[1])
          if (key !== PREFIX + keyOf(scope)) { state.faults.add(id); fail() }
        } catch (_) { partial = true; continue }
        const record = read(scope)
        if (record.kind !== 'valid') { partial = true; continue }
        snapshots.push(record.value)
        if (record.value.person_id === person) items.push(record.value)
      }
      // Never describe a changing or inaccessible directory as an empty inbox.
      if (keys.join('|') !== [...directory()].filter(key => key.startsWith(PREFIX)).sort().join('|')) fail()
      for (const marker of snapshots) {
        const after = read(marker)
        if (after.kind !== 'valid' || canonical(after.value) !== canonical(marker)) fail()
      }
      return { kind: partial ? 'partial' : 'ready', items: Object.freeze(items) }
    } catch (_) { return { kind: 'unavailable', items: Object.freeze([]) } }
  }
  return Object.freeze({ read, listPending, async withLease(value, work) {
    const id = keyOf(value)
    if (state.active.has(id) || read(value).kind === 'unavailable') fail('该工单正在处理或恢复记录不可用，请先核验原结果。')
    state.active.add(id)
    let live = true
    const check = marker => {
      if (!live || !state.active.has(id) || state.faults.has(id)) fail()
      const checked = validateMarker(marker)
      if (keyOf(checked) !== id) fail()
      return checked
    }
    const lease = {
      read() { if (!live) fail(); return read(value) },
      persist(marker) {
        const checked = check(marker)
        if (read(value).kind !== 'missing') fail('原工单结果未确认，禁止覆盖恢复记录。')
        try {
          storage.setStorageSync(PREFIX + id, JSON.stringify(checked))
          const after = read(value)
          if (after.kind !== 'valid' || canonical(after.value) !== canonical(checked)) fail()
        } catch (_) { state.faults.add(id); fail('恢复记录写后核验失败，已停止发送请求。') }
      },
      clearExact(marker) {
        const checked = check(marker), before = read(value)
        if (before.kind !== 'valid' || canonical(before.value) !== canonical(checked)) fail('恢复记录已变化，禁止清除。')
        try {
          storage.removeStorageSync(PREFIX + id)
          if (read(value).kind !== 'missing') fail()
        } catch (_) { state.faults.add(id); fail('恢复记录清理未确认，请保留原结果继续核验。') }
      }
    }
    try { return await work(Object.freeze(lease)) } finally { live = false; state.active.delete(id) }
  } })
}
let singleton
function getStore() { singleton ||= createStore(); return singleton }
module.exports = { PREFIX, validateMarker, createStore, getStore }
