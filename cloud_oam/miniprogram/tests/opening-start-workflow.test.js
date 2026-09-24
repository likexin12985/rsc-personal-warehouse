const assert = require('node:assert/strict')
const test = require('node:test')
const core = require('../utils/opening-start-core')
const client = require('../utils/opening-start-client')
const { createStartController } = require('../utils/opening-start-workflow')
const id = (n) => `10000000-0000-4000-8000-${String(n).padStart(12,'0')}`
const actor = { person_id: id(1), authorization_version: 7 }
const user = { ...actor, name: '合成负责人', employee_no: 'SYNTHETIC', organization_code: 'REGION', organization_name: '合成区域',
  account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['provincial_manager'] }
const selection = { actor, region: { id: id(2) }, scope: { owner: '区域一', location: '库位甲', person: '执行人甲',
  scope: { owner_org_id: id(2), location_id: id(4), assignee_user_id: 'user-count', freeze_mode: 'hard' } } }
const marker = { v: 1, kind: 'opening_start', region_org_id: id(2), publication_id: id(3), actor_person_id: id(1), actor_authorization_version: 7, trace_request_id: 'wxreq-'+'a'.repeat(36) }
function world(enabled = true) {
  const rows = new Map(), calls = [], state = { view: null, user, opened: '' }
  const storage = { getStorageInfoSync: () => ({ keys: [...rows.keys()] }), getStorageSync: (key) => rows.get(key) ?? '',
    setStorageSync: (key,value) => rows.set(key,value), removeStorageSync: (key) => rows.delete(key) }
  const store = client.createOpeningStartStore(storage)
  const result = { schema_version: '1.0', task_id: id(5), task_no: 'OPEN-MINI', status: 'counting', cutoff_ledger_cursor: 0, initial_round_id: id(6),
    scope_count: 1, snapshot_line_count: 0, control_line_count: 0, replayed: false }
  const ports = { identity: async () => { calls.push('identity') }, verifySelection: async () => { calls.push('selection') },
    coordinates: () => ({ requestId: marker.trace_request_id, idempotencyKey: 'wxidem-'+'b'.repeat(36) }),
    post: async (input) => { calls.push(['POST',input]); assert.equal(store.read(id(2)).kind,'valid'); return result },
    seal: async () => { throw new Error('unexpected seal') },
    lookup: async (value) => { calls.push(['GET',value]); return { schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: actor.authorization_version,
      region_org_id: id(2), publication_id: id(3), outcome: 'found', automatic_retry_allowed: false, seal: null, result: { ...result, replayed: true } } },
    detail: async () => ({ task_id: id(5), task_no: result.task_no, region_org_id: id(2) }) }
  const adapter = { ports, batches: async () => ({ items: [{ publicationId: id(3), sourceName: '合成来源', capturedAt: '2026-09-20T00:00:00Z', recordCount: 0, validUntil: '2099-09-20T00:00:00Z', isLatest: true }], nextAfterId: null }) }
  const factory = (override = {}) => createStartController({ enabled, store, adapter, currentIdentity: () => state.user, publish: (view) => { state.view = view }, open: (task) => { state.opened = task }, ...override })
  return { rows, storage, store, calls, state, ports, adapter, factory, result }
}
async function prepare(w) {
  const c = w.factory(); await c.select(selection); c.selectBatch(1); c.addScope(); c.edit('taskNo','OPEN-MINI'); return c
}
test('mini creates one multi-scope request, uses latest count-independent publication, and opens verified result', async () => {
  const w = world(); const c = await prepare(w)
  await c.select({ ...selection, scope: { ...selection.scope, scope: { ...selection.scope.scope, location_id: id(40) } } })
  c.edit('freeze',1); c.addScope(); w.result.scope_count = 2
  await Promise.all([c.submit(), c.submit()])
  const posts = w.calls.filter((row) => row[0] === 'POST'); assert.equal(posts.length,1)
  assert.equal(posts[0][1].scopes.length,2); assert.equal(posts[0][1].scopes[1].freeze_mode,'cutoff_replay')
  assert.equal(w.rows.size,0); c.open(); assert.equal(w.state.opened,id(5))
})
test('mini hide after POST preserves marker; reopening under closed release recovers using reads only', async () => {
  const w = world(); const c = await prepare(w); let resolve
  w.ports.post = async (input) => { w.calls.push(['POST',input]); return new Promise((yes) => { resolve = yes }) }
  const running = c.submit(); while (!resolve) await new Promise((yes) => setImmediate(yes))
  c.hide(); resolve(w.result); await running
  assert.equal(w.store.read(id(2)).kind,'valid'); assert.equal(w.state.view.found,'')
  const next = w.factory({ enabled: false }); await next.select(selection); await next.recover()
  assert.equal(w.calls.filter((row) => row[0] === 'POST').length,1); assert.equal(w.rows.size,0); next.open(); assert.equal(w.state.opened,id(5))
})
test('mini not_observed is not a retry authorization and unrelated identities retain the marker', async () => {
  const w = world(false); w.rows.set(core.START_STORAGE_PREFIX+id(2),JSON.stringify(marker))
  const c = w.factory(); await c.select(selection)
  const found = await w.ports.lookup(marker); w.ports.lookup = async () => ({ ...found,outcome:'not_observed',result:null })
  await c.recover(); assert.equal(w.state.view.pending,true); assert.match(w.state.view.message,/待核验/)
  await c.submit(); assert.equal(w.calls.filter((row) => row[0] === 'POST').length,0)
  w.state.user = { ...user,person_id:id(90) }; await c.recover(); assert.equal(w.rows.size,1)
})
test('mini stores neither command bodies nor write keys, and checks true missing keys separately from corrupt empty values', async () => {
  const w = world(); w.rows.set(core.START_STORAGE_PREFIX+id(2),'')
  assert.equal(w.store.read(id(2)).kind,'corrupt'); w.rows.clear()
  await w.store.withRegionLease(id(2),async (lease) => { lease.persist(marker) })
  const raw = w.rows.get(core.START_STORAGE_PREFIX+id(2)); assert.deepEqual(JSON.parse(raw),marker)
  assert.doesNotMatch(raw,/idempotency|scopes|note|quantity|token/)
})
test('mini factories share region coordination; a separate region remains independent', async () => {
  const w = world(), second = client.createOpeningStartStore(w.storage)
  await w.store.withRegionLease(id(2),async () => {
    await assert.rejects(second.withRegionLease(id(2),async () => {}),/其他页面/)
    await second.withRegionLease(id(99),async (lease) => { assert.equal(lease.read().kind,'missing') })
  })
})
test('mini release remains disabled by default and does not register the internal stocktake page', async () => {
  const w = world(false), c = w.factory(); await c.select(selection); await c.submit()
  assert.equal(client.OPENING_START_ENABLED,false); assert.equal(w.state.view.enabled,false); assert.equal(w.calls.length,0)
  assert.equal(require('../app.json').pages.includes('pages/formal-stocktakes/index'),false)
})
test('mini transport always disables refresh and sends exact key coordinates only with the one POST', async () => {
  const calls = []; const transport = { request: async (path,options) => { calls.push([path,options]); return {} }, createRequestId: () => marker.trace_request_id, createIdempotencyKey: () => 'wxidem-'+'b'.repeat(36) }
  const { ports } = client.createOpeningStartPorts(transport)
  await ports.post({ publication_id:id(3) },ports.coordinates()); await ports.lookup(marker)
  assert.equal(calls[0][0],'/v1/stocktakes/opening/from-publication'); assert.equal(calls[0][1].noRefresh,true)
  assert.equal(calls[0][1].requestId,marker.trace_request_id); assert.equal(calls[0][1].idempotencyKey,'wxidem-'+'b'.repeat(36))
  assert.equal(calls[1][1].noRefresh,true); assert.equal(calls[1][1].method,'GET'); assert.equal(calls[1][1].data,undefined)
  assert.equal(calls[1][1].requestId,undefined); assert.equal(calls[1][1].idempotencyKey,undefined)
})

test('mini seal confirmation and unknown delivery require exact independent proof before releasing original marker', async () => {
  const w = world(false); w.rows.set(core.START_STORAGE_PREFIX + id(2),JSON.stringify(marker))
  let sealed = false, confirmations = 0, sends = 0, reads = 0
  const envelope = { schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: 7,
    region_org_id: id(2), publication_id: id(3), automatic_retry_allowed: false, result: null }
  w.ports.lookup = async () => { reads++; return { ...envelope, outcome: sealed ? 'sealed' : 'not_observed', seal: sealed ? {
    seal_id: id(80), actor_person_id: actor.person_id, authorization_version: 7, region_org_id: id(2), publication_id: id(3),
    trace_request_id: marker.trace_request_id, sealed_at: '2026-09-20T12:00:00Z', permanent_nonexecution: true } : null } }
  w.ports.seal = async (original) => { sends++; assert.deepEqual(original,marker); sealed = true; throw new Error('network timeout') }
  const c = w.factory({ confirmSeal: async (message) => { confirmations++; assert.match(message,/永久禁止执行/); return true } })
  await c.select(selection); await Promise.all([c.seal(),c.seal()])
  assert.equal(confirmations,1); assert.equal(sends,1); assert.equal(reads,3); assert.equal(w.rows.size,0)
  assert.match(w.state.view.message,/永久终结并核验/); assert.equal(w.state.view.found,''); c.open(); assert.equal(w.state.opened,'')
  assert.equal(w.calls.filter((item) => item[0] === 'POST').length,0)
})
test('mini absent terminal response and cancellation retain exact original marker', async () => {
  for (const confirmed of [false,true]) {
    const w = world(false); w.rows.set(core.START_STORAGE_PREFIX + id(2),JSON.stringify(marker)); let sends = 0
    w.ports.lookup = async () => ({ schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: 7,
      region_org_id: id(2), publication_id: id(3), outcome: 'not_observed', automatic_retry_allowed: false, result: null, seal: null })
    w.ports.seal = async () => { sends++; return {} }
    const c = w.factory({ confirmSeal: async () => confirmed }); await c.select(selection); await c.seal()
    assert.equal(sends,Number(confirmed)); assert.deepEqual(w.store.read(id(2)),{kind:'valid',value:marker}); assert.equal(w.state.view.pending,true)
  }
})
test('mini seal transport is single-send with deterministic original headers and no raw startup command', async () => {
  const calls = []; const {ports} = client.createOpeningStartPorts({ request: async (...args) => { calls.push(args); return {} } })
  await ports.seal(marker)
  assert.deepEqual(calls,[['/v1/stocktakes/opening/seal-start-command',{method:'POST',noRefresh:true,
    data:{region_org_id:id(2),publication_id:id(3),trace_request_id:marker.trace_request_id},
    requestId:marker.trace_request_id,idempotencyKey:'opening-start-seal:'+marker.trace_request_id}]])
})
