const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const command = require('../../utils/my-receipt-command')
const recovery = require('../../utils/my-receipt-recovery-store')
const candidates = require('../../utils/my-receipt-candidates-contract')
const receiving = require('../../utils/my-receiving-contract')
const uploads = require('../../utils/formal-file-upload')
const REQUEST = '10000000-0000-4000-8000-000000000001'
const PERSON = '20000000-0000-4000-8000-000000000001'
const SHIPMENT = '30000000-0000-4000-8000-000000000001'
const LINE = '40000000-0000-4000-8000-000000000001'
const SERIAL = '50000000-0000-4000-8000-000000000001'
const FILE = '60000000-0000-4000-8000-000000000001'
const NOW = '2026-09-12T08:00:00.000Z'
const clone = value => JSON.parse(JSON.stringify(value))
function response() {
  return { schema_version: '1.0', request_id: REQUEST, request_no: 'REQ-TEST', request_version: 10, person_id: PERSON,
    shipment_id: SHIPMENT, shipment_no: 'SHP-TEST', shipped_at: '2026-09-10T00:00:00Z', target_location_name: '测试个人仓', checked_at: NOW,
    can_receive: true, blocked_reason: null, lines: [{ shipment_line_id: LINE, request_line_id: LINE, sku_code: 'SKU-TEST', material_name: '测试物料', base_unit: '个',
      shipped_qty: '2.000', accepted_qty: '1.000', rejected_qty: '0.000', unconfirmed_qty: '1.000', has_exception: false,
      lot_no: null, tracking_mode: 'serial', quantity_scale: 0, allow_fraction: false, remaining_serials: [{ serial_id: SERIAL, serial_no: 'SN-TEST', qr_code: 'QR-TEST' }] }] }
}
function candidate(raw = response()) { return candidates.validateCandidates(raw, REQUEST, SHIPMENT, PERSON) }
function body() { return command.buildCommand(candidate(), { [LINE]: { condition: 'normal', serials: { [SERIAL]: 'accepted' } } }, NOW, Date.parse(NOW)) }
function marker(payload = body()) {
  return recovery.validateMarker({ v: 1, kind: 'my_receipt', request_id: REQUEST, shipment_id: SHIPMENT, person_id: PERSON,
    authorization_version: 7, expected_request_version: payload.expected_request_version, received_at: payload.received_at,
    trace_request_id: `wxreq-${'a'.repeat(36)}`, request_hash: command.requestHash(REQUEST, PERSON, payload) })
}
function result(payload = body()) {
  return { schema_version: '1.0', request_id: REQUEST, person_id: PERSON, receipt_id: '70000000-0000-4000-8000-000000000001', receipt_no: 'RCT-TEST', shipment_id: SHIPMENT,
    received_at: payload.received_at, status: payload.lines.every(line => line.condition === 'normal') ? 'accepted' : 'exception',
    request_hash: command.requestHash(REQUEST, PERSON, payload), idempotency_replayed: false,
    lines: payload.lines.map((line, i) => Object.assign({}, line, { receipt_line_id: `80000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}` })) }
}
function storage() {
  const values = new Map()
  return { values, getStorageInfoSync: () => ({ keys: [...values.keys()] }), getStorageSync: key => values.has(key) ? values.get(key) : '',
    setStorageSync: (key, value) => values.set(key, value), removeStorageSync: key => values.delete(key) }
}
function harness(options = {}) {
  const local = options.storage || storage()
  const store = options.store || recovery.createStore({ storage: local, state: { active: new Set(), faults: new Set() } })
  const state = { user: { person_id: PERSON, authorization_version: 7 }, token: 'fixture-token', calls: [], scans: 0, modals: [], counter: 0 }
  let definition, accessReads = 0
  const request = async (endpoint, config) => {
    state.calls.push({ endpoint, ...clone(config) })
    if (options.request) return options.request(endpoint, config, state)
    if (config.method === 'POST' && endpoint.endsWith('/my-receipts')) return result(clone(config.data))
    return options.response ? options.response(state) : response()
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../pages/formal-my-receipt/index.js'), 'utf8'), {
    Page(value) { definition = value },
    wx: { stopPullDownRefresh() {},
      showModal(config) { state.modals.push(config); if (options.modal) options.modal(config, state); else config.success({ confirm: true }) },
      scanCode(config) { state.scans++; state.scanCallbacks = config; if (options.scan) options.scan(config, state); else config.success({ result: 'QR-TEST' }) }
    },
    require(module) {
      if (module === '../../utils/api') return { request, createRequestId: () => `wxreq-${String(++state.counter).padStart(36, 'a')}`, createIdempotencyKey: () => `wxidem-${String(++state.counter).padStart(36, 'b')}` }
      if (module === '../../utils/session') return { getUser: () => state.user, getToken: () => state.token, ensureLogin: () => !!state.token }
      if (module === '../../utils/material-request-adapter') return { formalMaterialRequestAdapter: {
        async loadIdentityNoReplay() { state.calls.push({ identity: true }); return options.identity ? options.identity(state) : clone(state.user) },
        async loadAccessNoReplay() { state.calls.push({ access: true }); const access = { can_read: true, can_read_material_catalog: true }; return options.access ? options.access(++accessReads, access) : access }
      } }
      if (module === '../../utils/my-receiving-contract') return receiving
      if (module === '../../utils/my-receipt-candidates-contract') return candidates
      if (module === '../../utils/my-receipt-command') return command
      if (module === '../../utils/my-receipt-recovery-store') return Object.assign({}, recovery, { getStore: () => store })
      if (module === '../../utils/formal-file-upload') return options.uploads || uploads
      throw new Error(module)
    }
  })
  const page = Object.assign({}, definition, { data: clone(definition.data), setData(value) { Object.assign(this.data, clone(value)) } })
  page.onLoad({ request_id: REQUEST, shipment_id: SHIPMENT })
  return { page, state, storage: local, store }
}
module.exports = { REQUEST, PERSON, SHIPMENT, LINE, SERIAL, FILE, NOW, clone, response, candidate, body, marker, result, storage, harness }
