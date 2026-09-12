const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const contract = require('../utils/work-order-query-contract')
const PERSON = '10000000-0000-4000-8000-000000000001'
const ORDER = '20000000-0000-4000-8000-000000000001'
const NEXT = '20000000-0000-4000-8000-000000000002'
const LOCATION = '30000000-0000-4000-8000-000000000001'
const ACCOUNT = '40000000-0000-4000-8000-000000000001'
const OTHER = '50000000-0000-4000-8000-000000000001'
const clone = value => JSON.parse(JSON.stringify(value))
function user() { return { person_id: PERSON, authorization_version: 7, role_codes: ['technician'] } }
function access() { return { ...user(), access_mode: 'active', permissions: [{ resource: 'inventory', action: 'read', field_code: '' }, { resource: 'work_order_material', action: 'read', field_code: '' }] } }
function order(id = ORDER) { return { work_order_id: id, work_order_no: 'WO-TEST', status: 'active', engineer_person_id: PERSON, organization_id: OTHER, source_system: 'starcharge_oam', source_external_id: 'OAM-TEST', source_version: 'wo-v2:test', source_updated_at: '2026-09-12T08:00:00Z', synced_at: '2026-09-12T08:00:00Z', freshness: 'fresh', can_operate: true } }
function list(id = ORDER, next = null) { return { schema_version: '1.0', person_id: PERSON, authorization_version: 7, queried_at: '2026-09-12T08:01:00Z', items: [order(id)], next_after_id: next } }
function options() {
  return { schema_version: '1.0', projection_status: 'ready', opening_balance_status: 'established', ledger_cursor: 2, projected_at: '2026-09-12T08:00:00Z', person_id: PERSON, authorization_version: 7, work_order: order(), location_id: LOCATION, location_code: 'PERSON-TEST', location_name: '测试个人仓', location_status: 'active', custody_effective_from: '2026-09-01T08:00:00Z', items: [{
    stock_account_id: ACCOUNT, owner_org_id: OTHER, owner_org_code: 'OWNER', owner_org_name: '资产组织', location_owner_org_id: OTHER, location_owner_org_code: 'REGION', location_owner_org_name: '区域公司', location_id: LOCATION, location_code: 'PERSON-TEST', location_name: '测试个人仓', location_type: 'personal', location_parent_id: OTHER, custodian_person_id: PERSON, custodian_person_name: '测试工程师', material_id: OTHER, sku_code: 'SKU-TEST', material_name: '测试物料', base_unit: '个', tracking_mode: 'none', condition_code: 'new', availability_bucket: 'reserved', lot_id: null, lot_no: null, quantity_status: 'available', quantity: '7.000', balance_version: 2, ledger_cursor: 2, selectable_quantity: '2.125', serials: [], allowed_actions: ['consume', 'release', 'replace']
    , release_target_stock_account_id: OTHER
  }] }
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const tick = () => new Promise(resolve => setImmediate(resolve))
function harness(settings = {}) {
  let definition, count = 0, opaque = 0
  const state = { user: user(), token: 'fixture-session', calls: [] }
  const context = { Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, scanCode: options => settings.scan(options), showModal: options => settings.confirm(options) }, require(name) {
    if (name === '../../utils/work-order-recovery-store' && settings.store) return { getStore: () => settings.store }
    if (name === '../../utils/session') return { getUser: () => state.user, getToken: () => state.token, ensureLogin: () => !!state.token }
    if (name === '../../utils/api') return {
      createRequestId() { return settings.randomFailure ? settings.randomFailure() : 'wxreq-' + (++opaque).toString(16).padStart(36, '0') },
      createIdempotencyKey() { return 'wxidem-' + (++opaque).toString(16).padStart(36, '0') },
      async postNoReplay(endpoint, data, options) {
        state.calls.push({ endpoint, method: 'POST', data: clone(data), ...clone(options) }); return settings.post(endpoint, data, options, state)
      }, async postSealNoReplay(endpoint, data, options) {
      state.calls.push({endpoint,method:'POST',data,...clone(options)}); return settings.seal(data, state)
    }, async request(endpoint, request) {
      state.calls.push({ endpoint, ...clone(request) })
      if (endpoint === '/auth/me') return settings.identity ? settings.identity(state) : user()
      if (endpoint === '/access/context') return settings.access ? settings.access(++count) : access()
      if (endpoint.includes('/by-request/')) return settings.recovery(state)
      if (endpoint.endsWith('/removed-part')) return settings.removed(request.data, state)
      if (endpoint.endsWith('/preview')) return settings.preview(endpoint, request.data, state)
      if (endpoint.endsWith('/material-options')) return settings.options ? settings.options(state) : options()
      if (endpoint.endsWith('/material-completion-check')) return settings.completion(state)
      return settings.list ? settings.list(endpoint, state) : list()
    } }
    return require(path.resolve(__dirname, '../pages/formal-work-orders', name))
  } }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-work-orders/index.js'), 'utf8'), context)
  const page = Object.assign({}, definition, { data: clone(definition.data), setData(update) { Object.assign(this.data, clone(update)) } })
  return { page, state }
}

test('work-order contract rejects foreign identities, bad source, missing versions and unordered pages', () => {
  assert.equal(contract.validateMyWorkOrders(list(), PERSON, 7).items[0].work_order_id, ORDER)
  for (const change of [r => { r.person_id = OTHER }, r => { r.authorization_version = 8 }, r => { r.items[0].engineer_person_id = OTHER }, r => { r.items[0].source_system = 'prototype' }, r => { delete r.items[0].source_version }, r => { r.items[0].freshness = 'stale' }, r => { r.items[0].status = 'closed' }, r => { r.items.push(order()) }, r => { r.next_after_id = NEXT }, r => { r.queried_at = 'bad' }]) {
    const r = list(); change(r); assert.throws(() => contract.validateMyWorkOrders(r, PERSON, 7))
  }
  assert.throws(() => contract.validateMyWorkOrders(list(), PERSON, 7, ORDER))
})
test('options display exact own reservation quantity and do not round large decimals', () => {
  const r = options(); r.items[0].quantity = '900719925474099.999'; r.items[0].selectable_quantity = '900719925474099.998'
  const output = contract.validateMaterialOptions(r, PERSON, 7, ORDER)
  assert.equal(output.items[0].selectable_quantity, '900719925474099.998')
  assert.equal(output.items[0].quantityLabel, '本工单剩余占用')
  assert.equal(output.items[0].release_target_stock_account_id, OTHER)
  const missing = options(); delete missing.items[0].release_target_stock_account_id
  assert.throws(() => contract.validateMaterialOptions(missing, PERSON, 7, ORDER))
  const crossed = options(); crossed.items[0].release_target_stock_account_id = ACCOUNT
  assert.throws(() => contract.validateMaterialOptions(crossed, PERSON, 7, ORDER))
})
test('options reject crossed account scope, missing opening, excess quantities, wrong SN and actions', () => {
  for (const change of [r => { r.items[0].custodian_person_id = OTHER }, r => { r.items[0].location_id = OTHER }, r => { r.work_order.work_order_id = NEXT }, r => { r.opening_balance_status = 'not_established' }, r => { r.items[0].selectable_quantity = '7.001' }, r => { r.items[0].selectable_quantity = 2.125 }, r => { r.items[0].tracking_mode = 'serial' }, r => { r.items[0].serials = [{ serial_id: OTHER, serial_no: 'SN' }] }, r => { r.items[0].allowed_actions = ['occupy'] }, r => { r.items[0].availability_bucket = 'frozen' }, r => { r.items.push(clone(r.items[0])) }]) {
    const r = options(); change(r); assert.throws(() => contract.validateMaterialOptions(r, PERSON, 7, ORDER))
  }
  const r = options(); r.items[0].tracking_mode = 'serial'; r.items[0].selectable_quantity = '1.000'; r.items[0].serials = [{ serial_id: OTHER, serial_no: 'SN-1' }]
  assert.equal(contract.validateMaterialOptions(r, PERSON, 7, ORDER).items[0].serials.length, 1)
  r.items[0].serials[0].qr_code = 'PREFILLED'; assert.throws(() => contract.validateMaterialOptions(r, PERSON, 7, ORDER))
})
test('stale and closed orders remain visible with no allowed actions', () => {
  const r = options(); Object.assign(r.work_order, { can_operate: false, status: 'closed', freshness: 'stale' }); r.items[0].allowed_actions = []
  assert.equal(contract.validateMaterialOptions(r, PERSON, 7, ORDER).workOrder.freshnessLabel, '同步已过期')
})
test('page selects formal own order and shows own quantity with only no-store GET requests', async () => {
  const { page, state } = harness(); await page.onShow()
  assert.equal(page.data.state, 'ready'); assert.equal(page.data.orders.length, 1)
  await page.openOrder({ currentTarget: { dataset: { id: ORDER } } })
  assert.equal(page.data.workOrder.work_order_id, ORDER)
  assert.equal(page.data.items[0].selectable_quantity, '2.125')
  assert.equal(page.data.items[0].quantityLabel, '本工单剩余占用')
  assert.equal(state.calls.length, 10)
  for (const call of state.calls) { assert.equal(call.method, 'GET'); assert.equal(call.noRefresh, true); assert.equal(call.header['Cache-Control'], 'no-store') }
  assert.equal(JSON.stringify(page.data).includes(state.token), false)
})
test('pagination and search use returned cursors and encode literal input', async () => {
  const { page, state } = harness({ list: endpoint => endpoint.includes('after_id=') ? list(NEXT) : list(ORDER, ORDER) })
  await page.onShow(); await page.nextPage()
  assert.equal(page.data.orders[0].work_order_id, NEXT)
  assert.equal(page.data.pageNumber, 2)
  await page.previousPage(); assert.equal(page.data.orders[0].work_order_id, ORDER)
  page.onSearchInput({ detail: { value: '%/_&' } }); await page.search()
  const query = state.calls.filter(row => row.endpoint.includes('/mine')).at(-1).endpoint
  assert.ok(query.includes('search=%25%2F_%26')); assert.ok(!query.includes('after_id='))
})
test('permission revocation and account switch discard the response', async () => {
  const revoked = harness({ access: n => { const r = access(); if (n === 2) r.permissions = []; return r } })
  await revoked.page.onShow(); assert.equal(revoked.page.data.state, 'error'); assert.equal(revoked.page.data.orders.length, 0)
  const switched = harness({ list: (_, state) => { state.token = 'different-session'; return list() } })
  await switched.page.onShow(); assert.equal(switched.page.data.state, 'error'); assert.equal(switched.page.data.orders.length, 0)
})
test('hidden pages discard pending responses and never launch a new dependent request', async () => {
  for (const phase of ['identity', 'list', 'options']) {
    const pending = deferred()
    const { page, state } = harness({ [phase]: () => pending.promise })
    let active
    if (phase === 'options') { await page.onShow(); active = page.openOrder({ currentTarget: { dataset: { id: ORDER } } }) }
    else active = page.onShow()
    await tick(); page.onHide(); const count = state.calls.length
    pending.resolve(phase === 'identity' ? user() : phase === 'list' ? list() : options())
    await active
    assert.equal(page.data.state, 'idle'); assert.equal(page.data.orders.length, 0); assert.equal(page.data.items.length, 0)
    assert.equal(state.calls.length, count)
  }
})

test('work-order page restores pending anchors and only reads the original result', async () => {
  const { store, marker } = await pendingStore()
  const { page, state } = harness({ store, recovery: () => recovered(marker) })
  await page.onShow(); await page.openOrder({ currentTarget: { dataset: { id: ORDER } } })
  assert.equal(page.data.canRecover, true)
  await page.recover()
  assert.equal(page.data.canRecover, false); assert.match(page.data.recoveryMessage, /原操作已确认/)
  assert.equal(store.read(marker).kind, 'missing')
  assert.equal(state.calls.filter(row => row.endpoint.includes('/by-request/')).length, 1)
  assert.ok(state.calls.every(row => row.method === 'GET'))
})

test('hiding during original-result lookup keeps its marker and prevents later dependent reads', async () => {
  const { store, marker } = await pendingStore(), pending = deferred()
  const { page, state } = harness({ store, recovery: () => pending.promise })
  await page.onShow(); await page.openOrder({ currentTarget: { dataset: { id: ORDER } } })
  const work = page.recover(); await tick()
  page.onHide(); const count = state.calls.length
  pending.resolve(recovered(marker)); await work
  assert.equal(state.calls.length, count); assert.equal(store.read(marker).kind, 'valid')
  assert.equal(page.data.state, 'idle')
})

test('pending inbox recovers a reassigned order absent from the current own list', async () => {
  const { store, marker } = await pendingStore()
  const { page, state } = harness({ store, list: () => list(NEXT), recovery: () => recovered(marker) })
  await page.onShow()
  assert.equal(page.data.orders[0].work_order_id, NEXT)
  assert.equal(page.data.pendingRequests.length, 1)
  assert.equal(page.data.workOrder, null)
  await page.recoverPendingRequest({ currentTarget: { dataset: { id: ORDER } } })
  assert.equal(store.read(marker).kind, 'missing')
  assert.equal(page.data.pendingRequests.length, 0)
  assert.match(page.data.recoveryMessage, /原操作已确认/)
  assert.equal(state.calls.some(row => row.endpoint.includes('/material-options')), false)
  assert.ok(state.calls.every(row => row.method === 'GET' && row.noRefresh))
  assert.equal(JSON.stringify(page.data).includes(marker.request_hash), false)
})

test('pending recovery remains reachable when current list or selected options are unavailable', async () => {
  for (const mode of ['list', 'options', 'malformed']) {
    const { store, marker } = await pendingStore()
    const { page, state } = harness({ store, list: () => {
      if (mode === 'list') throw new Error('temporary read failure')
      return mode === 'malformed' ? {} : list()
    }, options: () => { throw new Error('not own current order') }, recovery: () => recovered(marker) })
    await page.onShow()
    if (mode === 'options') await page.openOrder({ currentTarget: { dataset: { id: ORDER } } })
    assert.equal(page.data.state, 'error')
    assert.equal(page.data.pendingRequests.length, 1)
    await page.recoverPendingRequest({ currentTarget: { dataset: { id: ORDER } } })
    assert.equal(store.read(marker).kind, 'missing')
    assert.equal(state.calls.filter(row => row.endpoint.includes('/by-request/')).length, 1)
  }
})

test('pending inbox never reads another person or an invented event coordinate', async () => {
  const { store, marker } = await pendingStore()
  const foreign = { ...marker, work_order_id: NEXT, person_id: OTHER }
  await store.withLease(foreign, lease => lease.persist(foreign))
  const { page, state } = harness({ store })
  await page.onShow()
  assert.equal(page.data.pendingRequests.length, 1)
  await page.recoverPendingRequest({ currentTarget: { dataset: { id: NEXT } } })
  await page.recoverPendingRequest({ currentTarget: { dataset: { id: ACCOUNT } } })
  assert.equal(state.calls.some(row => row.endpoint.includes('/by-request/')), false)
  assert.equal(store.read(foreign).kind, 'valid')
})

test('pending inbox clears on access revocation or account switch while keeping recovery storage', async () => {
  for (const mode of ['revoked', 'account', 'malformed_identity']) {
    const { store, marker } = await pendingStore()
    let revoke = false
    const { page, state } = harness({ store, access: () => {
      const result = access(); if (revoke) result.permissions = []; return result
    }, recovery: () => {
      if (mode === 'account') state.token = 'new-session'
      else if (mode === 'malformed_identity') state.user = { person_id: 'invalid' }
      else revoke = true
      return recovered(marker)
    } })
    await page.onShow()
    await page.recoverPendingRequest({ currentTarget: { dataset: { id: ORDER } } })
    assert.equal(page.data.pendingRequests.length, 0)
    assert.equal(store.read(marker).kind, 'valid')
    assert.equal(state.calls.filter(row => row.endpoint.includes('/by-request/')).length, 1)
  }
})

test('pending inbox preserves uncertain requests without displaying confirmation', async () => {
  for (const mode of ['not_observed', 'timeout', 'hash']) {
    const { store, marker } = await pendingStore()
    const { page, state } = harness({ store, recovery: () => {
      if (mode === 'timeout') throw new Error('timeout')
      if (mode === 'not_observed') return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
      const response = recovered(marker); response.command.request_hash = '0'.repeat(64); return response
    } })
    await page.onShow()
    await page.recoverPendingRequest({ currentTarget: { dataset: { id: ORDER } } })
    assert.equal(page.data.pendingRequests.length, 1)
    assert.equal(store.read(marker).kind, 'valid')
    assert.doesNotMatch(page.data.recoveryMessage, /原操作已确认/)
    assert.ok(state.calls.every(row => row.method === 'GET'))
  }
})

test('global recovery ignores late response after hide and serializes double taps', async () => {
  const { store, marker } = await pendingStore(), pending = deferred()
  const { page, state } = harness({ store, recovery: () => pending.promise })
  await page.onShow()
  const click = { currentTarget: { dataset: { id: ORDER } } }
  const work = page.recoverPendingRequest(click); await tick()
  await page.recoverPendingRequest(click)
  assert.equal(state.calls.filter(row => row.endpoint.includes('/by-request/')).length, 1)
  page.onHide(); const count = state.calls.length
  pending.resolve(recovered(marker)); await work
  assert.equal(state.calls.length, count)
  assert.equal(page.data.pendingRequests.length, 0)
  assert.equal(store.read(marker).kind, 'valid')
})

async function pendingStore(pending = true) {
  const records = new Map()
  const module = require('../utils/work-order-recovery-store')
  const storage = {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.has(key) ? records.get(key) : '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
  }
  const store = module.createStore({ state: { active: new Set(), faults: new Set() }, storage })
  const marker = module.validateMarker({ v: 1, kind: 'work_order_material', work_order_id: ORDER, person_id: PERSON, authorization_version: 7,
    operation_type: 'consume', trace_request_id: 'wxreq-' + 'c'.repeat(36), request_hash: 'd'.repeat(64) })
  if (pending) await store.withLease(marker, lease => lease.persist(marker))
  return { store, marker, storage, records }
}
async function pairedPendingStore() {
  const value = await pendingStore(false)
  value.marker = { ...value.marker, kind: 'work_order_replacement', operation_type: 'replace' }
  await value.store.withLease(value.marker, lease => lease.persist(value.marker))
  return value
}
function pairedRecovered(marker) {
  return { schema_version: '1.0', replacement_id: OTHER, replacement_no: 'WR-TEST', work_order_id: marker.work_order_id,
    consume_operation_id: ORDER, recover_operation_id: NEXT, consume_transaction_id: ACCOUNT, recover_transaction_id: LOCATION,
    status: 'posted', operator_person_id: marker.person_id, request_id: marker.trace_request_id, request_hash: marker.request_hash }
}
test('paired original request is visible outside current orders and only its parent can confirm recovery', async () => {
  const { store, marker } = await pairedPendingStore()
  const { page, state } = harness({ store, list: () => list(NEXT), recovery: () => pairedRecovered(marker),
    access: () => ({ ...access(), permissions: access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }) })
  await page.onShow()
  assert.match(page.data.pendingRequests[0].label, /成对消耗与回收/)
  assert.equal(page.data.pendingRequests[0].sealable, true)
  await page.recoverPendingRequest(event(ORDER))
  assert.equal(store.read(marker).kind, 'missing'); assert.match(page.data.recoveryMessage, /WR-TEST/)
  assert.equal(page.data.pendingRequests.length, 0)
  assert.ok(state.calls.every(call => call.method === 'GET'))
  assert.equal(state.calls.find(call => call.endpoint.includes('/by-request/')).endpoint,
    `/v1/work-orders/${ORDER}/material-replacements/by-request/${marker.trace_request_id}`)
})
test('pending paired request disables ordinary drafts and retains evidence on unknown, child or hidden responses', async () => {
  for (const mode of ['unknown', 'child', 'hidden']) {
    const { store, marker } = await pairedPendingStore(), waiting = deferred()
    const { page, state } = await openDraft({ store, recovery: () => {
      if (mode === 'hidden') return waiting.promise
      if (mode === 'child') return recovered({ ...marker, operation_type: 'consume' })
      throw new Error('404')
    } })
    assert.equal(page.data.canDraft, false)
    const work = page.recoverPendingRequest(event(ORDER))
    if (mode === 'hidden') { await tick(); page.onHide(); waiting.resolve(pairedRecovered(marker)) }
    await work
    assert.equal(store.read(marker).kind, 'valid')
    assert.doesNotMatch(page.data.recoveryMessage, /原操作已确认/)
    assert.ok(state.calls.every(call => call.method === 'GET'))
  }
})
test('page confirms only the parent seal and clears the marker after a matching GET', async () => {
  const { store, marker } = await pairedPendingStore(); let posted = false, confirmations = 0
  const sealed = { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: {
    seal_id: OTHER, work_order_id: ORDER, operator_person_id: PERSON, operation_type: 'replace', request_id: marker.trace_request_id,
    request_hash: marker.request_hash, sealed_at: '2026-09-13T00:00:00Z' } }
  const { page, state } = await openDraft({ store, recovery: () => {
    if (!posted) throw Object.assign(new Error('not observed'), { status: 404, responseReceived: true, code: 'replacement_not_found' })
    return sealed
  }, seal: () => { posted = true; return sealed }, confirm: options => { confirmations++; options.success({ confirm: true }) } })
  await page.sealPendingRequest(event(ORDER))
  assert.equal(confirmations, 1); assert.equal(store.read(marker).kind, 'missing')
  assert.match(page.data.recoveryMessage, /已关闭且未执行/)
  const posts = state.calls.filter(call => call.method === 'POST')
  assert.equal(posts.length, 1); assert.equal(posts[0].endpoint,
    `/v1/work-orders/${ORDER}/material-replacements/by-request/${marker.trace_request_id}/seal`)
})
function recovered(marker) {
  return { schema_version: '1.0', lookup_status: 'confirmed', command: { schema_version: '1.0', work_order_id: ORDER, operator_person_id: PERSON,
    operation_type: marker.operation_type, request_id: marker.trace_request_id, request_hash: marker.request_hash, status: 'posted',
    operation_id: OTHER, posting_transaction_id: ACCOUNT, operation_no: 'WOM-TEST', posted_at: '2026-09-12T08:00:00Z' } }
}

const event = (id, rest = {}) => ({ currentTarget: { dataset: { id, ...rest } } })
async function openDraft(settings = {}) {
  const { store } = await pendingStore(false)
  const result = harness({ store, access: () => ({ ...access(), permissions: access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }), ...settings })
  await result.page.onShow(); await result.page.openOrder(event(ORDER))
  return { ...result, store }
}
function previewResult(kind, body) {
  return { schema_version: '1.0', status: 'batch_validated', work_order_id: ORDER, operator_person_id: PERSON,
    authorization_version: 7, operation_type: kind, source_version: order().source_version, ledger_cursor: 2,
    checked_at: '2026-09-12T08:01:00Z', line_count: body.lines.length,
    request_hash: require('../utils/work-order-command').requestHash(kind, ORDER, PERSON, body.lines) }
}
test('page previews a quantity batch without posting stock or persisting the command', async () => {
  const { page, state, store } = await openDraft({ preview: (_, body) => previewResult('release', body) })
  assert.equal(page.data.canDraft, true)
  page.chooseOperation(event(null, { kind: 'release' })); page.addMaterial(event(ACCOUNT))
  page.editQuantity({ ...event(ACCOUNT), detail: { value: '1.125' } })
  await page.previewMaterials()
  assert.match(page.data.previewMessage, /预检通过/); assert.match(page.data.previewMessage, /库存尚未变动/)
  const posts = state.calls.filter(row => row.method === 'POST')
  assert.equal(posts.length, 1); assert.ok(posts[0].endpoint.endsWith('/release/preview'))
  assert.equal(posts[0].data.lines[0].target_stock_account_id, OTHER)
  assert.equal('idempotency_key' in posts[0].data, false)
  assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  page.editQuantity({ ...event(ACCOUNT), detail: { value: '1' } })
  assert.equal(page.data.previewMessage, '')
  page.chooseOperation(event(null, { kind: 'consume' }))
  assert.equal(page.data.draftRows.length, 0)
})
test('physical SKU SN and QR scans are collected separately and cleared on hide', async () => {
  const inputs = ['SKU-TEST', 'SN-PHYSICAL', 'QR-PHYSICAL-PRIVATE']
  const { page, state } = await openDraft({ scan(opts) { assert.equal(opts.onlyFromCamera, true); opts.success({ result: inputs.shift() }) }, options: () => {
    const raw = options(), item = raw.items[0]
    Object.assign(item, { tracking_mode: 'serial', selectable_quantity: '1.000', serials: [{ serial_id: OTHER, serial_no: 'SN-PHYSICAL' }] })
    return raw
  } })
  page.chooseOperation(event(null, { kind: 'consume' })); page.addMaterial(event(ACCOUNT))
  assert.equal(page.data.draftRows[0].serials.length, 0)
  for (const code of ['sku_code', 'serial_no', 'qr_code']) await page.scanMaterialCode(event(ACCOUNT, { code }))
  assert.equal(page.data.draftRows[0].serials.length, 0)
  page.collectScanned(event(ACCOUNT))
  assert.equal(page.data.draftRows[0].serials.length, 1)
  assert.equal(JSON.stringify(page.data).includes('QR-PHYSICAL-PRIVATE'), false)
  assert.ok(state.calls.every(row => row.method === 'GET'))
  page.onHide()
  assert.equal(Object.keys(page._drafts).length, 0); assert.equal(Object.keys(page._scans).length, 0)
  assert.equal(page.data.draftRows.length, 0)
})
test('pending original commands block new drafts and unknown stock never passes preview', async () => {
  const { store } = await pendingStore()
  const waiting = await openDraft({ store })
  assert.equal(waiting.page.data.canDraft, false)
  waiting.page.addMaterial(event(ACCOUNT)); assert.equal(waiting.page.data.draftRows.length, 0)
  let reads = 0
  const fresh = await openDraft({ options: () => { const raw = options(); if (++reads > 1) raw.items[0].selectable_quantity = '0.500'; return raw } })
  fresh.page.chooseOperation(event(null, { kind: 'consume' })); fresh.page.addMaterial(event(ACCOUNT))
  fresh.page.editQuantity({ ...event(ACCOUNT), detail: { value: '1' } })
  await fresh.page.previewMaterials()
  assert.equal(fresh.state.calls.some(row => row.method === 'POST'), false)
  assert.match(fresh.page.data.previewMessage, /不能超过/)
})
test('hide and account switch discard pending preview and never start dependent reads', async () => {
  for (const mode of ['hide', 'account']) {
    const pending = deferred(); let body
    const { page, state } = await openDraft({ preview: (_, value) => { body = value; return pending.promise } })
    page.chooseOperation(event(null, { kind: 'consume' })); page.addMaterial(event(ACCOUNT))
    page.editQuantity({ ...event(ACCOUNT), detail: { value: '1' } })
    const work = page.previewMaterials(); await tick()
    const count = state.calls.length
    if (mode === 'hide') page.onHide(); else state.token = 'other-session'
    pending.resolve(previewResult('consume', body)); await work
    assert.equal(state.calls.length, count); assert.equal(page.data.draftRows.length, 0)
    assert.equal(page.data.previewMessage, ''); assert.equal(Object.keys(page._drafts).length, 0)
  }
})
test('read permission alone cannot expose the draft editor and late camera callbacks are discarded', async () => {
  const readonly = await openDraft({ access })
  assert.equal(readonly.page.data.canDraft, false)
  readonly.page.addMaterial(event(ACCOUNT)); assert.equal(readonly.page.data.draftRows.length, 0)
  let camera
  const { page } = await openDraft({ scan: opts => { camera = opts }, options: () => {
    const raw = options(); Object.assign(raw.items[0], { tracking_mode: 'serial', selectable_quantity: '1.000', serials: [{ serial_id: OTHER, serial_no: 'SN-PHYSICAL' }] }); return raw
  } })
  page.chooseOperation(event(null, { kind: 'consume' })); page.addMaterial(event(ACCOUNT))
  const scan = page.scanMaterialCode(event(ACCOUNT, { code: 'qr_code' }))
  page.onHide(); camera.success({ result: 'late-private-qr' }); await scan
  assert.equal(Object.keys(page._scans).length, 0); assert.equal(Object.keys(page._drafts).length, 0)
  assert.equal(JSON.stringify(page.data).includes('late-private-qr'), false)
})

test('seal action is permission gated and only closes after original GET proof', async () => {
  const { store, marker } = await pendingStore(); let sealed = false, prompts = 0
  const result = {schema_version:'1.0',lookup_status:'sealed_not_executed',command:null,seal:{
    seal_id:OTHER,work_order_id:ORDER,operator_person_id:PERSON,operation_type:marker.operation_type,
    request_id:marker.trace_request_id,request_hash:marker.request_hash,sealed_at:'2026-09-12T08:00:00Z'
  }}
  const { page, state } = harness({store,access:()=>({...access(),permissions:access().permissions.concat({resource:'work_order_material',action:'operate',field_code:''})}),
    recovery:()=>sealed?result:{schema_version:'1.0',lookup_status:'not_observed',command:null},
    seal:()=>{sealed=true;return result},confirm:options=>{prompts++;options.success({confirm:true})}})
  await page.onShow(); assert.equal(page.data.canSeal,true)
  await page.sealPendingRequest(event(ORDER))
  assert.equal(prompts,1); assert.equal(state.calls.filter(row=>row.method==='POST').length,1)
  assert.equal(page.data.pendingRequests.length,0); assert.match(page.data.recoveryMessage,/已关闭且未执行/)
  assert.equal(store.read(marker).kind,'missing')
  const readonly = harness({store}); await readonly.page.onShow()
  assert.equal(readonly.page.data.canSeal,false)
  await readonly.page.sealPendingRequest(event(ORDER))
  assert.ok(readonly.state.calls.every(row=>row.method==='GET'))
})

test('leaving during seal confirmation stops the write and preserves original recovery', async () => {
  const { store, marker } = await pendingStore(); let modal
  const { page, state } = harness({store,access:()=>({...access(),permissions:access().permissions.concat({resource:'work_order_material',action:'operate',field_code:''})}),
    recovery:()=>({schema_version:'1.0',lookup_status:'not_observed',command:null}),confirm:options=>{modal=options}})
  await page.onShow(); const work=page.sealPendingRequest(event(ORDER)); await tick()
  page.onHide(); const count=state.calls.length
  modal.success({confirm:true}); await work
  assert.equal(state.calls.length,count); assert.ok(state.calls.every(row=>row.method==='GET'))
  assert.equal(store.read(marker).kind,'valid'); assert.equal(page.data.pendingRequests.length,0)
})

function prepareQuantity(page, kind = 'consume') {
  page.chooseOperation(event(null, { kind })); page.addMaterial(event(ACCOUNT))
  page.editQuantity({ ...event(ACCOUNT), detail: { value: '1.125' } })
}

test('confirmed quantity submission persists and reads back its marker before one POST and proves original result', async () => {
  for (const kind of ['occupy', 'consume', 'release']) {
    const { store, records } = await pendingStore(false)
    let submitted
    const { page, state } = await openDraft({ store, options: () => {
      const raw = options()
      if (kind === 'occupy') Object.assign(raw.items[0], { availability_bucket: 'available', selectable_quantity: raw.items[0].quantity, allowed_actions: ['occupy'], release_target_stock_account_id: null })
      return raw
    }, preview: (_, body) => previewResult(kind, body), post: (endpoint, body, settings) => {
      const stored = store.read({ work_order_id: ORDER }); assert.equal(stored.kind, 'valid')
      submitted = stored.value
      assert.equal(body.request_id, submitted.trace_request_id); assert.equal(settings.requestId, body.request_id)
      assert.equal(settings.idempotencyKey, body.idempotency_key)
      assert.equal(submitted.request_hash, require('../utils/work-order-command').requestHash(kind, ORDER, PERSON, body.lines))
      assert.equal(body.lines[0].quantity, '1.125')
      if (kind === 'release') assert.equal(body.lines[0].target_stock_account_id, OTHER)
      else assert.equal('target_stock_account_id' in body.lines[0], false)
      const persisted = [...records.values()].join()
      for (const value of ['quantity', 'material_id', 'idempotency_key', 'fixture-session']) assert.equal(persisted.includes(value), false)
      return { ignored: 'Only the subsequent original GET is proof' }
    }, recovery: () => recovered(submitted) })
    prepareQuantity(page, kind)
    const work = page.submitMaterials(); await tick()
    assert.equal(page.data.confirming, true); assert.equal(page.data.reviewWorkOrderNo, 'WO-TEST')
    assert.equal(page.data.reviewRows[0].quantity, '1.125'); assert.equal(page.data.reviewRows[0].sku, 'SKU-TEST')
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 0)
    await page.submitMaterials() // Double tap during review cannot launch a second workflow.
    page.confirmSubmission(); page.confirmSubmission(); await work
    assert.equal(page.data.canDraft, false); assert.equal(page.data.confirming, false)
    assert.match(page.data.recoveryMessage, /已确认/); assert.equal(page.data.pendingRequests.length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 1)
    assert.equal(state.calls.filter(row => row.endpoint.includes('/by-request/')).length, 1)
    assert.equal(Object.keys(page._drafts).length, 0)
  }
})

test('cancel or hide during complete batch review never persists or posts stock', async () => {
  for (const mode of ['cancel', 'hide', 'account']) {
    const { page, state, store } = await openDraft({ preview: (_, body) => previewResult('consume', body) })
    prepareQuantity(page)
    const work = page.submitMaterials(); await tick()
    assert.equal(page.data.confirming, true)
    if (mode === 'cancel') page.cancelSubmission()
    else if (mode === 'hide') page.onHide()
    else { state.token = 'new-account'; page.confirmSubmission() }
    await work
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 0)
    assert.equal(page.data.confirming, false)
    if (mode === 'cancel') assert.equal(page.data.draftRows.length, 1)
    else assert.equal(page.data.reviewRows.length, 0)
  }
})

test('unknown POST outcomes and unproven original results keep one recovery marker and prohibit resubmission', async () => {
  for (const mode of ['post_timeout', 'post_401', 'not_observed', 'get_timeout', 'digest']) {
    const { store } = await pendingStore(false); let submitted
    const { page, state } = await openDraft({ store, preview: (_, body) => previewResult('consume', body), post: () => {
      submitted = store.read({ work_order_id: ORDER }).value
      if (mode.startsWith('post_')) throw Object.assign(new Error('outcome unknown'), { status: mode === 'post_401' ? 401 : 0 })
      return {}
    }, recovery: () => {
      if (mode === 'not_observed') return { schema_version:'1.0', lookup_status:'not_observed', command:null }
      if (mode === 'get_timeout') throw new Error('lookup timeout')
      const raw = recovered(submitted); raw.command.request_hash = '0'.repeat(64); return raw
    } })
    prepareQuantity(page)
    const work = page.submitMaterials(); await tick(); page.confirmSubmission(); await work
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid')
    assert.equal(page.data.canDraft, false); assert.equal(page.data.canRecover, true)
    assert.equal(page.data.pendingRequests.length, 1); assert.doesNotMatch(page.data.recoveryMessage, /已确认/)
    await page.submitMaterials()
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 1)
  }
})

test('failed marker readback and missing secure entropy stop before stock POST', async () => {
  for (const mode of ['storage', 'entropy']) {
    const { store, storage } = await pendingStore(false)
    const { page, state } = await openDraft({ store, preview: (_, body) => previewResult('consume', body),
      randomFailure: mode === 'entropy' ? () => { throw new Error('secure randomness unavailable') } : null })
    prepareQuantity(page)
    const work = page.submitMaterials(); await tick()
    if (mode === 'storage') storage.setStorageSync = () => {}
    page.confirmSubmission(); await work
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, mode === 'storage' ? 'unavailable' : 'missing')
  }
})

test('changed stock, preview contents or access stop the batch before review and marker creation', async () => {
  for (const mode of ['quantity', 'preview', 'permission']) {
    let reads = 0, revoke = false
    const { page, state, store } = await openDraft({ options: () => { const raw = options(); if (++reads > 1 && mode === 'quantity') raw.items[0].selectable_quantity = '0.500'; return raw },
      preview: (_, body) => { const raw = previewResult('consume', body); if (mode === 'preview') raw.request_hash = '0'.repeat(64); if (mode === 'permission') revoke = true; return raw },
      access: () => ({ ...access(), permissions: revoke ? [] : access().permissions.concat({resource:'work_order_material',action:'operate',field_code:''}) }) })
    prepareQuantity(page); await page.submitMaterials()
    assert.equal(page.data.confirming, false); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    assert.equal(state.calls.filter(row => row.method === 'POST' && !row.endpoint.endsWith('/preview')).length, 0)
  }
})

test('hidden page during stock POST leaves durable recovery and starts no late dependent reads', async () => {
  const pending = deferred()
  const { page, state, store } = await openDraft({ preview: (_, body) => previewResult('consume', body), post: () => pending.promise })
  prepareQuantity(page); const work = page.submitMaterials(); await tick(); page.confirmSubmission(); await tick()
  assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid')
  page.onHide(); const count = state.calls.length
  pending.resolve({}); await work
  assert.equal(state.calls.length, count); assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid')
  assert.equal(page.data.reviewRows.length, 0); assert.equal(page.data.draftRows.length, 0)
})

test('operate-only permission revocation before submission or sealing prevents even the confirmation step', async () => {
  for (const seal of [false, true]) {
    let revoked = false
    const { store, marker } = await pendingStore(seal)
    const { page, state } = await openDraft({ store, access: () => ({ ...access(), permissions: revoked ? access().permissions :
      access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }) })
    if (!seal) prepareQuantity(page)
    revoked = true
    const count = state.calls.length
    if (seal) await page.sealPendingRequest(event(ORDER)); else await page.submitMaterials()
    assert.equal(page.data.confirming, false)
    assert.equal(page.data.state, 'error')
    assert.equal(state.calls.slice(count).some(row => row.method === 'POST' || row.endpoint.includes('/by-request/') || row.endpoint.endsWith('/material-options')), false)
    assert.equal(store.read(marker).kind, seal ? 'valid' : 'missing')
  }
})

test('serial submission reviews every scanned SN but never exposes or stores physical QR evidence', async () => {
  const { store, records } = await pendingStore(false)
  const scans = ['SKU-TEST', 'SN-PHYSICAL', 'QR-PRIVATE-PHYSICAL']; let submitted
  const { page } = await openDraft({ store, options: () => {
    const raw = options(); Object.assign(raw.items[0], { tracking_mode:'serial', selectable_quantity:'1.000', serials:[{serial_id:OTHER,serial_no:'SN-PHYSICAL'}] }); return raw
  }, scan: options => options.success({ result:scans.shift() }), preview: (_, body) => previewResult('consume', body),
  post: (_, body) => {
    submitted = store.read({ work_order_id:ORDER }).value
    assert.equal(body.lines[0].quantity, '1.000')
    assert.deepEqual(body.lines[0].serial_ids, [OTHER])
    assert.equal(body.lines[0].serial_verifications[0].qr_code, 'QR-PRIVATE-PHYSICAL')
    assert.equal([...records.values()].join().includes('QR-PRIVATE-PHYSICAL'), false)
    return {}
  }, recovery: () => recovered(submitted) })
  page.chooseOperation(event(null, {kind:'consume'})); page.addMaterial(event(ACCOUNT))
  for (const code of ['sku_code','serial_no','qr_code']) await page.scanMaterialCode(event(ACCOUNT, {code}))
  page.collectScanned(event(ACCOUNT))
  const work = page.submitMaterials(); await tick()
  assert.equal(page.data.confirming, true)
  assert.deepEqual(page.data.reviewRows[0].serialNumbers, ['SN-PHYSICAL'])
  assert.equal(page.data.reviewRows[0].quantity, '1.000')
  assert.equal(JSON.stringify(page.data).includes('QR-PRIVATE-PHYSICAL'), false)
  page.confirmSubmission(); await work
  assert.equal(store.read({ work_order_id:ORDER }).kind, 'missing')
  assert.equal(Object.keys(page._scans).length, 0)
})

test('every batch line appears in review and a later insufficient line rejects the whole batch', async () => {
  for (const insufficient of [false, true]) {
    let reads = 0
    const { page, state, store } = await openDraft({ options: () => {
      const raw = options(); const second = clone(raw.items[0]); second.stock_account_id = NEXT; second.material_id = NEXT
      second.material_name = '第二种物料'; second.sku_code = 'SKU-SECOND'
      if (++reads > 1 && insufficient) second.selectable_quantity = '0.500'
      raw.items.push(second); return raw
    }, preview: (_, body) => previewResult('consume', body) })
    prepareQuantity(page); page.addMaterial(event(NEXT)); page.editQuantity({...event(NEXT), detail:{value:'1'}})
    const work = page.submitMaterials(); await tick()
    if (insufficient) {
      await work; assert.equal(page.data.confirming,false)
      assert.equal(state.calls.some(row => row.endpoint.endsWith('/preview')), false)
    } else {
      assert.equal(page.data.reviewRows.length,2); assert.equal(page.data.reviewRows[1].sku,'SKU-SECOND')
      page.cancelSubmission(); await work
    }
    assert.equal(store.read({work_order_id:ORDER}).kind,'missing')
    assert.equal(state.calls.filter(row => row.method==='POST' && !row.endpoint.endsWith('/preview')).length,0)
  }
})

const pairedCommand = require('../utils/work-order-replacement-command')
const SN1 = '60000000-0000-4000-8000-000000000001'
const SN2 = '60000000-0000-4000-8000-000000000002'
const OLD1 = '70000000-0000-4000-8000-000000000001'
const OLD2 = '70000000-0000-4000-8000-000000000002'
const pairedWrites = state => state.calls.filter(call => call.method === 'POST' && call.endpoint.endsWith('/material-replacements'))
function pairedOptions(serial = false) {
  const raw = options()
  if (serial) Object.assign(raw.items[0], { tracking_mode: 'serial', selectable_quantity: '2.000',
    serials: [{ serial_id: SN1, serial_no: 'NEW-1' }, { serial_id: SN2, serial_no: 'NEW-2' }] })
  return raw
}
function removedResult(scan) {
  return { schema_version: '1.0', work_order_id: ORDER, operator_person_id: PERSON, authorization_version: 7,
    source_version: order().source_version, ledger_cursor: 2, checked_at: '2026-09-13T00:00:00Z',
    basis_stock_account_id: scan.basis_stock_account_id, material_id: LOCATION, sku_code: scan.sku_code,
    material_name: '拆回的另一种配件', base_unit: '件', condition_before: scan.condition_before,
    tracking_mode: scan.serial_no ? 'serial' : scan.lot_no ? 'lot' : 'none', quantity_scale: scan.serial_no ? 0 : 3,
    allow_fraction: !scan.serial_no, lot_id: scan.lot_no ? LOCATION : null, lot_no: scan.lot_no,
    serial_id: scan.serial_no ? scan.serial_no === 'OLD-1' ? OLD1 : OLD2 : null, serial_no: scan.serial_no }
}
function pairedPreview(body) {
  return { schema_version: '1.0', status: 'batch_validated', work_order_id: ORDER, operator_person_id: PERSON,
    authorization_version: 7, source_version: order().source_version, ledger_cursor: 2, checked_at: '2026-09-13T00:00:00Z',
    consume_line_count: body.consume_lines.length, recover_line_count: body.recover_lines.length, pair_count: body.replacement_pairs.length,
    request_hash: pairedCommand.requestHash({ workOrderId: ORDER, personId: PERSON,
      consumeLines: body.consume_lines, recoverLines: body.recover_lines, pairs: body.replacement_pairs }) }
}
async function pairedPage(settings = {}, serial = false) {
  const scans = []
  const result = await openDraft({ options: () => pairedOptions(serial), removed: removedResult,
    preview: (_, body) => pairedPreview(body), scan: options => options.success({ result: scans.shift() }), ...settings })
  result.page.chooseOperation(event(null, { kind: 'replace' })); result.page.addMaterial(event(ACCOUNT))
  if (serial) {
    for (const number of ['NEW-1', 'NEW-2']) {
      scans.push('SKU-TEST', number, `QR-private-${number}`)
      for (const code of ['sku_code', 'serial_no', 'qr_code']) await result.page.scanMaterialCode(event(ACCOUNT, { code }))
      result.page.collectScanned(event(ACCOUNT))
    }
  } else result.page.editQuantity({ ...event(ACCOUNT), detail: { value: '1.125' } })
  return { ...result, scans }
}
async function addRemovedRow(context, number = null, lot = '', basis = ACCOUNT, sku = 'REMOVED-SKU') {
  const { page, scans } = context
  page.addRemoved(event(basis))
  const id = page.data.removedRows.at(-1).id
  page.editRemoved(event(id, { field: 'condition', value: 'damaged' }))
  scans.push(sku); await page.scanRemoved(event(id, { code: 'sku_code' }))
  if (number) {
    scans.push(number, `QR-private-${number}`)
    for (const code of ['serial_no', 'qr_code']) await page.scanRemoved(event(id, { code }))
  }
  if (lot) page.editRemoved({ ...event(id, { field: 'lot_no' }), detail: { value: lot } })
  await page.inspectRemoved(event(id))
  if (!number) page.editRemoved({ ...event(id, { field: 'quantity' }), detail: { value: '0.125' } })
  return id
}

test('paired quantity page refreshes scans, reviews both sides and posts one parent only after durable marker', async () => {
  const { store, records } = await pendingStore(false); let marker
  const context = await pairedPage({ store, post: (endpoint, body, options) => {
    marker = store.read({ work_order_id: ORDER }).value
    assert.equal(marker.kind, 'work_order_replacement'); assert.equal(marker.operation_type, 'replace')
    assert.equal(marker.request_hash, pairedPreview(body).request_hash)
    assert.equal(marker.trace_request_id, body.request_id); assert.equal(options.requestId, body.request_id)
    assert.equal(options.idempotencyKey, body.idempotency_key)
    assert.equal(body.consume_lines[0].quantity, '1.125'); assert.equal(body.recover_lines[0].quantity, '0.125')
    assert.equal(body.recover_lines[0].material_id, LOCATION); assert.equal(body.recover_lines[0].lot_id, LOCATION)
    assert.equal(body.recover_lines[0].basis_stock_account_id, ACCOUNT); assert.deepEqual(body.replacement_pairs, [])
    for (const field of ['material_id', 'quantity', 'idempotency_key', 'sku_code', 'qr_code']) assert.equal([...records.values()].join().includes(field), false)
    return { noProof: true }
  }, recovery: () => pairedRecovered(marker) })
  const { page, state } = context
  await addRemovedRow(context, null, 'LOT-X')
  await page.previewMaterials()
  assert.match(page.data.previewMessage, /投入 1 行、拆回 1 行、SN 配对 0 对/)
  assert.equal(pairedWrites(state).length, 0); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  const work = page.submitMaterials(); await tick()
  assert.equal(page.data.confirming, true); assert.equal(page.data.reviewRows.length, 2)
  assert.equal(page.data.reviewRows[0].side, '投入消耗'); assert.equal(page.data.reviewRows[1].side, '拆回入库')
  assert.equal(page.data.reviewRows[1].basisLineNo, 1); assert.equal(page.data.reviewRows[1].lot, 'LOT-X')
  assert.equal(page.data.reviewRows[1].sku, 'REMOVED-SKU')
  await page.submitMaterials(); page.confirmSubmission(); page.confirmSubmission(); await work
  assert.equal(pairedWrites(state).length, 1); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  assert.match(page.data.recoveryMessage, /WR-TEST/); assert.equal(page._removedDrafts.length, 0)
  assert.equal(page.data.removedRows.length, 0)
  assert.equal(state.calls.filter(call => call.endpoint.endsWith('/removed-part')).length, 3)
  assert.equal(state.calls.filter(call => call.endpoint.includes('/by-request/')).length, 1)
})

test('multiple removed SNs merge into one target with explicitly selected reverse pairs and no displayed QR', async () => {
  const { store, records } = await pendingStore(false); let marker
  const context = await pairedPage({ store, post: (_, body) => {
    marker = store.read({ work_order_id: ORDER }).value
    assert.equal(body.consume_lines.length, 1); assert.equal(body.recover_lines.length, 1)
    assert.equal(body.recover_lines[0].quantity, '2.000')
    assert.deepEqual(body.recover_lines[0].serial_ids, [OLD1, OLD2])
    assert.deepEqual(body.replacement_pairs, [{ installed_serial_id: SN1, removed_serial_id: OLD2 }, { installed_serial_id: SN2, removed_serial_id: OLD1 }])
    assert.equal(body.recover_lines[0].serial_verifications[0].qr_code, 'QR-private-OLD-1')
    assert.equal([...records.values()].join().includes('QR-private'), false)
    return {}
  }, recovery: () => pairedRecovered(marker) }, true)
  const { page, state } = context
  const first = await addRemovedRow(context, 'OLD-1'), second = await addRemovedRow(context, 'OLD-2')
  assert.equal(page.data.removedRows[0].pairIndex, 0)
  await page.submitMaterials(); assert.equal(page.data.confirming, false); assert.match(page.data.previewMessage, /明确选择/)
  page.chooseInstalled({ ...event(first), detail: { value: '2' } })
  page.chooseInstalled({ ...event(second), detail: { value: '1' } })
  const work = page.submitMaterials(); await tick()
  assert.equal(page.data.confirming, true); assert.equal(page.data.reviewRows.length, 2)
  assert.deepEqual(page.data.reviewRows[1].serialNumbers, ['OLD-1', 'OLD-2'])
  assert.deepEqual(page.data.reviewPairs, [{ pairNo: 1, installed: 'NEW-2', removed: 'OLD-1' }, { pairNo: 2, installed: 'NEW-1', removed: 'OLD-2' }])
  assert.equal(JSON.stringify(page.data).includes('QR-private'), false)
  page.confirmSubmission(); await work
  assert.equal(pairedWrites(state).length, 1); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  assert.equal(page._removedDrafts.length, 0); assert.equal(page.data.reviewPairs.length, 0)
})

test('removed quantity precision, duplicated targets, missing basis rows and duplicated SN pairs block the whole batch', async () => {
  for (const mode of ['precision', 'duplicate_quantity', 'missing_row', 'duplicate_pair']) {
    const context = await pairedPage({}, mode === 'duplicate_pair'), { page, state, store } = context
    if (mode === 'duplicate_pair') {
      const first = await addRemovedRow(context, 'OLD-1'), second = await addRemovedRow(context, 'OLD-2')
      for (const id of [first, second]) page.chooseInstalled({ ...event(id), detail: { value: '1' } })
    } else if (mode !== 'missing_row') {
      const id = await addRemovedRow(context)
      if (mode === 'precision') page.editRemoved({ ...event(id, { field: 'quantity' }), detail: { value: '0.0001' } })
      if (mode === 'duplicate_quantity') await addRemovedRow(context)
    }
    await page.submitMaterials()
    assert.equal(page.data.confirming, false); assert.equal(pairedWrites(state).length, 0)
    assert.equal(state.calls.some(call => call.endpoint.endsWith('/preview')), false)
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  }
})

test('changed removed identity, policy, stock, source or preview digest prevents confirmation', async () => {
  for (const mode of ['identity', 'policy', 'stock', 'source', 'digest']) {
    let changed = false
    const context = await pairedPage({ options: () => { const raw = options(); if (changed && mode === 'stock') raw.items[0].selectable_quantity = '0.001'; return raw },
      removed: scan => { const raw = removedResult(scan); if (changed) { if (mode === 'identity') raw.material_id = NEXT; if (mode === 'policy') raw.quantity_scale = 0; if (mode === 'source') raw.source_version = 'different' }; return raw },
      preview: (_, body) => { const raw = pairedPreview(body); if (mode === 'digest') raw.request_hash = 'a'.repeat(64); return raw } })
    await addRemovedRow(context); changed = true
    await context.page.submitMaterials()
    assert.equal(context.page.data.confirming, false); assert.equal(pairedWrites(context.state).length, 0)
    assert.equal(context.store.read({ work_order_id: ORDER }).kind, 'missing')
  }
})

test('unknown removed SN remains blocked and condition or physical proof changes invalidate identification and pairing', async () => {
  let unknown = true
  const context = await pairedPage({ removed: scan => {
    if (unknown) throw Object.assign(new Error('not registered'), { code: 'removed_serial_not_found' })
    return removedResult(scan)
  } }, true)
  const { page } = context, id = await addRemovedRow(context, 'OLD-1')
  assert.equal(page.data.removedRows[0].identified, false); assert.match(page.data.previewMessage, /核对扫码内容/)
  unknown = false; await page.inspectRemoved(event(id)); page.chooseInstalled({ ...event(id), detail: { value: '1' } })
  assert.equal(page.data.removedRows[0].pairIndex, 1)
  page.editRemoved(event(id, { field: 'condition', value: 'used' }))
  assert.equal(page.data.removedRows[0].identified, false); assert.equal(page.data.removedRows[0].pairIndex, 0)
  await page.inspectRemoved(event(id)); page.chooseInstalled({ ...event(id), detail: { value: '1' } })
  page.removeScanned(event(ACCOUNT, { serial: SN1 })); assert.equal(page.data.removedRows[0].pairIndex, 0)
  page.removeMaterial(event(ACCOUNT)); assert.equal(page._removedDrafts.length, 0); assert.equal(page.data.removedRows.length, 0)
})

test('cancel, navigation and account switch during paired review never create a marker or post stock', async () => {
  for (const mode of ['cancel', 'hide', 'account', 'mode']) {
    const context = await pairedPage(); await addRemovedRow(context)
    const { page, state, store } = context
    if (mode === 'mode') { page.chooseOperation(event(null, { kind: 'consume' })); assert.equal(page._removedDrafts.length, 0); assert.equal(page.data.removedRows.length, 0); continue }
    const work = page.submitMaterials(); await tick(); assert.equal(page.data.confirming, true)
    if (mode === 'cancel') page.cancelSubmission()
    else if (mode === 'hide') page.onHide()
    else { state.token = 'other'; page.confirmSubmission() }
    await work
    assert.equal(pairedWrites(state).length, 0); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    assert.equal(page.data.reviewPairs.length, 0); assert.equal(page.data.reviewRows.length, 0)
    assert.equal(page._removedDrafts.length, mode === 'cancel' ? 1 : 0)
  }
})

test('paired timeout, 401, exact missing result, ordinary child or mismatched digest retains one original marker', async () => {
  for (const mode of ['timeout', '401', 'missing', 'child', 'digest']) {
    let marker
    const context = await pairedPage({ post: () => {
      marker = context.store.read({ work_order_id: ORDER }).value
      if (mode === 'timeout' || mode === '401') throw Object.assign(new Error('unknown'), { status: mode === '401' ? 401 : 0 })
      return {}
    }, recovery: () => {
      if (mode === 'missing') throw Object.assign(new Error('missing'), { responseReceived: true, status: 404, code: 'replacement_not_found' })
      if (mode === 'child') return recovered({ ...marker, operation_type: 'consume' })
      return { ...pairedRecovered(marker), request_hash: 'f'.repeat(64) }
    } })
    await addRemovedRow(context)
    const { page, state, store } = context, work = page.submitMaterials(); await tick(); page.confirmSubmission(); await work
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid'); assert.equal(page.data.canDraft, false)
    assert.equal(page.data.pendingRequests.length, 1); assert.equal(page.data.canRecover, true)
    assert.doesNotMatch(page.data.recoveryMessage, /已确认/); assert.equal(page._removedDrafts.length, 0)
    await page.submitMaterials(); assert.equal(pairedWrites(state).length, 1)
  }
})

test('storage readback, secure entropy and late permission failures block paired stock transport', async () => {
  for (const mode of ['storage', 'entropy', 'permission']) {
    const { store, storage } = await pendingStore(false); let revoke = false
    const context = await pairedPage({ store,
      randomFailure: mode === 'entropy' ? () => { throw new Error('no entropy') } : null,
      access: () => ({ ...access(), permissions: revoke ? access().permissions : access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }) })
    await addRemovedRow(context)
    const work = context.page.submitMaterials(); await tick(); assert.equal(context.page.data.confirming, true)
    if (mode === 'storage') storage.setStorageSync = () => {}
    if (mode === 'permission') revoke = true
    context.page.confirmSubmission(); await work
    assert.equal(pairedWrites(context.state).length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, mode === 'storage' ? 'unavailable' : 'missing')
  }
})

test('hiding during removed scan lookup, batch preview, stock POST or original GET stops dependent calls', async () => {
  for (const phase of ['removed', 'preview', 'post', 'recovery']) {
    const pending = deferred(); let wait = false, marker, body
    const context = await pairedPage({ removed: scan => wait && phase === 'removed' ? pending.promise : removedResult(scan),
      preview: (_, value) => { body = value; return wait && phase === 'preview' ? pending.promise : pairedPreview(value) },
      post: () => { marker = context.store.read({ work_order_id: ORDER }).value; return phase === 'post' ? pending.promise : {} },
      recovery: () => pending.promise })
    const id = await addRemovedRow(context); wait = true
    const { page, state, store } = context
    const work = phase === 'removed' ? page.inspectRemoved(event(id)) : page.submitMaterials()
    await tick()
    if (phase === 'post' || phase === 'recovery') { assert.equal(page.data.confirming, true); page.confirmSubmission(); await tick() }
    page.onHide(); const calls = state.calls.length
    pending.resolve(phase === 'removed' ? removedResult({ basis_stock_account_id: ACCOUNT, sku_code: 'REMOVED-SKU', condition_before: 'damaged', lot_no: null, serial_no: null })
      : phase === 'preview' ? pairedPreview(body) : phase === 'post' ? {} : pairedRecovered(marker))
    await work
    assert.equal(state.calls.length, calls); assert.equal(page._removedDrafts.length, 0)
    assert.equal(page.data.removedRows.length, 0); assert.equal(page.data.reviewPairs.length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, phase === 'post' || phase === 'recovery' ? 'valid' : 'missing')
  }
})


test('paired review includes every independent basis and invalid later recovery prevents the entire review', async () => {
  for (const invalid of [false, true]) {
    const context = await pairedPage({ options: () => {
      const raw = options(), second = clone(raw.items[0]); second.stock_account_id = NEXT; second.material_id = NEXT
      second.sku_code = 'SECOND-SKU'; second.material_name = '第二条投入'; raw.items.push(second); return raw
    }, removed: scan => { const raw = removedResult(scan); if (scan.basis_stock_account_id === NEXT) raw.material_id = NEXT; return raw } })
    const { page, state, store } = context
    page.addMaterial(event(NEXT)); page.editQuantity({ ...event(NEXT), detail: { value: '1' } })
    await addRemovedRow(context)
    const id = await addRemovedRow(context, null, '', NEXT, 'SECOND-REMOVED')
    if (invalid) page.editRemoved({ ...event(id, { field: 'quantity' }), detail: { value: '0' } })
    const work = page.submitMaterials(); await tick()
    if (invalid) {
      assert.equal(page.data.confirming, false); assert.equal(state.calls.some(call => call.endpoint.endsWith('/preview')), false)
    } else {
      assert.equal(page.data.reviewRows.length, 4)
      assert.deepEqual(page.data.reviewRows.map(row => row.basisLineNo), [1, 2, 1, 2])
      assert.deepEqual(page.data.reviewRows.map(row => row.sku), ['SKU-TEST', 'SECOND-SKU', 'REMOVED-SKU', 'SECOND-REMOVED'])
      page.cancelSubmission()
    }
    await work; assert.equal(pairedWrites(state).length, 0); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  }
})

test('late removed camera results are discarded after hide or account switch without exposing the QR', async () => {
  for (const mode of ['hide', 'account']) {
    let camera
    const context = await pairedPage({ scan: options => { camera = options } })
    const { page, state } = context
    page.addRemoved(event(ACCOUNT)); const id = page.data.removedRows[0].id
    const scan = page.scanRemoved(event(id, { code: 'qr_code' }))
    if (mode === 'hide') page.onHide(); else state.token = 'changed-session'
    camera.success({ result: 'PRIVATE-LATE-QR' }); await scan
    assert.equal(page._removedDrafts.length, 0); assert.equal(page.data.removedRows.length, 0)
    assert.equal(JSON.stringify(page.data).includes('PRIVATE-LATE-QR'), false)
    assert.equal(state.calls.some(call => call.method === 'POST'), false)
  }
})

const registrationCommand = require('../utils/work-order-removed-registration-command')
const registrationWrites = state => state.calls.filter(call => call.method === 'POST' && call.endpoint.endsWith('/removed-registrations'))
const unknownIdentity = () => Object.assign(new Error('unknown removed identity'), { responseReceived: true, status: 412, code: 'removed_serial_not_found' })
function registrationPreview(body) {
  return { ...removedResult(body), tracking_mode: body.lot_no ? 'lot_and_serial' : 'serial', quantity_scale: 0, allow_fraction: false,
    serial_id: null, status: 'registration_validated', request_hash: registrationCommand.requestHash({ workOrderId: ORDER, personId: PERSON, scan: body }) }
}
function registrationProof(marker, lot = false) {
  return { schema_version: '1.0', status: 'registered', registration_id: OTHER, registration_no: 'WORS-' + 'A'.repeat(24),
    work_order_id: ORDER, operator_person_id: PERSON, serial_id: OLD2, material_id: LOCATION, lot_id: lot ? LOCATION : null,
    basis_stock_account_id: ACCOUNT, request_id: marker.trace_request_id, request_hash: marker.request_hash, registered_at: '2026-09-13T08:00:00Z' }
}
async function unknownPage(settings = {}, lot = '') {
  const context = await pairedPage({ removed: () => { throw unknownIdentity() }, preview: (_, body) => registrationPreview(body), ...settings }, true)
  context.store = context.page._store
  context.removedId = await addRemovedRow(context, 'UNKNOWN-OLD', lot)
  assert.equal(context.page.data.removedRows[0].registrationAvailable, true)
  return context
}

test('unknown SN registration reviews only identity, writes once after durable trace and returns to explicit pairing', async () => {
  for (const lot of ['', 'EXACT-LOT']) {
    const { store, records } = await pendingStore(false); let marker, admitted = false
    const context = await unknownPage({ store, removed: scan => {
      if (!admitted) throw unknownIdentity()
      return { ...removedResult(scan), tracking_mode: lot ? 'lot_and_serial' : 'serial' }
    }, post: (endpoint, body, options) => {
      assert.ok(endpoint.endsWith('/removed-registrations')); marker = store.read({ work_order_id: ORDER }).value
      assert.equal(marker.kind, registrationCommand.KIND); assert.equal(marker.operation_type, 'register_removed')
      const { idempotency_key, request_id, ...scan } = body
      assert.equal(marker.request_hash, registrationPreview(scan).request_hash)
      assert.equal(body.request_id, marker.trace_request_id); assert.equal(options.requestId, body.request_id); assert.equal(options.idempotencyKey, body.idempotency_key)
      assert.equal(body.qr_code, 'QR-private-UNKNOWN-OLD'); assert.equal(body.lot_no, lot || null)
      const stored = [...records.values()].join('')
      for (const privateValue of ['QR-private', 'UNKNOWN-OLD', 'material_id', 'sku_code', 'idempotency_key']) assert.equal(stored.includes(privateValue), false)
      admitted = true; return { ignored: 'POST alone is not original proof' }
    }, recovery: () => registrationProof(marker, !!lot) }, lot)
    const { page, state, removedId } = context
    const work = page.registerRemoved(event(removedId)); await tick()
    assert.equal(page.data.confirming, true); assert.match(page.data.reviewTitle, /登记拆回 SN/)
    assert.match(page.data.reviewDescription, /库存不会增加/); assert.equal(page.data.reviewWorkOrderNo, 'WO-TEST')
    assert.equal(page.data.reviewRows[0].basisSku, 'SKU-TEST'); assert.equal(page.data.reviewRows[0].lot, lot)
    assert.equal(page.data.reviewRows[0].basisCondition, '新件'); assert.equal(page.data.reviewRows[0].basisLot, '')
    assert.deepEqual(page.data.reviewRows[0].serialNumbers, ['UNKNOWN-OLD'])
    assert.equal(JSON.stringify(page.data).includes('QR-private'), false)
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    await page.registerRemoved(event(removedId)); assert.equal(registrationWrites(state).length, 0)
    page.confirmSubmission(); await work
    assert.equal(registrationWrites(state).length, 1); assert.equal(pairedWrites(state).length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing'); assert.equal(page.data.canDraft, true)
    assert.match(page.data.previewMessage, /登记已确认.*库存未变动/)
    assert.equal(page.data.removedRows[0].identified, false); assert.equal(page.data.removedRows[0].registrationAvailable, false)
    assert.equal(page._removedDrafts[0].scan.qr_code, 'QR-private-UNKNOWN-OLD')
    await page.inspectRemoved(event(removedId)); assert.equal(page.data.removedRows[0].identified, true)
    page.chooseInstalled({ ...event(removedId), detail: { value: '1' } })
    assert.equal(page._removedDrafts[0].installedSerialId, SN1)
    page.onHide(); assert.equal(page._removedDrafts.length, 0); assert.equal(JSON.stringify(page.data).includes('UNKNOWN-OLD'), false)
  }
})
test('only exact unknown-identity response offers registration and input edits invalidate that offer', async () => {
  for (const mode of ['transport', 'other_code', 'wrong_status', 'unobserved', 'known']) {
    const context = await pairedPage({ removed: scan => {
      if (mode === 'known') return removedResult(scan)
      const error = unknownIdentity()
      if (mode === 'transport') delete error.responseReceived
      if (mode === 'other_code') error.code = 'removed_material_not_found'
      if (mode === 'wrong_status') error.status = 404
      if (mode === 'unobserved') error.responseReceived = false
      throw error
    } }, true)
    const id = await addRemovedRow(context, 'UNKNOWN-OLD')
    assert.equal(context.page.data.removedRows[0].registrationAvailable, false)
    await context.page.registerRemoved(event(id)); assert.equal(registrationWrites(context.state).length, 0)
  }
  for (const field of ['condition', 'lot_no', 'qr_code']) {
    const context = await unknownPage(), { page, removedId } = context
    if (field === 'qr_code') { context.scans.push('CHANGED-QR'); await page.scanRemoved(event(removedId, { code: field })) }
    else page.editRemoved({ ...event(removedId, { field, value: 'used' }), detail: { value: 'CHANGED-LOT' } })
    assert.equal(page.data.removedRows[0].registrationAvailable, false)
    await page.registerRemoved(event(removedId)); assert.equal(registrationWrites(context.state).length, 0)
  }
})
test('registration cancel, navigation or account change during review never persists or writes', async () => {
  for (const mode of ['cancel', 'hide', 'switch']) {
    const { page, state, store, removedId } = await unknownPage()
    const work = page.registerRemoved(event(removedId)); await tick(); assert.equal(page.data.confirming, true)
    if (mode === 'cancel') page.cancelSubmission()
    else if (mode === 'hide') page.onHide()
    else { state.token = 'another-session'; page.confirmSubmission() }
    await work
    assert.equal(registrationWrites(state).length, 0); assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
    if (mode === 'cancel') assert.equal(page.data.removedRows[0].registrationAvailable, true)
  }
})
test('registration timeout, 401, mismatched GET or absent original preserves marker and blocks all resubmission', async () => {
  for (const mode of ['timeout', '401', 'missing', 'hash', 'parent']) {
    let marker
    const context = await unknownPage({ post: () => {
      marker = context.store.read({ work_order_id: ORDER }).value
      if (mode === 'timeout' || mode === '401') throw new Error(mode)
      return {}
    }, recovery: () => {
      if (mode === 'missing') throw Object.assign(new Error('missing'), { responseReceived: true, status: 404, code: 'removed_registration_not_found' })
      if (mode === 'parent') return pairedRecovered({ ...marker, kind: 'work_order_replacement', operation_type: 'replace' })
      return { ...registrationProof(marker), request_hash: 'b'.repeat(64) }
    } })
    const { page, state, store, removedId } = context
    const work = page.registerRemoved(event(removedId)); await tick(); page.confirmSubmission(); await work
    assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid'); assert.equal(page.data.canDraft, false)
    assert.equal(page.data.pendingRequests[0].sealable, true); assert.match(page.data.pendingRequests[0].label, /拆回 SN 登记/)
    assert.equal(page._removedDrafts.length, 0); assert.equal(JSON.stringify(page.data).includes('QR-private'), false)
    await page.registerRemoved(event(removedId)); await page.submitMaterials()
    assert.equal(registrationWrites(state).length, 1); assert.equal(pairedWrites(state).length, 0)
  }
})
test('registration stored proof and independent seal remain recoverable outside the current order list', async () => {
  for (const outcome of ['registered', 'sealed']) {
    const { store, marker: ordinary } = await pendingStore(false)
    const marker = { ...ordinary, kind: registrationCommand.KIND, operation_type: 'register_removed' }
    await store.withLease(marker, lease => lease.persist(marker)); let closed = false
    const seal = { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: { seal_id: OTHER,
      work_order_id: ORDER, operator_person_id: PERSON, operation_type: 'register_removed', request_id: marker.trace_request_id,
      request_hash: marker.request_hash, sealed_at: '2026-09-13T08:00:00Z' } }
    const { page, state } = harness({ store, list: () => list(NEXT), access: () => ({ ...access(), permissions: access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }),
      recovery: () => {
        if (outcome === 'registered') return registrationProof(marker)
        if (closed) return seal
        throw Object.assign(new Error('missing'), { responseReceived: true, status: 404, code: 'removed_registration_not_found' })
      }, seal: () => { closed = true; return seal }, confirm: options => options.success({ confirm: true }) })
    await page.onShow(); assert.equal(page.data.pendingRequests[0].sealable, true)
    if (outcome === 'registered') await page.recoverPendingRequest(event(ORDER)); else await page.sealPendingRequest(event(ORDER))
    assert.equal(store.read(marker).kind, 'missing'); assert.equal(page.data.pendingRequests.length, 0)
    assert.match(page.data.recoveryMessage, outcome === 'registered' ? /登记已确认.*库存未变动/ : /已关闭且未执行/)
    assert.equal(state.calls.some(call => call.endpoint.includes('/material-options')), false)
    assert.ok(state.calls.filter(call => call.endpoint.includes('/by-request/')).every(call => call.endpoint.includes('/removed-registrations/')))
  }
})
test('registration entropy, durable readback and current operate permission guard the final transport', async () => {
  for (const mode of ['entropy', 'storage', 'permission']) {
    const { store, storage } = await pendingStore(false); let revoke = false
    const context = await unknownPage({ store, randomFailure: mode === 'entropy' ? () => { throw new Error('no entropy') } : null,
      access: () => ({ ...access(), permissions: revoke ? access().permissions : access().permissions.concat({ resource: 'work_order_material', action: 'operate', field_code: '' }) }) })
    const work = context.page.registerRemoved(event(context.removedId)); await tick(); assert.equal(context.page.data.confirming, true)
    if (mode === 'storage') storage.setStorageSync = () => {}
    if (mode === 'permission') revoke = true
    context.page.confirmSubmission(); await work
    assert.equal(registrationWrites(context.state).length, 0)
    assert.equal(store.read({ work_order_id: ORDER }).kind, mode === 'storage' ? 'unavailable' : 'missing')
  }
})
test('registration discards late preview, POST or original GET after leaving the page', async () => {
  for (const phase of ['preview', 'post', 'get']) {
    const pending = deferred(); let body, marker
    const context = await unknownPage({ preview: (_, value) => { body = value; return phase === 'preview' ? pending.promise : registrationPreview(value) },
      post: () => { marker = context.store.read({ work_order_id: ORDER }).value; return phase === 'post' ? pending.promise : {} }, recovery: () => pending.promise })
    const { page, state, store, removedId } = context
    const work = page.registerRemoved(event(removedId)); await tick()
    if (phase !== 'preview') { page.confirmSubmission(); await tick() }
    page.onHide(); const count = state.calls.length
    pending.resolve(phase === 'preview' ? registrationPreview(body) : phase === 'post' ? {} : registrationProof(marker))
    await work
    assert.equal(state.calls.length, count); assert.equal(page.data.state, 'idle')
    assert.equal(store.read({ work_order_id: ORDER }).kind, phase === 'preview' ? 'missing' : 'valid')
  }
})
test('stale registration preview, identity collision or released basis prevents any review or write', async () => {
  for (const mode of ['hash', 'collision', 'basis']) {
    let changed = false
    const context = await unknownPage({ options: () => { const value = pairedOptions(true); if (changed && mode === 'basis') value.items[0].allowed_actions = []; return value },
      preview: (_, body) => { if (mode === 'collision') throw new Error('SN 或二维码已登记'); const raw = registrationPreview(body); if (mode === 'hash') raw.request_hash = 'f'.repeat(64); return raw } })
    changed = true
    await context.page.registerRemoved(event(context.removedId))
    assert.equal(context.page.data.confirming, false); assert.equal(registrationWrites(context.state).length, 0)
    assert.equal(context.store.read({ work_order_id: ORDER }).kind, 'missing')
  }
})

const completionContract = require('../utils/work-order-completion-contract')
function completionResult(kind = 'unreleased_reservation') {
  const issue = { kind, reference_id: kind === 'unreleased_reservation' ? ACCOUNT : NEXT,
    operation_id: ['pending_return', 'unpaired_serial_consumption'].includes(kind) ? NEXT : null,
    operation_no: ['pending_return', 'unpaired_serial_consumption'].includes(kind) ? 'WOM-TEST' : null,
    stock_account_id: ACCOUNT, material_id: OTHER, sku_code: 'SKU-TEST', material_name: '测试物料', base_unit: '件',
    condition_code: ['pending_recovery', 'pending_return'].includes(kind) ? 'damaged' : 'new',
    lot_id: null, lot_no: null, quantity: '1.000', serials: kind === 'unreleased_reservation' ? [] : [{ serial_id: OTHER, serial_no: 'SN-TEST' }] }
  return { schema_version: '1.0', person_id: PERSON, authorization_version: 7, work_order: order(),
    checked_at: '2026-09-12T08:01:00Z', ledger_cursor: 7, material_check_status: kind ? 'blocked' : 'clear',
    blockers: kind ? [kind] : [], issue_count: kind ? 1 : 0, issues: kind ? [issue] : [] }
}
const completionExpected = { workOrderId: ORDER, personId: PERSON, authorizationVersion: 7, sourceVersion: 'wo-v2:test' }
test('completion contract distinguishes each obligation and exact quantities from a clear observation', () => {
  for (const kind of ['', 'unreleased_reservation', 'pending_recovery', 'pending_return', 'unpaired_serial_consumption']) {
    const raw = completionResult(kind), result = completionContract.validateCompletion(raw, completionExpected)
    assert.equal(result.status, kind ? 'blocked' : 'clear'); assert.equal(result.issueCount, kind ? 1 : 0)
  }
  const large = completionResult(); large.issues[0].quantity = '900719925474099.999'
  assert.equal(completionContract.validateCompletion(large, completionExpected).issues[0].quantity, large.issues[0].quantity)
  const lot = completionResult('pending_return'); lot.issues[0].lot_id = NEXT; lot.issues[0].lot_no = 'EXACT-LOT'
  assert.equal(completionContract.validateCompletion(lot, completionExpected).issues[0].lot_no, 'EXACT-LOT')
})
test('completion contract rejects partial counts, foreign context, QR leakage and false clear results', () => {
  for (const mutate of [r => { r.person_id = OTHER }, r => { r.authorization_version++ }, r => { r.work_order.work_order_id = NEXT },
    r => { r.work_order.source_version = 'stale' }, r => { r.checked_at = 'yesterday' }, r => { r.checked_at = '2020-01-01T00:00:00Z' },
    r => { r.ledger_cursor = -1 }, r => { r.issue_count = 0 }, r => { r.material_check_status = 'clear' }, r => { r.blockers = [] },
    r => { r.blockers.push('pending_return') }, r => { r.blockers.push(r.blockers[0]) }, r => { r.issues.push(clone(r.issues[0])); r.issue_count++ },
    r => { r.issues[0].qr_code = 'private' }, r => { r.issues[0].quantity = 'NaN' }, r => { r.issues[0].quantity = '0.000' },
    r => { r.issues[0].lot_id = OTHER }, r => { r.issues[0].lot_no = 'unbound' }, r => { r.issues[0].stock_account_id = NEXT },
    r => { r.issues[0].serials = [{ serial_id: OTHER, serial_no: 'SN', qr_code: 'private' }] }]) {
    const raw = completionResult(); mutate(raw); assert.throws(() => completionContract.validateCompletion(raw, completionExpected))
  }
  for (const kind of ['pending_recovery', 'pending_return', 'unpaired_serial_consumption']) {
    const raw = completionResult(kind); raw.issues[0].serials.push(clone(raw.issues[0].serials[0]))
    assert.throws(() => completionContract.validateCompletion(raw, completionExpected))
  }
})
test('completion page performs only fresh no-store reads and displays exact pending return facts', async () => {
  const { page, state, store } = await openDraft({ completion: () => completionResult('pending_return') })
  await page.checkCompletion()
  assert.equal(page.data.completion.issues[0].label, '回收后待退回')
  assert.equal(page.data.completion.issues[0].serials[0].serial_no, 'SN-TEST')
  assert.match(page.data.completionMessage, /仍有物料事项/)
  assert.equal(store.read({ work_order_id: ORDER }).kind, 'missing')
  for (const call of state.calls) { assert.equal(call.method, 'GET'); assert.equal(call.noRefresh, true); assert.equal(call.header['Cache-Control'], 'no-store') }
  page.onHide(); assert.equal(page.data.completion, null); assert.equal(page.data.completionMessage, '')
})
test('server clear result never overrides a local uncertain original request', async () => {
  const { store } = await pendingStore()
  const { page } = await openDraft({ store, completion: () => completionResult('') })
  await page.checkCompletion()
  assert.equal(page.data.completion.status, 'clear'); assert.equal(page.data.completion.clientPending, true)
  assert.match(page.data.completionMessage, /本机还有待确认/)
  assert.equal(store.read({ work_order_id: ORDER }).kind, 'valid')
})
test('completion error, wrong source or permission drift clears the previous displayed result', async () => {
  for (const mode of ['transport', 'source', 'permission']) {
    let changed = false
    const { page } = await openDraft({ access: () => changed && mode === 'permission' ? { ...access(), permissions: [] } : access(),
      completion: () => { if (changed && mode === 'transport') throw new Error('network'); const raw = completionResult(''); if (changed) raw.work_order.source_version = 'changed'; return raw } })
    await page.checkCompletion(); assert.equal(page.data.completion.status, 'clear')
    changed = true; await page.checkCompletion()
    assert.equal(page.data.completion, null)
    if (mode !== 'permission') assert.match(page.data.completionMessage, /结束检查未完成/)
  }
})
test('completion double click, hide and session change never apply a late result', async () => {
  for (const mode of ['hide', 'switch']) {
    const result = deferred(), { page, state } = await openDraft({ completion: () => result.promise })
    const work = page.checkCompletion(); await tick(); await page.checkCompletion()
    assert.equal(state.calls.filter(call => call.endpoint.endsWith('/material-completion-check')).length, 1)
    if (mode === 'hide') page.onHide(); else state.token = 'different-session'
    const calls = state.calls.length; result.resolve(completionResult('')); await work
    assert.equal(page.data.completion, null); assert.equal(state.calls.length, calls)
  }
})
test('a new material submission retires an earlier completion observation', async () => {
  const { page } = await openDraft({ completion: () => completionResult(''), preview: (_, body) => previewResult('consume', body) })
  await page.checkCompletion(); assert.equal(page.data.completion.status, 'clear')
  page.chooseOperation(event(null, { kind: 'consume' })); page.addMaterial(event(ACCOUNT)); page.editQuantity({ ...event(ACCOUNT), detail: { value: '1' } })
  const work = page.submitMaterials(); await tick()
  assert.equal(page.data.completion, null); assert.equal(page.data.completionMessage, '')
  page.cancelSubmission(); await work
})
