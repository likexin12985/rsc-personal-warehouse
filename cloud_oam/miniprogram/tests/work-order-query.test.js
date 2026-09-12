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
  let definition, count = 0
  const state = { user: user(), token: 'fixture-session', calls: [] }
  const context = { Page(value) { definition = value }, wx: { stopPullDownRefresh() {}, scanCode: options => settings.scan(options), showModal: options => settings.confirm(options) }, require(name) {
    if (name === '../../utils/work-order-recovery-store' && settings.store) return { getStore: () => settings.store }
    if (name === '../../utils/session') return { getUser: () => state.user, getToken: () => state.token, ensureLogin: () => !!state.token }
    if (name === '../../utils/api') return { async postSealNoReplay(endpoint, data, options) {
      state.calls.push({endpoint,method:'POST',data,...clone(options)}); return settings.seal(data, state)
    }, async request(endpoint, request) {
      state.calls.push({ endpoint, ...clone(request) })
      if (endpoint === '/auth/me') return settings.identity ? settings.identity(state) : user()
      if (endpoint === '/access/context') return settings.access ? settings.access(++count) : access()
      if (endpoint.includes('/by-request/')) return settings.recovery(state)
      if (endpoint.endsWith('/preview')) return settings.preview(endpoint, request.data, state)
      if (endpoint.endsWith('/material-options')) return settings.options ? settings.options(state) : options()
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
  const store = module.createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.has(key) ? records.get(key) : '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
  } })
  const marker = module.validateMarker({ v: 1, kind: 'work_order_material', work_order_id: ORDER, person_id: PERSON, authorization_version: 7,
    operation_type: 'consume', trace_request_id: 'wxreq-' + 'c'.repeat(36), request_hash: 'd'.repeat(64) })
  if (pending) await store.withLease(marker, lease => lease.persist(marker))
  return { store, marker }
}
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
