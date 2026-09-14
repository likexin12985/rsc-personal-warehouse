const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { validate } = require('../utils/personal-warehouse-transactions-contract')

const PERSON = '10000000-0000-4000-8000-000000000001'
const LOCATION = '20000000-0000-4000-8000-000000000001'
const TX = '30000000-0000-4000-8000-000000000001'

function page(items = []) {
  return {
    schema_version: '1.0', projection_status: 'ready', opening_balance_status: 'established',
    projected_at: '2026-09-14T10:00:00+08:00', ledger_cursor: 9,
    person_id: PERSON, location_id: LOCATION, items, next_after_cursor: null
  }
}
function row(patch = {}) {
  return {
    transaction_id: TX, transaction_no: 'INV-0009', ledger_cursor: 9, movement_type: 'inbound', status: 'posted',
    source_document_type: 'material_request_inbound', source_document_id: 'REQ-1',
    effective_at: '2026-09-14T09:59:00+08:00', posted_at: '2026-09-14T10:00:00+08:00',
    changes: [{ movement_id: '50000000-0000-4000-8000-000000000001', line_no: 1, stock_account_id: '60000000-0000-4000-8000-000000000001',
      material_id: '70000000-0000-4000-8000-000000000001', sku_code: 'SKU-1', material_name: '测试物料', base_unit: '个',
      condition_code: 'new', availability_bucket: 'available', direction: 'in', quantity: '1.000', serial_count: 2 }], ...patch
  }
}

test('accepts redacted personal-warehouse transaction rows and keeps decimal strings', () => {
  const result = validate(page([row()]), { personId: PERSON, locationId: LOCATION })
  assert.equal(result.items[0].changes[0].quantity, '1.000')
  assert.equal(result.items[0].changes[0].serial_count, 2)
})

test('rejects a transaction row that exposes account coordinates or an unknown status', () => {
  assert.throws(() => validate(page([row({ status: 'pending' })]), { personId: PERSON }), /未通过核验|未知状态/)
  assert.throws(() => validate(page([row({ from_account_id: LOCATION })]), { personId: PERSON }), /字段不完整/)
  assert.throws(() => validate(page([row({ movement_type: 'signed' })]), { personId: PERSON }), /动作/)
})

test('rejects cursor order, opening and identity drift', () => {
  assert.throws(() => validate(page([row({ ledger_cursor: 10 })]), { personId: PERSON }), /游标/)
  assert.throws(() => validate({ ...page([row()]), opening_balance_status: 'not_established' }, { personId: PERSON }), /期初未建立/)
  assert.throws(() => validate(page(), { personId: '40000000-0000-4000-8000-000000000001' }), /人员/)
})

test('accepts a bounded older page only when the next cursor is the last row', () => {
  const older = { ...page([row({ transaction_id: '40000000-0000-4000-8000-000000000001', ledger_cursor: 7 })]), next_after_cursor: 7 }
  const result = validate(older, { personId: PERSON, afterCursor: 9 })
  assert.equal(result.next_after_cursor, 7)
  assert.throws(() => validate({ ...older, next_after_cursor: 6 }, { personId: PERSON, afterCursor: 9 }), /下一页游标/)
  assert.throws(() => validate(older, { personId: PERSON, afterCursor: 9, ledgerCursor: 8 }), /跨页账本/)
})

function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function harness(options = {}) {
  const state = { user: { person_id: PERSON, authorization_version: 7 }, token: 'session-1', calls: [] }
  let definition
  const context = {
    Page(value) { definition = value },
    wx: { stopPullDownRefresh() {} },
    require(module) {
      if (module === '../../utils/session') return { getToken: () => state.token, getUser: () => state.user, ensureLogin: () => !!state.token }
      if (module === '../../utils/material-request-adapter') return { formalMaterialRequestAdapter: {
        loadIdentityNoReplay: async () => ({ person_id: PERSON, authorization_version: 7 }),
        loadAccessNoReplay: async () => ({ person_id: PERSON, authorization_version: 7, can_read: true, can_read_material_catalog: true })
      } }
      if (module === '../../utils/api') return { async request(endpoint, request) {
        state.calls.push({ endpoint, request })
        return options.response ? options.response() : page([row()])
      } }
      return require(path.resolve(__dirname, '../pages/formal-personal-warehouse-transactions', module))
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-personal-warehouse-transactions/index.js'), 'utf8'), context)
  const instance = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)), setData(value) { Object.assign(this.data, JSON.parse(JSON.stringify(value))) } })
  return { instance, state }
}

test('page performs a no-store scoped read and renders a per-SKU personal change', async () => {
  const { instance, state } = harness()
  await instance.onShow()
  assert.equal(instance.data.state, 'ready')
  assert.equal(instance.data.rows[0].changes[0].directionLabel, '增加')
  assert.equal(instance.data.rows[0].changes[0].serialLabel, '2 个 SN')
  assert.equal(state.calls[0].endpoint, '/v1/inventory/personal/me/transactions?limit=20')
  assert.equal(state.calls[0].request.method, 'GET')
  assert.equal(state.calls[0].request.noRefresh, true)
  assert.equal(state.calls[0].request.header['Cache-Control'], 'no-store')
  assert.equal(state.calls[0].request.header.Pragma, 'no-cache')
})

test('page discards a late history response after token rotation', async () => {
  const pending = deferred(), { instance, state } = harness({ response: () => pending.promise })
  const loading = instance.onShow()
  await new Promise(resolve => setImmediate(resolve))
  state.token = 'session-2'
  pending.resolve(page([row()]))
  await loading
  assert.equal(instance.data.state, 'error')
  assert.equal(instance.data.rows.length, 0)
})
