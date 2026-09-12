const assert = require('node:assert/strict')
const test = require('node:test')
const command = require('../utils/work-order-command')
const storageModule = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const PERSON = '10000000-0000-4000-8000-000000000001'
const ORDER = '20000000-0000-4000-8000-000000000001'
const OTHER_ORDER = '20000000-0000-4000-8000-000000000002'
const ACCOUNT = '40000000-0000-4000-8000-000000000001'
const MATERIAL = '50000000-0000-4000-8000-000000000001'
const SERIAL = '60000000-0000-4000-8000-000000000001'
const OPERATION = '70000000-0000-4000-8000-000000000001'
const TRACE = 'wxreq-' + 'a'.repeat(36)
const clone = value => JSON.parse(JSON.stringify(value))
function line() { return { material_id: MATERIAL, stock_account_id: ACCOUNT, quantity: '1', condition_before: 'used', serial_ids: [SERIAL], serial_verifications: [{ serial_id: SERIAL, sku_code: 'SKU-测试', serial_no: 'SN-旧件', qr_code: 'QR-测试😀' }] } }
function marker() { return storageModule.validateMarker({ v: 1, kind: 'work_order_material', work_order_id: ORDER, person_id: PERSON,
  authorization_version: 7, operation_type: 'occupy', trace_request_id: TRACE, request_hash: command.requestHash('occupy', ORDER, PERSON, [line()]) }) }
function confirmed() {
  return { schema_version: '1.0', lookup_status: 'confirmed', command: { schema_version: '1.0', operation_id: OPERATION,
    operation_no: 'WOM-TEST', work_order_id: ORDER, posting_transaction_id: ACCOUNT, operation_type: 'occupy', status: 'posted',
    operator_person_id: PERSON, request_id: TRACE, request_hash: marker().request_hash, posted_at: '2026-09-12T08:00:00Z' } }
}
function storage() {
  const data = new Map()
  return { data, getStorageInfoSync: () => ({ keys: [...data.keys()] }), getStorageSync: key => data.has(key) ? data.get(key) : '',
    setStorageSync: (key, value) => data.set(key, value), removeStorageSync: key => data.delete(key) }
}
function storeFor(backing = storage()) { return storageModule.createStore({ storage: backing, state: { active: new Set(), faults: new Set() } }) }
const anchor = { work_order_id: ORDER }
async function persisted(backing = storage()) { const store = storeFor(backing); await store.withLease(anchor, lease => lease.persist(marker())); return { store, backing } }

test('canonical command fingerprint matches Python for Unicode and server-derived occupy target', () => {
  // Computed independently by the backend client_request_hash helper.
  assert.equal(command.requestHash('occupy', ORDER, PERSON, [line()]), '2039bbb8002213aa96981d0bfc0d1ebeb8132780d37ba397f3e7f8b303d3a3c4')
  const payload = command.payload('occupy', ORDER, PERSON, [line()])
  assert.equal(payload.lines[0].quantity, '1.000'); assert.equal('target_stock_account_id' in payload.lines[0], false)
  const fraction = line(); fraction.serial_ids = []; fraction.serial_verifications = []; fraction.quantity = '900719925474099.999'
  assert.equal(command.payload('consume', ORDER, PERSON, [fraction]).lines[0].quantity, '900719925474099.999')
})
test('command normalization rejects ambiguous accounts, serials, targets and missing scan proof', () => {
  for (const change of [l => { l.quantity = '1.001' }, l => { l.serial_ids = [SERIAL, SERIAL] }, l => { l.serial_verifications = [] }, l => { l.serial_verifications[0].serial_id = OPERATION }, l => { l.serial_verifications[0].qr_code = '' }, l => { l.target_stock_account_id = ACCOUNT }]) {
    const value = line(); change(value); assert.throws(() => command.payload('occupy', ORDER, PERSON, [value]))
  }
  assert.throws(() => command.payload('occupy', ORDER, PERSON, [line(), line()]))
  assert.throws(() => command.payload('release', ORDER, PERSON, [line()]))
  assert.throws(() => command.utf8('\ud800'))
})
test('recovery records survive restart and contain only anchors and a digest', async () => {
  const { backing } = await persisted()
  const raw = backing.getStorageSync(storageModule.PREFIX + ORDER)
  for (const forbidden of ['qr_code', 'serial_no', 'quantity', 'material_id', 'idempotency_key', 'token', 'SKU-测试', 'SN-旧件']) assert.equal(raw.includes(forbidden), false)
  const restored = storeFor(backing).read(anchor)
  assert.equal(restored.kind, 'valid'); assert.deepEqual(restored.value, marker())
  assert.throws(() => storageModule.validateMarker({ ...marker(), token: 'forbidden' }))
})
test('one unresolved command blocks overwrite only for its own work order', async () => {
  const { store } = await persisted()
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  let release
  const held = store.withLease(anchor, () => new Promise(resolve => { release = resolve }))
  await assert.rejects(store.withLease(anchor, () => {}))
  const other = { ...marker(), work_order_id: OTHER_ORDER }
  await store.withLease(other, lease => lease.persist(other))
  release(); await held
  assert.equal(store.read(anchor).kind, 'valid')
})
test('storage write/readback failures and corrupt records stay blocked', async () => {
  const broken = storage(); broken.setStorageSync = () => {}
  const store = storeFor(broken)
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  assert.equal(store.read(anchor).kind, 'unavailable')
  const { backing } = await persisted(); backing.setStorageSync(storageModule.PREFIX + ORDER, '{bad')
  assert.equal(storeFor(backing).read(anchor).kind, 'unavailable')
})
test('confirmed original result clears only its exact marker using GET with no refresh', async () => {
  const { store } = await persisted(), calls = []
  const result = await recoverPending({ api: { async request(path, options) { calls.push({ path, ...clone(options) }); return confirmed() } }, store,
    workOrderId: ORDER, personId: PERSON, authorize: async () => 'current-authority' })
  assert.equal(result.status, 'confirmed'); assert.equal(store.read(anchor).kind, 'missing')
  assert.deepEqual(calls, [{ path: `/v1/work-orders/${ORDER}/material-operations/occupy/by-request/${TRACE}`, method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }])
})
test('not-observed, timeout and conflicting results retain the marker without any POST', async () => {
  for (const mode of ['not_observed', 'timeout', 'hash', 'kind', 'person', 'request']) {
    const { store } = await persisted(); let calls = 0
    const api = { async request(_, options) {
      calls++; assert.equal(options.method, 'GET')
      if (mode === 'timeout') throw new Error('timeout')
      if (mode === 'not_observed') return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
      const raw = confirmed()
      if (mode === 'hash') raw.command.request_hash = 'b'.repeat(64)
      if (mode === 'kind') raw.command.operation_type = 'consume'
      if (mode === 'person') raw.command.operator_person_id = ACCOUNT
      if (mode === 'request') raw.command.request_id = 'wxreq-' + 'b'.repeat(36)
      return raw
    } }
    const work = recoverPending({ api, store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'authority' })
    if (mode === 'not_observed') assert.equal((await work).status, 'pending')
    else await assert.rejects(work)
    assert.equal(calls, 1); assert.equal(store.read(anchor).kind, 'valid')
  }
})
test('authority changes, another person and storage-clear faults never discard recovery evidence', async () => {
  const { store, backing } = await persisted(); let count = 0
  await assert.rejects(recoverPending({ api: { request: async () => confirmed() }, store, workOrderId: ORDER, personId: PERSON, authorize: async () => String(++count) }))
  assert.equal(store.read(anchor).kind, 'valid')
  await assert.rejects(recoverPending({ api: { request: () => { throw new Error('must not read another person') } }, store, workOrderId: ORDER, personId: ACCOUNT, authorize: async () => '' }))
  backing.removeStorageSync = () => {}
  await assert.rejects(recoverPending({ api: { request: async () => confirmed() }, store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same' }))
  assert.equal(store.read(anchor).kind, 'unavailable')
  assert.ok(backing.getStorageSync(storageModule.PREFIX + ORDER))
})

test('pending inbox finds own original requests across orders and authorization versions after restart', async () => {
  const { store, backing } = await persisted()
  const older = { ...marker(), work_order_id: OTHER_ORDER, authorization_version: 2, operation_type: 'release' }
  await store.withLease(older, lease => lease.persist(older))
  const foreign = { ...marker(), work_order_id: ACCOUNT, person_id: ACCOUNT }
  await store.withLease(foreign, lease => lease.persist(foreign))
  const snapshot = storeFor(backing).listPending(PERSON)
  assert.equal(snapshot.kind, 'ready')
  assert.deepEqual(snapshot.items.map(row => row.work_order_id), [ORDER, OTHER_ORDER])
  assert.equal(snapshot.items[1].authorization_version, 2)
  assert.equal(Object.isFrozen(snapshot.items), true)
  assert.equal(storeFor(backing).listPending(ACCOUNT).items.length, 1)
  assert.equal(backing.data.size, 3)
})

test('one corrupt record stays untouched and does not hide other verified own requests', async () => {
  const { backing } = await persisted()
  const bad = storageModule.PREFIX + OTHER_ORDER
  backing.setStorageSync(bad, '{bad')
  backing.setStorageSync(storageModule.PREFIX + 'non-canonical-key', 'unreadable')
  const store = storeFor(backing), snapshot = store.listPending(PERSON)
  assert.equal(snapshot.kind, 'partial'); assert.deepEqual(snapshot.items, [marker()])
  assert.equal(store.read({ work_order_id: OTHER_ORDER }).kind, 'unavailable')
  assert.equal(backing.getStorageSync(bad), '{bad')
})

test('inaccessible or changing storage cannot claim an empty pending inbox', async () => {
  for (const phase of ['directory', 'keys', 'value', 'duplicate', 'too_many']) {
    const { backing } = await persisted()
    const originalDirectory = backing.getStorageInfoSync, originalRead = backing.getStorageSync
    let calls = 0
    if (phase === 'directory') backing.getStorageInfoSync = () => { throw new Error('storage unavailable') }
    if (phase === 'duplicate') backing.getStorageInfoSync = () => ({ keys: [storageModule.PREFIX + ORDER, storageModule.PREFIX + ORDER] })
    if (phase === 'keys') backing.getStorageInfoSync = () => ++calls > 1 ? { keys: [] } : originalDirectory()
    if (phase === 'value') backing.getStorageSync = key => ++calls > 1 ? JSON.stringify({ ...marker(), request_hash: 'c'.repeat(64) }) : originalRead(key)
    if (phase === 'too_many') backing.getStorageInfoSync = () => ({ keys: Array.from({ length: 1001 }, (_, i) => storageModule.PREFIX + i) })
    const snapshot = storeFor(backing).listPending(PERSON)
    assert.equal(snapshot.kind, 'unavailable', phase); assert.deepEqual(snapshot.items, [])
    assert.ok(backing.data.has(storageModule.PREFIX + ORDER))
  }
})

test('a failed persistence still warns after no record was created', async () => {
  const backing = storage(); backing.setStorageSync = () => {}
  const store = storeFor(backing)
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  assert.equal(store.listPending(PERSON).kind, 'partial')
})

test('a noncanonical storage alias blocks that exact order without hiding unrelated records', async () => {
  const { store, backing } = await persisted()
  const aliased = { ...marker(), work_order_id: 'abcdefab-0000-4000-8000-000000000002' }
  backing.setStorageSync(storageModule.PREFIX + aliased.work_order_id.toUpperCase(), JSON.stringify(aliased))
  const snapshot = store.listPending(PERSON)
  assert.equal(snapshot.kind, 'partial'); assert.deepEqual(snapshot.items, [marker()])
  assert.equal(store.read(aliased).kind, 'unavailable')
  await assert.rejects(store.withLease(aliased, lease => lease.persist(aliased)))
  assert.equal(backing.data.size, 2)
})

function sealed() {
  return { schema_version:'1.0', lookup_status:'sealed_not_executed', command:null, seal:{
    seal_id:OPERATION, work_order_id:ORDER, operator_person_id:PERSON, operation_type:'occupy',
    request_id:TRACE, request_hash:marker().request_hash, sealed_at:'2026-09-12T08:00:00Z'
  } }
}

test('sealed result must match the complete original coordinate before recovery clears it', async () => {
  for (const field of ['work_order_id','operator_person_id','operation_type','request_id','request_hash','sealed_at','command']) {
    const { store } = await persisted(), value = sealed()
    if (field === 'command') value.command = confirmed().command
    else value.seal[field] = 'invalid'
    await assert.rejects(recoverPending({ api:{ request:async()=>value }, store, workOrderId:ORDER, personId:PERSON, authorize:async()=>'same' }))
    assert.equal(store.read(anchor).kind, 'valid')
  }
  const { store } = await persisted()
  const result = await recoverPending({ api:{ request:async()=>sealed() }, store, workOrderId:ORDER, personId:PERSON, authorize:async()=>'same' })
  assert.equal(result.status,'sealed'); assert.equal(store.read(anchor).kind,'missing')
})

test('seal reads first then sends one confirmed no-replay request and proves its result with GET', async () => {
  const { store } = await persisted(), calls = []
  let reads = 0
  const api = {
    async request(path, options) { calls.push({path,...options}); return ++reads === 1 ? {schema_version:'1.0',lookup_status:'not_observed',command:null} : sealed() },
    async postSealNoReplay(path, data, options) { calls.push({path,data,...options}); return {} }
  }
  const result = await sealPending({api,store,workOrderId:ORDER,personId:PERSON,authorize:async()=>'same',confirm:async()=>true})
  assert.equal(result.status,'sealed'); assert.equal(store.read(anchor).kind,'missing')
  assert.equal(calls.length,3); assert.equal(calls[0].method,'GET'); assert.equal(calls[2].method,'GET')
  assert.equal(calls[1].path,calls[0].path+'/seal'); assert.equal(calls[1].requestId,TRACE)
  assert.deepEqual(calls[1].data,{operator_person_id:PERSON,request_hash:marker().request_hash})
  assert.equal('idempotency_key' in calls[1].data,false)
})

test('original proof bypasses seal confirmation and a posting race returns confirmed stock', async () => {
  for (const mode of ['already','race']) {
    const { store } = await persisted(); let posts=0, confirms=0, reads=0
    const result = await sealPending({store,workOrderId:ORDER,personId:PERSON,authorize:async()=>'same',confirm:async()=>{confirms++;return true},api:{
      async request(){return mode==='race' && ++reads===1 ? {schema_version:'1.0',lookup_status:'not_observed',command:null} : confirmed()},
      async postSealNoReplay(){posts++;return confirmed()}
    }})
    assert.equal(result.status,'confirmed'); assert.equal(posts,mode==='race'?1:0); assert.equal(confirms,posts)
    assert.equal(store.read(anchor).kind,'missing')
  }
})

test('cancelled confirmation, permission drift, timeout and absent final proof retain the request', async () => {
  for (const mode of ['cancel','authority','timeout','not_observed','post_authority']) {
    const { store } = await persisted(); let posts=0, auth=0
    const work=sealPending({store,workOrderId:ORDER,personId:PERSON,
      authorize:async()=> ++auth >= (mode==='post_authority'?4:3) && ['authority','post_authority'].includes(mode)?'changed':'same',
      confirm:async()=>mode!=='cancel',api:{
        async request(){return {schema_version:'1.0',lookup_status:'not_observed',command:null}},
        async postSealNoReplay(){posts++;if(mode==='timeout')throw new Error('timeout');return sealed()}
      }})
    if(mode==='cancel')assert.equal((await work).status,'cancelled')
    else if(mode==='not_observed')assert.equal((await work).status,'pending')
    else await assert.rejects(work)
    assert.equal(posts,['cancel','authority'].includes(mode)?0:1)
    assert.equal(store.read(anchor).kind,'valid')
  }
})
