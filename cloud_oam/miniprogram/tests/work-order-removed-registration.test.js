const test = require('node:test')
const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const command = require('../utils/work-order-removed-registration-command')
const ordinary = require('../utils/work-order-command')
const { createStore, validateMarker, PREFIX } = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const { submitDraft } = require('../utils/work-order-submit')
const { submitReplacement } = require('../utils/work-order-replacement-submit')
const { submitRegistration } = require('../utils/work-order-removed-registration-submit')
const id = n => `abcdef00-0000-4000-8000-${String(n).padStart(12, '0')}`
const clone = value => JSON.parse(JSON.stringify(value))
function input(lot = false) { return { workOrderId: id(1), personId: id(2), scan: { operator_person_id: id(2), basis_stock_account_id: id(3),
  condition_before: 'damaged', sku_code: '拆回-配件', lot_no: lot ? '批次-01' : null, serial_no: '旧件-SN', qr_code: '二维码-🔧' } } }
function expected(lot = false) { return { ...input(lot), authorizationVersion: 7, sourceVersion: 'source-v2', ledgerCursor: 18 } }
function preview(lot = false) {
  const value = input(lot)
  return { schema_version: '1.0', status: 'registration_validated', work_order_id: id(1), operator_person_id: id(2), authorization_version: 7,
    source_version: 'source-v2', ledger_cursor: 19, checked_at: '2026-09-13T08:00:00.123456Z', basis_stock_account_id: id(3),
    material_id: id(4), sku_code: value.scan.sku_code, material_name: '拆回配件', base_unit: '件', condition_before: 'damaged',
    tracking_mode: lot ? 'lot_and_serial' : 'serial', quantity_scale: 0, allow_fraction: false,
    lot_id: lot ? id(5) : null, lot_no: value.scan.lot_no, serial_id: null, serial_no: value.scan.serial_no, request_hash: command.requestHash(value) }
}
function marker() { return validateMarker({ v: 1, kind: command.KIND, work_order_id: id(1), person_id: id(2), authorization_version: 7,
  operation_type: 'register_removed', trace_request_id: 'wxreq-' + 'a'.repeat(36), request_hash: command.requestHash(input()) }) }
function registered() { const value = marker(); return { schema_version: '1.0', status: 'registered', registration_id: id(6), registration_no: 'WORS-'+'A'.repeat(24),
  work_order_id: id(1), operator_person_id: id(2), serial_id: id(7), material_id: id(4), lot_id: null, basis_stock_account_id: id(3),
  request_id: value.trace_request_id, request_hash: value.request_hash, registered_at: '2026-09-13T08:00:01Z' } }
function sealed() { const value = marker(); return { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: {
  seal_id: id(8), work_order_id: id(1), operator_person_id: id(2), operation_type: 'register_removed', request_id: value.trace_request_id,
  request_hash: value.request_hash, sealed_at: '2026-09-13T08:00:01Z' } } }
function backing() { const data = new Map(); return { data, getStorageInfoSync: () => ({ keys: [...data.keys()] }),
  getStorageSync: key => data.has(key) ? data.get(key) : '', setStorageSync: (key, value) => data.set(key, value), removeStorageSync: key => data.delete(key) } }
function storeFor(storage) { return createStore({ storage, state: { active: new Set(), faults: new Set() } }) }
async function pending() { const storage = backing(), store = storeFor(storage); await store.withLease(marker(), lease => lease.persist(marker())); return { storage, store } }
const missing = () => Object.assign(new Error('no original registration'), { responseReceived: true, status: 404, code: 'removed_registration_not_found' })

test('registration hash binds physical Unicode, original basis and all three codes without stock fields', () => {
  const value = input(), before = clone(value)
  assert.equal(command.requestHash(value), createHash('sha256').update(ordinary.canonical(command.command(value)), 'utf8').digest('hex'))
  assert.deepEqual(command.payload(value), value.scan); assert.deepEqual(value, before)
  for (const field of ['sku_code', 'serial_no', 'qr_code', 'lot_no', 'condition_before', 'basis_stock_account_id']) {
    const changed = clone(value)
    changed.scan[field] = field === 'basis_stock_account_id' ? id(9) : field === 'condition_before' ? 'used' : String(changed.scan[field]) + '-other'
    assert.notEqual(command.requestHash(changed), command.requestHash(value))
  }
  for (const changed of [{ ...value, personId: id(9) }, { ...value, scan: { ...value.scan, quantity: '1' } },
    { ...value, scan: { ...value.scan, serial_no: null, qr_code: null } }, { ...value, scan: { ...value.scan, qr_code: '\ud800' } }]) assert.throws(() => command.command(changed))
})
test('registration preview requires exact metadata, pending identity and current source and authority', () => {
  for (const lot of [false, true]) {
    assert.equal(command.validatePreview(preview(lot), expected(lot)).serial_id, null)
    for (const mutate of [x => { x.serial_id = id(7) }, x => { x.serial_no = 'WRONG' }, x => { x.sku_code = 'WRONG' },
      x => { x.operator_person_id = id(9) }, x => { x.authorization_version++ }, x => { x.source_version = 'stale' },
      x => { x.ledger_cursor = 1 }, x => { x.checked_at = 'bad' }, x => { x.basis_stock_account_id = id(9) },
      x => { x.tracking_mode = 'none' }, x => { x.lot_no = 'wrong' }, x => { x.material_id = null }, x => { x.request_hash = 'b'.repeat(64) },
      x => { x.status = 'registered' }, x => { x.qr_code = 'forbidden' }, x => { x.quantity_scale = 4 }]) {
      const copy = preview(lot); mutate(copy); assert.throws(() => command.validatePreview(copy, expected(lot)))
    }
  }
})
test('only the original safe registered or sealed proof matches its independent command kind', () => {
  command.validateLookup(registered(), marker()); command.validateLookup(sealed(), marker())
  for (const mutate of [r => { r.request_id += 'x' }, r => { r.request_hash = 'b'.repeat(64) }, r => { r.operator_person_id = id(9) },
    r => { r.serial_id = null }, r => { r.lot_id = 'missing' }, r => { r.registration_no = 'WOM-OTHER' },
    r => { r.qr_code = 'forbidden' }, r => { r.status = 'posted' }, r => { r.registered_at = 'invalid' }]) {
    const copy = registered(); mutate(copy); assert.throws(() => command.validateLookup(copy, marker()))
  }
  for (const value of [{ ...marker(), kind: 'work_order_replacement', operation_type: 'replace' },
    { ...marker(), operation_type: 'occupy' }]) assert.throws(() => command.validateLookup(registered(), value))
  const copy = sealed(); copy.seal.operation_type = 'replace'; assert.throws(() => command.validateLookup(copy, marker()))
})
test('registration shares one per-order recovery lock with both ordinary and paired stock writes', async () => {
  const { store, storage } = await pending()
  assert.equal(storage.data.size, 1); assert.deepEqual(storeFor(storage).listPending(id(2)).items, [marker()])
  const persisted = storage.getStorageSync(PREFIX + id(1))
  for (const field of ['qr_code', 'serial_no', 'material_id', 'quantity', 'idempotency_key', 'token']) assert.equal(persisted.includes(field), false)
  let requests = 0
  for (const submit of [submitDraft, submitReplacement, submitRegistration]) await assert.rejects(submit({ api: {}, store,
    workOrderId: id(1), personId: id(2), authorizationVersion: 7, drafts: {}, removedDrafts: [], row: {},
    authorize: async () => { requests++; return 'same' }, confirm: async () => true }))
  assert.equal(requests, 0)
  for (const [kind, operation] of [['work_order_material', 'consume'], ['work_order_replacement', 'replace']]) {
    await assert.rejects(store.withLease(marker(), lease => lease.persist({ ...marker(), kind, operation_type: operation })))
    const other = storeFor(backing()); await other.withLease(marker(), lease => lease.persist({ ...marker(), kind, operation_type: operation }))
    await assert.rejects(submitRegistration({ api: {}, store: other, workOrderId: id(1), personId: id(2), authorizationVersion: 7, row: {}, authorize: async () => 'same' }))
  }
})
test('registration restart reads its own GET and only proven nonexecution allows sealing', async () => {
  for (const outcome of ['registered', 'sealed']) {
    const { store } = await pending(), calls = []; let posted = false
    const result = await (outcome === 'sealed' ? sealPending : recoverPending)({ store, workOrderId: id(1), personId: id(2),
      authorize: async () => 'same', confirm: async () => true, api: {
        request: async (path, options) => { calls.push({ path, ...options }); if (outcome === 'sealed' && !posted) throw missing(); return outcome === 'sealed' ? sealed() : registered() },
        postSealNoReplay: async (path, body, options) => { posted = true; calls.push({ path, body, ...options }); assert.deepEqual(body, { operator_person_id: id(2), request_hash: marker().request_hash }) }
      } })
    assert.equal(result.status, outcome === 'sealed' ? 'sealed' : 'confirmed'); assert.equal(store.read(marker()).kind, 'missing')
    assert.ok(calls.every(call => call.path.includes('/material-replacements/removed-registrations/by-request/')))
    assert.equal(calls.filter(call => call.method === 'GET').length, outcome === 'sealed' ? 2 : 1)
  }
})
test('missing, wrong or uncertain registration evidence never clears its marker', async () => {
  for (const mode of ['exact404', 'other404', 'timeout', 'hash', 'partial', 'wrong_kind', 'authority', 'storage']) {
    const { store, storage } = await pending(); let checks = 0
    if (mode === 'storage') storage.removeStorageSync = () => {}
    const work = recoverPending({ store, workOrderId: id(1), personId: id(2), authorize: async () => mode === 'authority' ? String(++checks) : 'same', api: {
      request: async () => {
        if (mode === 'exact404') throw missing()
        if (mode === 'other404') throw Object.assign(missing(), { code: 'replacement_not_found' })
        if (mode === 'timeout') throw new Error('timeout')
        const proof = registered()
        if (mode === 'hash') proof.request_hash = 'b'.repeat(64)
        if (mode === 'partial') delete proof.request_id
        if (mode === 'wrong_kind') return { schema_version: '1.0', status: 'posted' }
        return proof
      }
    } })
    if (mode === 'exact404') assert.equal((await work).status, 'pending'); else await assert.rejects(work)
    assert.notEqual(store.read(marker()).kind, 'missing'); assert.ok(storage.data.has(PREFIX + id(1)))
  }
})
