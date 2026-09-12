const assert = require('node:assert/strict')
const test = require('node:test')
const command = require('../utils/work-order-command')
const storageModule = require('../utils/work-order-recovery-store')
const { recoverPending, sealPending } = require('../utils/work-order-recovery')
const { submitDraft } = require('../utils/work-order-submit')
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

function pairedMarker() { return storageModule.validateMarker({ ...marker(), kind: 'work_order_replacement', operation_type: 'replace' }) }
function pairedResult(value = pairedMarker()) {
  return { schema_version: '1.0', replacement_id: OPERATION, replacement_no: 'WR-TEST', work_order_id: value.work_order_id,
    consume_operation_id: ORDER, recover_operation_id: OTHER_ORDER, consume_transaction_id: ACCOUNT, recover_transaction_id: MATERIAL,
    status: 'posted', operator_person_id: value.person_id, request_id: value.trace_request_id, request_hash: value.request_hash }
}
async function pairedPersisted() {
  const backing = storage(), store = storeFor(backing)
  await store.withLease(anchor, lease => lease.persist(pairedMarker()))
  return { backing, store }
}

test('ordinary and paired requests use the same per-order durable marker and cannot overwrite each other', async () => {
  const { store, backing } = await pairedPersisted()
  assert.deepEqual(storeFor(backing).listPending(PERSON).items, [pairedMarker()])
  assert.deepEqual(storeFor(backing).listPending(ACCOUNT).items, [])
  assert.equal(backing.data.size, 1)
  assert.equal(JSON.parse(backing.getStorageSync(storageModule.PREFIX + ORDER)).operation_type, 'replace')
  for (const forbidden of ['qr_code', 'serial_no', 'quantity', 'material_id', 'idempotency_key', 'token']) {
    assert.equal(backing.getStorageSync(storageModule.PREFIX + ORDER).includes(forbidden), false)
  }
  let calls = 0
  await assert.rejects(submitDraft({ api: {}, store, workOrderId: ORDER, personId: PERSON, authorizationVersion: 7,
    kind: 'consume', drafts: {}, authorize: async () => { calls++; return 'same' }, confirm: async () => { calls++; return true } }))
  assert.equal(calls, 0); assert.deepEqual(store.read(anchor).value, pairedMarker())
  await assert.rejects(store.withLease(anchor, lease => lease.persist(marker())))
  const ordinary = await persisted()
  await assert.rejects(ordinary.store.withLease(anchor, lease => lease.persist(pairedMarker())))
  await store.withLease({ work_order_id: OTHER_ORDER }, lease => lease.persist({ ...marker(), work_order_id: OTHER_ORDER }))
  assert.equal(backing.data.size, 2)
  for (const value of [{ ...pairedMarker(), operation_type: 'consume' }, { ...marker(), operation_type: 'replace' },
    { ...pairedMarker(), kind: 'unknown' }, { ...pairedMarker(), qr_code: 'forbidden' }]) assert.throws(() => storageModule.validateMarker(value))
})
test('paired recovery after restart reads only exact parent GET and clears only its full proof', async () => {
  const { backing } = await pairedPersisted(), store = storeFor(backing), calls = []
  const result = await recoverPending({ store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'current-version',
    api: { async request(path, options) { calls.push({ path, ...options }); return pairedResult() } } })
  assert.equal(result.status, 'confirmed'); assert.equal(result.command.replacement_no, 'WR-TEST')
  assert.equal(store.read(anchor).kind, 'missing')
  assert.deepEqual(calls, [{ path: `/v1/work-orders/${ORDER}/material-replacements/by-request/${TRACE}`, method: 'GET', noRefresh: true,
    header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } }])
})
test('paired 404, timeout, wrong digest, partial results and lost permission all preserve the original request', async () => {
  for (const mode of ['404', 'timeout', 'hash', 'child', 'post_only', 'authority', 'foreign', 'clear_fault']) {
    const { store, backing } = await pairedPersisted(); let reads = 0, permissions = 0
    if (mode === 'clear_fault') backing.removeStorageSync = () => {}
    await assert.rejects(recoverPending({ store, workOrderId: ORDER, personId: mode === 'foreign' ? ACCOUNT : PERSON,
      authorize: async () => mode === 'authority' ? String(++permissions) : 'same', api: { async request(_, options) {
        reads++; assert.equal(options.method, 'GET')
        if (mode === '404' || mode === 'timeout') throw new Error(mode)
        if (mode === 'child') return confirmed()
        const raw = pairedResult()
        if (mode === 'hash') raw.request_hash = 'b'.repeat(64)
        if (mode === 'post_only') { delete raw.operator_person_id; delete raw.request_id; delete raw.request_hash }
        return raw
      } } }))
    assert.equal(reads, mode === 'foreign' ? 0 : 1)
    assert.equal(store.read(anchor).kind, mode === 'clear_fault' ? 'unavailable' : 'valid')
    assert.ok(backing.data.has(storageModule.PREFIX + ORDER))
  }
})
test('paired recovery holds the same order lease', async () => {
  const { store } = await pairedPersisted(); let resolve, queries = 0
  const pending = recoverPending({ store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same',
    api: { request: async () => { queries++; return new Promise(done => { resolve = done }) } } })
  await new Promise(done => setImmediate(done))
  await assert.rejects(store.withLease(anchor, () => {}))
  assert.equal(queries, 1); resolve(pairedResult()); await pending
})

function parentNotObserved() { return Object.assign(new Error('not observed'), { status: 404, responseReceived: true, code: 'replacement_not_found' }) }
function pairedSealed() {
  return { schema_version: '1.0', lookup_status: 'sealed_not_executed', command: null, seal: {
    seal_id: OPERATION, work_order_id: ORDER, operator_person_id: PERSON, operation_type: 'replace',
    request_id: TRACE, request_hash: pairedMarker().request_hash, sealed_at: '2026-09-13T00:00:00Z' } }
}
test('parent not-observed only permits a confirmed parent seal followed by exact original GET proof', async () => {
  const { store } = await pairedPersisted(), calls = []; let reads = 0
  const api = { async request(path, options) {
    calls.push({ path, ...options }); if (++reads === 1) throw parentNotObserved(); return pairedSealed()
  }, async postSealNoReplay(path, body, options) { calls.push({ path, body, ...options }); return {} } }
  const result = await sealPending({ store, api, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same', confirm: async () => true })
  assert.equal(result.status, 'sealed'); assert.equal(store.read(anchor).kind, 'missing')
  assert.equal(calls.length, 3); assert.equal(calls[0].method, 'GET'); assert.equal(calls[2].method, 'GET')
  assert.equal(calls[1].path, `/v1/work-orders/${ORDER}/material-replacements/by-request/${TRACE}/seal`)
  assert.equal(calls[1].requestId, TRACE); assert.deepEqual(calls[1].body, { operator_person_id: PERSON, request_hash: pairedMarker().request_hash })
})
test('missing parent GET never clears storage and transient or unrelated 404 never dispatches a seal', async () => {
  const valid = await pairedPersisted()
  assert.equal((await recoverPending({ store: valid.store, api: { request: async () => { throw parentNotObserved() } },
    workOrderId: ORDER, personId: PERSON, authorize: async () => 'same' })).status, 'pending')
  assert.equal(valid.store.read(anchor).kind, 'valid')
  for (const change of [e => { e.code = 'route_not_found' }, e => { e.responseReceived = false },
    e => { e.status = 503 }, e => { e.status = 401 }, e => { delete e.code }]) {
    const { store } = await pairedPersisted(), error = parentNotObserved(); change(error); let actions = 0
    await assert.rejects(sealPending({ store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same',
      confirm: async () => { actions++; return true }, api: { request: async () => { throw error }, postSealNoReplay: async () => { actions++ } } }))
    assert.equal(actions, 0); assert.equal(store.read(anchor).kind, 'valid')
  }
})
test('parent seal preserves its marker on cancellation, authority drift, timeout or unproven final result', async () => {
  for (const mode of ['cancel', 'authority', 'timeout', 'post_authority', 'not_observed', 'hash', 'child']) {
    const { store } = await pairedPersisted(); let posts = 0, reads = 0, auth = 0
    const promise = sealPending({ store, workOrderId: ORDER, personId: PERSON,
      authorize: async () => ++auth >= (mode === 'post_authority' ? 4 : 3) && ['authority', 'post_authority'].includes(mode) ? 'changed' : 'same',
      confirm: async () => mode !== 'cancel', api: {
        async request() {
          if (++reads === 1 || mode === 'not_observed') throw parentNotObserved()
          if (mode === 'child') return sealed()
          const value = pairedSealed(); if (mode === 'hash') value.seal.request_hash = 'b'.repeat(64); return value
        }, async postSealNoReplay() { posts++; if (mode === 'timeout') throw new Error('timeout'); return pairedSealed() }
      } })
    if (mode === 'cancel') assert.equal((await promise).status, 'cancelled')
    else if (mode === 'not_observed') assert.equal((await promise).status, 'pending')
    else await assert.rejects(promise)
    assert.equal(posts, ['cancel', 'authority'].includes(mode) ? 0 : 1)
    assert.equal(store.read(anchor).kind, 'valid')
  }
})
test('already posted parent and a posting race return complete stock proof without another stock POST', async () => {
  for (const race of [false, true]) {
    const { store } = await pairedPersisted(); let reads = 0, posts = 0, confirmations = 0
    const result = await sealPending({ store, workOrderId: ORDER, personId: PERSON, authorize: async () => 'same',
      confirm: async () => { confirmations++; return true }, api: {
        async request() { if (race && ++reads === 1) throw parentNotObserved(); return pairedResult() },
        async postSealNoReplay() { posts++; return pairedResult() }
      } })
    assert.equal(result.status, 'confirmed'); assert.equal(result.command.replacement_no, 'WR-TEST')
    assert.equal(posts, race ? 1 : 0); assert.equal(confirmations, posts); assert.equal(store.read(anchor).kind, 'missing')
  }
})

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
