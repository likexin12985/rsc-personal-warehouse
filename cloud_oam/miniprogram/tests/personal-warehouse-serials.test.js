const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { validate } = require('../utils/personal-warehouse-serials-contract')

const PERSON = '10000000-0000-4000-8000-000000000001'
const LOCATION = '20000000-0000-4000-8000-000000000001'
const ACCOUNT = '30000000-0000-4000-8000-000000000001'
const MATERIAL = '40000000-0000-4000-8000-000000000001'
const FIRST = '50000000-0000-4000-8000-000000000001'
const SECOND = '50000000-0000-4000-8000-000000000002'

function item(id = FIRST, serialNo = 'SN-001') { return { serial_id: id, serial_no: serialNo, lifecycle_status: 'active' } }
function page(items = [item()], patch = {}) {
  return {
    schema_version: '1.0', projection_status: 'ready', opening_balance_status: 'established',
    projected_at: '2026-09-15T08:00:00+08:00', ledger_cursor: 11,
    person_id: PERSON, location_id: LOCATION, stock_account_id: ACCOUNT, material_id: MATERIAL,
    sku_code: 'SKU-SN', material_name: 'SN 测试物料', base_unit: '个', tracking_mode: 'serial',
    condition_code: 'new', availability_bucket: 'available', lot_id: null, lot_no: null,
    total_serials: 2, items, next_after_id: null, ...patch
  }
}

test('accepts current personal SN rows and keeps QR values absent', () => {
  const result = validate(page(), { personId: PERSON, accountId: ACCOUNT })
  assert.equal(result.items[0].serial_no, 'SN-001')
  assert.equal(JSON.stringify(result).includes('qr_code'), false)
})

test('rejects leaked QR, identity drift and malformed lifecycle', () => {
  assert.throws(() => validate({ ...page(), qr_code: 'QR-SECRET' }, { personId: PERSON, accountId: ACCOUNT }), /字段不完整/)
  assert.throws(() => validate({ ...page(), person_id: LOCATION }, { personId: PERSON, accountId: ACCOUNT }), /人员/)
  assert.throws(() => validate(page([{ ...item(), lifecycle_status: 'consumed' }]), { personId: PERSON, accountId: ACCOUNT }), /生命周期/)
})

test('requires a stable cursor and an exact result for scanned SN', () => {
  const older = page([item(SECOND, 'SN-002')], { next_after_id: SECOND })
  assert.equal(validate(older, { personId: PERSON, accountId: ACCOUNT, afterId: FIRST, ledgerCursor: 11 }).next_after_id, SECOND)
  assert.throws(() => validate(older, { personId: PERSON, accountId: ACCOUNT, ledgerCursor: 12 }), /跨页账本/)
  assert.throws(() => validate(page([item(FIRST, 'OTHER')]), { personId: PERSON, accountId: ACCOUNT, serialNo: 'SN-001' }), /扫描 SN/)
})

function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function harness(options = {}) {
  const state = { user: { person_id: PERSON, authorization_version: 7 }, token: 'session-1', calls: [], scans: 0 }
  let definition
  const context = {
    Page(value) { definition = value },
    wx: {
      stopPullDownRefresh() {},
      scanCode(settings) {
        state.scans += 1
        if (options.scanFailure) settings.fail(new Error('cancelled'))
        else settings.success({ result: options.scanResult || 'SN-002' })
      }
    },
    require(module) {
      if (module === '../../utils/session') return { getToken: () => state.token, getUser: () => state.user, ensureLogin: () => !!state.token }
      if (module === '../../utils/material-request-adapter') return { formalMaterialRequestAdapter: {
        loadIdentityNoReplay: async () => ({ person_id: PERSON, authorization_version: 7 }),
        loadAccessNoReplay: async () => ({ person_id: PERSON, authorization_version: 7, can_read: true, can_read_material_catalog: true })
      } }
      if (module === '../../utils/api') return { async request(endpoint, request) {
        state.calls.push({ endpoint, request })
        if (options.response) return options.response(endpoint)
        if (endpoint.includes('serial_no=')) return page([item(SECOND, 'SN-002')])
        return page([item(FIRST, 'SN-001')], { next_after_id: FIRST })
      } }
      return require(path.resolve(__dirname, '../pages/formal-personal-warehouse-serials', module))
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-personal-warehouse-serials/index.js'), 'utf8'), context)
  const instance = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)), setData(value) { Object.assign(this.data, JSON.parse(JSON.stringify(value))) } })
  instance.onLoad({ accountId: ACCOUNT })
  return { instance, state }
}

test('page reads a scoped no-store page and preserves ledger cursor across pagination', async () => {
  const { instance, state } = harness({ response(endpoint) {
    if (endpoint.includes('after_id=')) return page([item(SECOND, 'SN-002')])
    return page([item(FIRST, 'SN-001')], { next_after_id: FIRST })
  } })
  await instance.onShow()
  assert.equal(instance.data.rows[0].serial_no, 'SN-001')
  assert.equal(state.calls[0].request.noRefresh, true)
  assert.equal(state.calls[0].request.header['Cache-Control'], 'no-store')
  await instance.nextPage()
  assert.equal(instance.data.rows[0].serial_no, 'SN-002')
  assert.match(state.calls[1].endpoint, new RegExp(`after_id=${FIRST}$`))
})

test('direct serial navigation performs one exact no-store lookup before showing the account list', async () => {
  const { instance, state } = harness()
  instance.onLoad({ accountId: ACCOUNT, serialNo: 'SN-002' })
  await instance.onShow()
  assert.equal(instance.data.scanMode, true)
  assert.equal(instance.data.rows[0].serial_no, 'SN-002')
  assert.match(state.calls[0].endpoint, /serial_no=SN-002$/)
  assert.equal(state.calls.length, 1)
  instance.showAll()
  await new Promise(resolve => setImmediate(resolve))
  assert.match(state.calls[1].endpoint, /\/serials\?limit=50$/)
})

test('SN scan stays out of page data and performs an exact read only lookup', async () => {
  const { instance, state } = harness()
  await instance.onShow()
  await instance.scanSerial()
  assert.equal(state.scans, 1)
  assert.equal(instance.data.scanMode, true)
  assert.equal(instance.data.rows[0].serial_no, 'SN-002')
  assert.match(state.calls[1].endpoint, /serial_no=SN-002$/)
  assert.equal(JSON.stringify(instance.data).includes('SN-002'), true)
  assert.equal(Object.prototype.hasOwnProperty.call(instance.data, 'scanValue'), false)
  assert.equal(state.calls.every(call => call.request.method === 'GET'), true)
})

test('late SN response after token rotation cannot restore rows', async () => {
  const pending = deferred()
  const { instance, state } = harness({ response: () => pending.promise })
  const loading = instance.onShow()
  await new Promise(resolve => setImmediate(resolve))
  state.token = 'session-2'
  pending.resolve(page())
  await loading
  assert.equal(instance.data.state, 'error')
  assert.equal(instance.data.rows.length, 0)
})
