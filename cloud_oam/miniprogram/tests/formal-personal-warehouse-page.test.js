const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const PERSON = '10000000-0000-4000-8000-000000000001'
const LOCATION = '20000000-0000-4000-8000-000000000001'
const OTHER = '30000000-0000-4000-8000-000000000001'
const clone = (value) => JSON.parse(JSON.stringify(value))
function user() { return { person_id: PERSON, name: '测试工程师', role_codes: ['technician'], authorization_version: 7 } }
function access() { return { person_id: PERSON, access_mode: 'active', role_codes: ['technician'], authorization_version: 7, permissions: [{ resource: 'inventory', action: 'read', field_code: '' }] } }
function account(index = 1) {
  return {
    stock_account_id: `40000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
    owner_org_id: OTHER, owner_org_code: 'OWNER', owner_org_name: '资产所属公司',
    location_owner_org_id: OTHER, location_owner_org_code: 'REGION', location_owner_org_name: '区域公司',
    location_id: LOCATION, location_code: 'PERSON-001', location_name: '测试个人仓', location_type: 'personal', location_parent_id: OTHER,
    custodian_person_id: PERSON, custodian_person_name: '测试工程师', material_id: OTHER,
    sku_code: `SKU-${index}`, material_name: '测试物料', base_unit: '个', tracking_mode: 'none',
    condition_code: 'new', availability_bucket: 'available', lot_id: null, lot_no: null,
    quantity_status: 'available', quantity: '12.345', balance_version: 1, ledger_cursor: 1
  }
}
function warehouse(items = [account()]) {
  return {
    schema_version: '1.0', projection_status: 'ready', opening_balance_status: 'established',
    ledger_cursor: 1, projected_at: '2026-09-12T08:00:00+08:00',
    person_id: PERSON, location_id: LOCATION, location_code: 'PERSON-001', location_name: '测试个人仓',
    location_status: 'active', custody_effective_from: '2026-09-01T08:00:00+08:00', items
  }
}
function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
function harness(options = {}) {
  const state = { user: user(), token: 'fixture-session', calls: [], savedUsers: [], stops: 0 }
  let identityReads = 0
  let accessReads = 0
  let definition
  const context = {
    Page(value) { definition = value },
    getApp: () => ({ setUser(value) { state.savedUsers.push(clone(value)); state.user = clone(value); return true } }),
    wx: { stopPullDownRefresh() { state.stops += 1 } },
    require(module) {
      if (module === '../../utils/session') return {
        getToken: () => state.token, getUser: () => state.user, ensureLogin: () => !!state.token
      }
      if (module === '../../utils/api') return {
        async get(endpoint) {
          state.calls.push({ endpoint, method: 'GET' })
          if (endpoint === '/auth/me') return options.identity ? options.identity(++identityReads, state) : user()
          if (endpoint === '/access/context') return options.access ? options.access(++accessReads, state) : access()
          throw new Error('Unexpected GET')
        },
        async request(endpoint, request) {
          state.calls.push({ endpoint, ...clone(request) })
          assert.equal(endpoint, '/v1/inventory/personal/me')
          assert.equal(request.method, 'GET')
          return options.warehouse ? options.warehouse(state) : warehouse()
        }
      }
      return require(path.resolve(__dirname, '../pages/formal-personal-warehouse', module))
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-personal-warehouse/index.js'), 'utf8'), context)
  const page = Object.assign({}, definition, { data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } })
  return { page, state }
}
const event = (value) => ({ detail: { value } })
const tick = () => new Promise((resolve) => setImmediate(resolve))

test('personal tab loads only current-person formal accounts with no-store and two identity checks', async () => {
  const { page, state } = harness()
  await page.onShow()
  assert.equal(page.data.state, 'ready')
  assert.equal(page.data.personName, '测试工程师')
  assert.equal(page.data.rows[0].quantity, '12.345')
  assert.equal(page.data.rows[0].owner, '资产所属公司')
  assert.equal(state.calls.length, 5)
  assert.deepEqual(state.calls[2], { endpoint: '/v1/inventory/personal/me', method: 'GET', header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
  assert.equal(JSON.stringify(page.data).includes(state.token), false)
  assert.equal(page.data.loading, false)
})

test('search and independent condition/status filters preserve decimal quantities and units', async () => {
  const a = account()
  a.quantity = '900719925474099.001'
  const b = Object.assign(account(2), { material_name: '旧电缆', condition_code: 'used', availability_bucket: 'frozen', base_unit: '米', tracking_mode: 'lot', lot_id: OTHER, lot_no: 'LOT-B' })
  const { page, state } = harness({ warehouse: () => warehouse([a, b]) })
  await page.onShow()
  assert.equal(page.data.rows[0].quantity, a.quantity)
  assert.equal(page.data.rows.length, 2)
  page.onSearch(event('lot-b'))
  assert.equal(page.data.rows[0].sku, 'SKU-2')
  assert.equal(page.data.rows[0].unit, '米')
  page.onConditionChange(event('1'))
  assert.equal(page.data.rows.length, 0)
  page.onConditionChange(event('2'))
  page.onStatusChange(event(String(page.data.statusLabels.indexOf('冻结'))))
  assert.equal(page.data.rows.length, 1)
  page.onStatusChange(event('1'))
  assert.equal(page.data.matchedCount, 0)
  page.onStatusChange(event('999'))
  assert.equal(page.data.statusIndex, 1)
  page.onStatusChange(event('-1'))
  assert.equal(page.data.statusIndex, 1)
  assert.equal(state.calls.length, 5, 'filtering never calls a write or broader inventory endpoint')
})

test('long inventories render in bounded batches without losing or summing rows', async () => {
  const { page } = harness({ warehouse: () => warehouse(Array.from({ length: 85 }, (_, i) => account(i + 1))) })
  await page.onShow()
  assert.equal(page.data.matchedCount, 85)
  assert.equal(page.data.rows.length, 40)
  page.loadMore()
  assert.equal(page.data.rows.length, 80)
  page.loadMore()
  assert.equal(page.data.rows.length, 85)
  assert.equal(page.data.hasMore, false)
  page.onSearch(event('SKU-85'))
  assert.equal(page.data.rows.length, 1)
  page.onSearch(event(''))
  assert.equal(page.data.rows.length, 40)
})

test('unconfigured, unopened and established-empty warehouses remain different states', async () => {
  const unopened = Object.assign(warehouse([]), { opening_balance_status: 'not_established' })
  const unconfigured = Object.assign(clone(unopened), { location_id: null, location_code: null, location_name: null, location_status: null, custody_effective_from: null })
  for (const [payload, expected] of [[unconfigured, 'unconfigured'], [unopened, 'unopened'], [warehouse([]), 'ready']]) {
    const { page } = harness({ warehouse: () => payload })
    await page.onShow()
    assert.equal(page.data.state, expected)
    assert.equal(page.data.rows.length, 0)
    assert.equal(page.data.openingEstablished, expected === 'ready')
  }
})

test('foreign person/location, malformed quantities and incomplete opening fail closed', async () => {
  const changes = [
    (w) => { w.person_id = OTHER },
    (w) => { w.items[0].location_id = OTHER },
    (w) => { w.items[0].custodian_person_id = OTHER },
    (w) => { w.items[0].quantity = 12.345 },
    (w) => { w.items[0].availability_bucket = 'signature_received' },
    (w) => { w.items.push(clone(w.items[0])) },
    (w) => { w.opening_balance_status = 'not_established' },
    (w) => { delete w.ledger_cursor }
  ]
  for (const change of changes) {
    const payload = warehouse()
    change(payload)
    const { page } = harness({ warehouse: () => payload })
    await page.onShow()
    assert.equal(page.data.state, 'error')
    assert.equal(page.data.rows.length, 0)
    assert.equal(page._allRows.length, 0)
  }
})

test('missing permissions and restricted handover never query inventory', async () => {
  for (const patch of [{ permissions: [] }, { access_mode: 'restricted_handover' }, { person_id: OTHER }, { authorization_version: 9 }]) {
    const { page, state } = harness({ access: () => Object.assign(access(), patch) })
    await page.onShow()
    assert.equal(page.data.state, 'error')
    assert.equal(state.calls.length, 2)
  }
})

test('revocation or authorization change while inventory is returning discards every row', async () => {
  for (const patch of [{ permissions: [] }, { access_mode: 'restricted_handover' }, { authorization_version: 8 }]) {
    const { page } = harness({ access: (n) => Object.assign(access(), n === 2 ? patch : {}) })
    await page.onShow()
    assert.equal(page.data.state, 'error')
    assert.equal(page.data.rows.length, 0)
    assert.equal(page._allRows.length, 0)
  }
})

test('hiding while inventory is in flight prevents late rows and subsequent identity requests', async () => {
  const pending = deferred()
  const { page, state } = harness({ warehouse: () => pending.promise })
  const loading = page.onShow()
  await tick()
  assert.equal(state.calls.length, 3)
  page.onHide()
  const hidden = clone(page.data)
  pending.resolve(warehouse())
  await loading
  assert.deepEqual(page.data, hidden)
  assert.equal(state.calls.length, 3)
})

test('hiding and unloading clear displayed and private inventory and search terms', async () => {
  for (const action of ['onHide', 'onUnload']) {
    const { page } = harness()
    await page.onShow()
    page.onSearch(event('SKU'))
    page[action]()
    assert.equal(page.data.rows.length, 0)
    assert.equal(page._allRows.length, 0)
    assert.equal(page._sessionCurrent, null)
    assert.equal(page.data.personName, '')
    assert.equal(page.data.query, '')
    page.onSearch(event('late hidden input'))
    page.onStatusChange(event('1'))
    assert.equal(page.data.query, '')
    assert.equal(page.data.statusIndex, 0)
  }
})

test('account switch during the first identity request cannot overwrite the new login', async () => {
  const pending = deferred()
  const { page, state } = harness({ identity: () => pending.promise })
  const loading = page.onShow()
  state.token = 'different-fixture-session'
  state.user = Object.assign(user(), { person_id: OTHER })
  pending.resolve(user())
  await loading
  assert.equal(state.user.person_id, OTHER)
  assert.equal(state.savedUsers.length, 0)
  assert.equal(state.calls.length, 2)
  assert.equal(page.data.state, 'error')
})

test('logout or token rotation during inventory read discards the old response', async () => {
  for (const token of ['', 'rotated-fixture-session']) {
    const pending = deferred()
    const { page, state } = harness({ warehouse: () => pending.promise })
    const loading = page.onShow()
    await tick()
    state.token = token
    pending.resolve(warehouse())
    await loading
    assert.equal(page.data.state, 'error')
    assert.equal(page.data.rows.length, 0)
    assert.equal(state.calls.length, 3)
  }
})

test('local session changes cannot reuse the loaded rows through filters', async () => {
  const { page, state } = harness()
  await page.onShow()
  state.user.authorization_version += 1
  page.onSearch(event('SKU'))
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.rows.length, 0)
  assert.equal(page._allRows.length, 0)
})

test('a second refresh wins over an older inventory response', async () => {
  const first = deferred()
  let reads = 0
  const { page } = harness({ warehouse: () => ++reads === 1 ? first.promise : warehouse([account(2)]) })
  const loading = page.onShow()
  await tick()
  await page.load()
  assert.equal(page.data.rows[0].sku, 'SKU-2')
  first.resolve(warehouse())
  await loading
  assert.equal(page.data.rows[0].sku, 'SKU-2')
})

test('failed refresh clears previous balances and stops pull-down loading', async () => {
  let failed = false
  const { page, state } = harness({ warehouse: () => { if (failed) throw new Error('fixture network failure'); return warehouse() } })
  await page.onShow()
  failed = true
  await page.onPullDownRefresh()
  assert.equal(page.data.rows.length, 0)
  assert.equal(page.data.openingEstablished, false)
  assert.equal(page.data.state, 'error')
  assert.equal(state.stops, 1)
})

test('unauthenticated entry makes no inventory request', async () => {
  const { page, state } = harness()
  state.token = ''
  await page.onShow()
  assert.equal(page.data.state, 'error')
  assert.equal(page.data.loading, false)
  assert.equal(state.calls.length, 0)
})
