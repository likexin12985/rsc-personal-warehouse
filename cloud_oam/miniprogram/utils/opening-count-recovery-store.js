// Durable public coordinates only. Never store count bodies, quantities or keys.
// This module coordinates pages/factories in ONE mini-program service context.
// It is not a Web Lock, native process lock, cross-device lock or server lock.
const STORAGE_PREFIX = 'rsc_oam_mini_opening_count_sentinel_v1:'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const FIELDS = ['v', 'kind', 'task_id', 'round_id', 'round_no', 'scope_id',
  'actor_person_id', 'actor_authorization_version', 'trace_request_id']
const COORDINATORS = new WeakMap()

function invalid(message = '盘点恢复记录无效，已停止写入') {
  const error = new Error(message)
  error.status = 409
  error.responseReceived = false
  throw error
}

function taskId(value) {
  if (typeof value !== 'string' || !UUID.test(value)) invalid()
  return value
}

function validateOpeningCountSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid()
  if (Object.keys(value).length !== FIELDS.length || FIELDS.some(
    (key) => !Object.prototype.hasOwnProperty.call(value, key)
  ) || value.v !== 1 || value.kind !== 'opening_scope_count'
    || ![value.task_id, value.round_id, value.scope_id, value.actor_person_id].every(
      (id) => typeof id === 'string' && UUID.test(id)
    ) || !Number.isSafeInteger(value.round_no) || value.round_no < 1
    || !Number.isSafeInteger(value.actor_authorization_version) || value.actor_authorization_version < 1
    || typeof value.trace_request_id !== 'string' || !TRACE.test(value.trace_request_id)) invalid()
  return Object.freeze({ v: 1, kind: 'opening_scope_count', task_id: value.task_id,
    round_id: value.round_id, round_no: value.round_no, scope_id: value.scope_id,
    actor_person_id: value.actor_person_id, actor_authorization_version: value.actor_authorization_version,
    trace_request_id: value.trace_request_id })
}

/** Opaque shared service-context coordinator; no public reset or unlock escape. */
function createOpeningCountCoordinator() {
  const coordinator = Object.freeze({})
  COORDINATORS.set(coordinator, { active: new Map(), storageFaults: new Set() })
  return coordinator
}

// Module lifetime is the mini-program service-context lifetime. All default
// factories share both active leases and fault latches, not just the singleton.
const DEFAULT_COORDINATOR = createOpeningCountCoordinator()
let defaultStore

function defaultStorage() {
  try { return typeof wx === 'undefined' ? null : wx } catch (_) { return null }
}

function availableStorage(storage) {
  return storage && ['getStorageInfoSync', 'getStorageSync', 'setStorageSync', 'removeStorageSync']
    .every((name) => typeof storage[name] === 'function')
}

function storedKeys(storage) {
  const info = storage.getStorageInfoSync()
  if (!info || !Array.isArray(info.keys) || info.keys.some((key) => typeof key !== 'string')
    || new Set(info.keys).size !== info.keys.length) invalid('盘点存储目录无法确认，已停止写入')
  return new Set(info.keys)
}

function createOpeningCountRecoveryStore(options = {}) {
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const coordinator = options.coordinator === undefined ? DEFAULT_COORDINATOR : options.coordinator
  const state = coordinator && COORDINATORS.get(coordinator)

  function unavailable(id) {
    if (state) state.storageFaults.add(id)
    return Object.freeze({ kind: 'unavailable' })
  }

  function read(id) {
    if (!state || state.storageFaults.has(id)) return unavailable(id)
    try {
      if (!availableStorage(storage)) return unavailable(id)
      const key = STORAGE_PREFIX + id
      const present = storedKeys(storage).has(key)
      // Read even an apparently missing key: an actual read error is never
      // converted to permission for a new command. Inventory presence is the
      // authority; wx's empty-string fallback cannot distinguish corrupt data.
      const raw = storage.getStorageSync(key)
      if (storedKeys(storage).has(key) !== present) return unavailable(id)
      if (!present) {
        if (raw !== '') return unavailable(id)
        return Object.freeze({ kind: 'missing' })
      }
      if (typeof raw !== 'string') return Object.freeze({ kind: 'corrupt' })
      try {
        const value = validateOpeningCountSentinel(JSON.parse(raw))
        return value.task_id === id ? Object.freeze({ kind: 'valid', value }) : Object.freeze({ kind: 'corrupt' })
      } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    } catch (_) { return unavailable(id) }
  }

  return Object.freeze({
    read(id) { return read(taskId(id)) },
    async withTaskLease(id, work) {
      const checkedId = taskId(id)
      if (!state || state.storageFaults.has(checkedId)) invalid('盘点持久恢复或页面协调不可用，已停止写入')
      if (typeof work !== 'function') invalid('盘点协调回调无效，已停止写入')
      if (state.active.has(checkedId)) invalid('同一小程序上下文正在核验该盘点，请勿重复提交')
      const token = Object.freeze({})
      state.active.set(checkedId, token)
      let live = true
      let persistedByThisLease = null
      function requireLease() {
        if (!live || state.active.get(checkedId) !== token || state.storageFaults.has(checkedId)) {
          invalid('盘点协调已结束或存储异常，禁止写入')
        }
      }
      function expected(value) {
        requireLease()
        const checked = validateOpeningCountSentinel(value)
        if (checked.task_id !== checkedId) invalid('盘点恢复记录与当前任务不一致')
        return checked
      }
      const lease = Object.freeze({
        read() { requireLease(); return read(checkedId) },
        persist(value) {
          const checked = expected(value)
          const serialized = JSON.stringify(checked)
          const before = read(checkedId)
          if (before.kind === 'valid') {
            if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return
            invalid('原盘点请求仍待核验，禁止覆盖坐标或切换人员')
          }
          if (before.kind !== 'missing') invalid('盘点恢复记录不可用，禁止覆盖或新建请求')
          try {
            storage.setStorageSync(STORAGE_PREFIX + checkedId, serialized)
            const after = read(checkedId)
            if (after.kind !== 'valid' || JSON.stringify(after.value) !== serialized) invalid()
            persistedByThisLease = serialized
          } catch (_) {
            state.storageFaults.add(checkedId)
            invalid('盘点恢复记录写后核验失败，已停止发送请求')
          }
        },
        clearExact(value) {
          const checked = expected(value)
          const before = read(checkedId)
          if (before.kind !== 'valid' || JSON.stringify(before.value) !== JSON.stringify(checked)) {
            invalid('盘点恢复坐标不匹配，禁止清理')
          }
          try {
            storage.removeStorageSync(STORAGE_PREFIX + checkedId)
            if (read(checkedId).kind !== 'missing') invalid()
            persistedByThisLease = null
          } catch (_) {
            state.storageFaults.add(checkedId)
            invalid('盘点恢复记录清理未确认，继续停止写入')
          }
        }
      })
      try {
        // Capability checks precede the callback even if it never reads a marker.
        if (read(checkedId).kind === 'unavailable') invalid('盘点持久恢复不可用，已停止写入')
        return await work(lease)
      } finally {
        live = false
        if (state.active.get(checkedId) === token) state.active.delete(checkedId)
      }
    }
  })
}

function getOpeningCountRecoveryStore() {
  if (!defaultStore) defaultStore = createOpeningCountRecoveryStore()
  return defaultStore
}

/** Other task commands cannot race an unresolved count or its recovery. */
async function withNoPendingOpeningCount(id, work, store = getOpeningCountRecoveryStore()) {
  return store.withTaskLease(id, async (lease) => {
    if (lease.read().kind !== 'missing') {
      invalid('该任务有盘点请求待核验或恢复记录不可用；请先只读核验，禁止其他写入')
    }
    return work()
  })
}

module.exports = {
  STORAGE_PREFIX,
  validateOpeningCountSentinel,
  createOpeningCountCoordinator,
  createOpeningCountRecoveryStore,
  getOpeningCountRecoveryStore,
  withNoPendingOpeningCount
}
