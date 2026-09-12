const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const crypto = require('node:crypto')
const f = require('./fixtures/my-inbound')
const c = require('../utils/my-inbound-contract')
const recovery = require('../utils/my-inbound-recovery-store')
const event = { currentTarget: { dataset: { receiptId: f.RECEIPT } } }
const posts = h => h.state.calls.filter(call => call.method === 'POST')
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('candidate allowlist binds recipient, quantities, serials, posting and pagination', () => {
  assert.equal(c.validateCandidates(f.response(), f.REQUEST, f.PERSON).items[0].status, 'pending')
  for (const change of [r => r.person_id = f.REQUEST, r => r.items[0].detail.source_account_id = f.LINE,
    r => r.items[0].detail.lines[0].accepted_qty = '2.000', r => r.items[0].detail.lines[0].accepted_serials.push(r.items[0].detail.lines[0].accepted_serials[0]),
    r => r.items[0].status = 'posted', r => r.items[0].status = 'no_accepted', r => r.items[0].status = 'blocked',
    r => r.items.push(r.items[0]), r => r.next_after_id = f.REQUEST, r => r.items[0].detail.receipt_request_hash = 'bad']) {
    const raw = f.response(); change(raw); assert.throws(() => c.validateCandidates(raw, f.REQUEST, f.PERSON))
  }
  const blocked = f.response(); blocked.items[0].status = 'blocked'; blocked.items[0].detail = null
  assert.equal(c.validateCandidates(blocked, f.REQUEST, f.PERSON).items[0].status, 'blocked')
  const body = f.body(), digest = crypto.createHash('sha256').update(c.canonical({ request_id: f.REQUEST, person_id: f.PERSON, command: body })).digest('hex')
  assert.equal(c.requestHash(f.REQUEST, f.PERSON, body), digest)
  assert.throws(() => c.requestHash(f.REQUEST, f.PERSON, { ...body, quantity: '1' }))
})

test('explicit confirmation persists only recovery coordinates before one non-replayed POST', async () => {
  let h
  h = f.harness({ request: (path, config) => {
    if (config.method !== 'POST') return f.response()
    const stored = h.store.read(h.page.anchors()); assert.equal(stored.kind, 'valid')
    assert.equal(stored.value.trace_request_id, config.requestId)
    assert.equal(config.noRefresh, true)
    assert.deepEqual(Object.keys(config.data).sort(), ['expected_request_version', 'receipt_id', 'receipt_request_hash'])
    return f.result(config.data)
  } })
  await h.page.onShow(); assert.equal(posts(h).length, 0)
  await h.page.submit(event); await h.page.submit(event)
  assert.equal(posts(h).length, 1); assert.equal(h.page.data.state, 'confirmed')
  assert.equal(h.store.read(h.page.anchors()).kind, 'missing')
  assert.match(h.state.modals[0].content, /合格 1.000/)
  assert.match(h.page.data.message, /入账已完成/)
})

test('read-only, already-posted, rejected-only and unknown rows cannot submit', async () => {
  for (const mode of ['readonly', 'posted', 'no_accepted', 'blocked']) {
    const raw = f.response(), row = raw.items[0]
    if (mode === 'readonly') raw.can_post = false
    else row.status = mode
    if (mode === 'posted') Object.assign(row.detail, { inbound_no: 'INB-OLD', inventory_transaction_id: f.LINE, posted_at: f.NOW })
    if (mode === 'no_accepted') Object.assign(row.detail.lines[0], { accepted_qty: '0.000', rejected_qty: '1.000', accepted_serials: [] })
    if (mode === 'blocked') row.detail = null
    const h = f.harness({ response: () => raw }); await h.page.onShow(); await h.page.submit(event)
    assert.equal(h.page.data.state, 'ready'); assert.equal(posts(h).length, 0)
  }
})

test('cancellation, version drift, account switch or hiding in modal cannot send', async () => {
  for (const mode of ['cancel', 'drift', 'account', 'hide']) {
    const raw = f.response(); let h
    h = f.harness({ response: () => f.clone(raw), modal: config => {
      if (mode === 'drift') raw.request_version++
      if (mode === 'account') h.state.token = 'switched'
      if (mode === 'hide') h.page.onHide()
      config.success({ confirm: mode !== 'cancel' })
    } })
    await h.page.onShow(); await h.page.submit(event)
    assert.equal(posts(h).length, 0); assert.equal(h.storage.values.size, 0)
    if (mode === 'account' || mode === 'hide') assert.equal(h.page.data.items.length, 0)
  }
})

test('timeout survives restart; unobserved and wrong receipt results retain marker; exact GET clears it', async () => {
  const h = f.harness({ request: (path, config) => { if (config.method === 'POST') throw new Error('timeout'); return f.response() } })
  await h.page.onShow(); await h.page.submit(event); await h.page.submit(event)
  assert.equal(posts(h).length, 1); assert.equal(h.page.data.state, 'pending')
  const marker = h.store.read(h.page.anchors()).value
  const rawStored = [...h.storage.values.values()][0]
  for (const text of ['token', 'idempotencyKey', 'SKU-TEST', 'SN-TEST', '1.000']) assert.ok(!rawStored.includes(text))
  h.page.onUnload()
  let lookup = { schema_version: '1.0', lookup_status: 'not_observed', command: null }
  const second = f.harness({ storage: h.storage, request: () => f.clone(lookup) })
  await second.page.onShow(); assert.equal(second.page.data.state, 'pending')
  await second.page.recover(); assert.equal(second.store.read(marker).kind, 'valid')
  lookup = { schema_version: '1.0', lookup_status: 'confirmed', command: { ...f.result(), receipt_id: f.REQUEST } }
  await second.page.recover(); assert.equal(second.store.read(marker).kind, 'valid')
  lookup.command = f.result(); second.state.user.authorization_version++
  await second.page.onShow(); await second.page.recover()
  assert.equal(second.store.read(marker).kind, 'missing'); assert.equal(second.page.data.state, 'confirmed')
  for (const call of second.state.calls.filter(c => c.endpoint)) {
    assert.equal(call.method, 'GET'); assert.match(call.endpoint, /my-inbounds\/trace-status$/)
    assert.equal(call.header['X-Original-Request-ID'], marker.trace_request_id); assert.equal(call.noRefresh, true)
  }
})

test('known precommit rejection clears marker; generic and ambiguous errors retain it', async () => {
  for (const [error, remains] of [[{ responseReceived: true, status: 409, category: 'conflict', code: 'my_inbound_version_conflict' }, false],
    [{ responseReceived: true, status: 412, category: 'precondition_failed', code: 'my_inbound_state_invalid' }, false],
    [{ responseReceived: true, status: 409, category: 'conflict', code: 'my_inbound_key_reused' }, true],
    [{ responseReceived: false, status: 409, category: 'conflict', code: 'my_inbound_version_conflict' }, true], [{ status: 500 }, true]]) {
    const h = f.harness({ request: (path, config) => { if (config.method === 'POST') throw Object.assign(new Error('rejected'), error); return f.response() } })
    await h.page.onShow(); await h.page.submit(event)
    assert.equal(h.store.read(h.page.anchors()).kind, remains ? 'valid' : 'missing')
  }
})

test('late POST response after hide retains durable marker and does not redraw', async () => {
  const sent = deferred(), reply = deferred()
  const h = f.harness({ request: (path, config) => { if (config.method !== 'POST') return f.response(); sent.resolve(); return reply.promise } })
  await h.page.onShow(); const pending = h.page.submit(event); await sent.promise
  h.page.onHide(); reply.resolve(f.result()); await pending
  assert.equal(h.page.data.state, 'idle'); assert.equal(h.store.read(h.page.anchors()).kind, 'valid')
})

test('storage failure stops POST; lease prevents concurrent mutation and validates exact fingerprint', async () => {
  const storage = f.storage(); storage.setStorageSync = () => { throw new Error('disk full') }
  const h = f.harness({ storage }); await h.page.onShow(); await h.page.submit(event)
  assert.equal(posts(h).length, 0)
  assert.throws(() => recovery.validateMarker({ ...f.marker(), request_hash: '0'.repeat(64) }))
  const store = recovery.createStore({ storage: f.storage(), state: { active: new Set(), faults: new Set() } }), marker = f.marker()
  await store.withLease(marker, async lease => {
    lease.persist(marker)
    await assert.rejects(store.withLease(marker, async () => {}))
    assert.throws(() => lease.clearExact({ ...marker, trace_request_id: 'wxreq-'+'c'.repeat(36) }))
    lease.clearExact(marker)
  })
})

test('inbound page is reachable from receiving and registered in mini application', () => {
  const app = JSON.parse(fs.readFileSync(require.resolve('../app.json'), 'utf8'))
  assert.ok(app.pages.includes('pages/formal-my-inbound/index'))
  assert.match(fs.readFileSync(require.resolve('../pages/formal-my-receiving/index.js'), 'utf8'), /pages\/formal-my-inbound\/index/)
})

test('pagination stays on a single request version and returns to first page on drift', async () => {
  const next = '70000000-0000-4000-8000-000000000002'
  let drift = false
  const h = f.harness({ request: path => {
    const raw = f.response()
    if (path.includes('after_id=')) { raw.items[0].receipt_id = next; if (drift) raw.request_version++ }
    else raw.next_after_id = f.RECEIPT
    return raw
  } })
  await h.page.onShow(); assert.equal(h.page.data.hasNext, true)
  await h.page.nextPage(); assert.equal(h.page.data.pageNumber, 2); assert.equal(h.page.data.items[0].receipt_id, next)
  await h.page.previousPage(); assert.equal(h.page.data.pageNumber, 1)
  drift = true; await h.page.nextPage(); assert.equal(h.page.data.state, 'error'); assert.equal(h.page.data.items.length, 0)
})

test('revocation after data load and late reads after hide never expose stale rows', async () => {
  const h = f.harness({ access: (n, access) => n >= 3 ? { ...access, can_read: false } : access })
  await h.page.onShow(); assert.equal(h.page.data.state, 'error'); assert.equal(h.page.data.items.length, 0)
  const fetched = deferred(), response = deferred()
  const late = f.harness({ request: () => { fetched.resolve(); return response.promise } })
  const pending = late.page.onShow(); await fetched.promise; late.page.onHide(); response.resolve(f.response()); await pending
  assert.equal(late.page.data.state, 'idle'); assert.equal(late.page.data.items.length, 0)
})
