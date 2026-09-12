const test = require('node:test')
const assert = require('node:assert/strict')
const f = require('./fixtures/my-receipt')
const recovery = require('../utils/my-receipt-recovery-store')
const event = (dataset, value) => ({ currentTarget: { dataset }, detail: { value } })
const posts = h => h.state.calls.filter(call => call.method === 'POST')
async function selectAccepted(h) {
  await h.page.onShow()
  await h.page.scan()
  h.page.chooseSerial(event({ lineId: f.LINE, serialId: f.SERIAL, result: 'accepted' }))
}
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('scan only marks; explicit acceptance persists recovery before one POST and never posts inventory', async () => {
  let h
  h = f.harness({ request: (path, config) => {
    if (config.method !== 'POST') return f.response()
    const stored = h.store.read(h.page.anchors())
    assert.equal(stored.kind, 'valid')
    assert.equal(stored.value.trace_request_id, config.requestId)
    assert.equal(config.noRefresh, true)
    assert.match(config.idempotencyKey, /^wxidem-[a-f0-9]{36}$/)
    return f.result(f.clone(config.data))
  } })
  await h.page.onShow(); await h.page.scan(); await h.page.submit()
  assert.equal(posts(h).length, 0)
  h.page.chooseSerial(event({ lineId: f.LINE, serialId: f.SERIAL, result: 'accepted' }))
  await h.page.submit()
  assert.equal(posts(h).length, 1)
  assert.ok(posts(h)[0].endpoint.endsWith('/my-receipts'))
  assert.equal(h.page.data.state, 'confirmed')
  assert.match(h.page.data.message, /个人仓尚需独立入账/)
  assert.equal(h.store.read(h.page.anchors()).kind, 'missing')
  assert.match(h.state.modals[0].content, /SKU-TEST：合格 1.000 \/ 拒收 0.000/)
})

test('unscanned accepted SN and canceled confirmation do not create requests or recovery markers', async () => {
  const h = f.harness({ modal: config => config.success({ confirm: false }) })
  await h.page.onShow()
  h.page.chooseSerial(event({ lineId: f.LINE, serialId: f.SERIAL, result: 'accepted' }))
  assert.equal(h.page.data.lines[0].selectedAccepted, 0)
  await h.page.scan()
  h.page.chooseSerial(event({ lineId: f.LINE, serialId: f.SERIAL, result: 'accepted' }))
  await h.page.submit()
  assert.equal(h.state.modals.length, 1)
  assert.equal(posts(h).length, 0)
  assert.equal(h.storage.values.size, 0)
})

test('quantity form rejects excess, negative and forbidden decimal precision before confirmation', async () => {
  const raw = f.response()
  Object.assign(raw.lines[0], { tracking_mode: 'none', remaining_serials: [] })
  const h = f.harness({ response: () => f.clone(raw) })
  await h.page.onShow()
  for (const value of ['2', '-1', '0.1', '1e0']) {
    h.page.editQuantity(event({ lineId: f.LINE, field: 'accepted' }, value))
    await h.page.submit()
    assert.equal(posts(h).length, 0)
    assert.equal(h.state.modals.length, 0)
  }
  h.page.editQuantity(event({ lineId: f.LINE, field: 'accepted' }, '1'))
  await h.page.submit()
  assert.equal(posts(h).length, 1)
  assert.deepEqual(posts(h)[0].data.lines[0].accepted_serial_ids, [])
})

test('candidate drift before and after the confirmation dialog stops the command', async () => {
  for (const afterModal of [false, true]) {
    const raw = f.response()
    const h = f.harness({ response: () => f.clone(raw), modal: config => { raw.request_version++; config.success({ confirm: true }) } })
    await selectAccepted(h)
    if (!afterModal) raw.request_version++
    await h.page.submit()
    assert.equal(posts(h).length, 0)
    assert.equal(h.storage.values.size, 0)
    assert.match(h.page.data.message, /已变化/)
    assert.equal(h.state.modals.length, afterModal ? 1 : 0)
  }
})

test('timeout survives restart; unobserved and mismatched replies retain marker; exact recovery uses only GET', async () => {
  let payload
  const h = f.harness({ request: (path, config) => {
    if (config.method !== 'POST') return f.response()
    payload = f.clone(config.data)
    throw Object.assign(new Error('timeout'), { responseReceived: false, status: 0 })
  } })
  await selectAccepted(h); await h.page.submit(); await h.page.submit()
  assert.equal(posts(h).length, 1)
  assert.equal(h.page.data.state, 'pending')
  const marker = h.store.read(h.page.anchors()).value
  h.page.onUnload()
  let response = { schema_version: '1.0', lookup_status: 'not_observed', command: null }
  const restarted = f.harness({ storage: h.storage, request: () => f.clone(response) })
  await restarted.page.onShow()
  assert.equal(restarted.page.data.state, 'pending')
  assert.equal(restarted.state.calls.filter(call => call.endpoint).length, 0)
  await restarted.page.recover()
  assert.equal(restarted.store.read(marker).kind, 'valid')
  response = { schema_version: '1.0', lookup_status: 'confirmed', command: f.result(payload) }
  response.command.lines[0].accepted_qty = '2.000'
  await restarted.page.recover()
  assert.equal(restarted.store.read(marker).kind, 'valid')
  response.command = f.result(payload)
  await restarted.page.recover()
  assert.equal(restarted.page.data.state, 'confirmed')
  assert.equal(restarted.store.read(marker).kind, 'missing')
  for (const call of restarted.state.calls.filter(call => call.endpoint)) {
    assert.equal(call.method, 'GET'); assert.ok(call.endpoint.endsWith('/my-receipts/trace-status'))
    assert.equal(call.header['X-Original-Request-ID'], marker.trace_request_id)
    assert.equal(call.noRefresh, true)
    assert.equal(call.idempotencyKey, undefined)
  }
})

test('structured precommit rejection allows refresh; generic errors and reused commands remain pending', async () => {
  const errors = [
    [{ responseReceived: true, status: 409, code: 'my_receipt_version_conflict', category: 'conflict' }, false],
    [{ responseReceived: true, status: 412, code: 'my_receipt_evidence_unavailable', category: 'precondition_failed' }, false],
    [{ responseReceived: true, status: 409, code: 'my_receipt_trace_reused', category: 'conflict' }, true],
    [{ responseReceived: true, status: 409, code: 'my_receipt_key_reused', category: 'conflict' }, true],
    [{ responseReceived: true, status: 503, code: 'my_receipt_history_invalid', category: 'service_unavailable' }, true],
    [{ responseReceived: true, status: 409 }, true],
    [{ responseReceived: false, status: 409, code: 'my_receipt_version_conflict', category: 'conflict' }, true],
    [{ responseReceived: true, status: 503, code: 'my_receipt_version_conflict', category: 'conflict' }, true]
  ]
  for (const [metadata, pending] of errors) {
    const h = f.harness({ request: (path, config) => { if (config.method === 'POST') throw Object.assign(new Error('rejected'), metadata); return f.response() } })
    await selectAccepted(h); await h.page.submit(); await h.page.submit()
    assert.equal(posts(h).length, 1)
    assert.equal(h.store.read(h.page.anchors()).kind, pending ? 'valid' : 'missing')
    assert.equal(h.page.data.state, pending ? 'pending' : 'error')
  }
})

test('account changes or page hiding in confirmation cannot send the prepared command', async () => {
  for (const hide of [false, true]) {
    let h
    h = f.harness({ modal: config => { if (hide) h.page.onHide(); else h.state.token = 'different-session'; config.success({ confirm: true }) } })
    await selectAccepted(h); await h.page.submit()
    assert.equal(posts(h).length, 0)
    assert.equal(h.storage.values.size, 0)
    assert.equal(h.page.data.lines.length, 0)
  }
})

test('late POST reply after hiding keeps original recovery marker and does not redraw old data', async () => {
  const sent = deferred(), reply = deferred()
  let payload
  const h = f.harness({ request: (path, config) => {
    if (config.method !== 'POST') return f.response()
    payload = f.clone(config.data); sent.resolve(); return reply.promise
  } })
  await selectAccepted(h)
  const running = h.page.submit()
  await sent.promise; h.page.onHide(); reply.resolve(f.result(payload)); await running
  assert.equal(h.store.read(h.page.anchors()).kind, 'valid')
  assert.equal(h.page.data.lines.length, 0)
  assert.equal(h.page.data.receiptNo, '')
  await h.page.onShow()
  assert.equal(h.page.data.state, 'pending')
})

test('another person cannot read or overwrite original pending receipt', async () => {
  const h = f.harness()
  await h.store.withLease(f.marker(), lease => lease.persist(f.marker()))
  h.state.user.person_id = f.REQUEST
  await h.page.onShow(); await h.page.recover(); await h.page.submit()
  assert.equal(h.page.data.state, 'error')
  assert.equal(h.state.calls.filter(call => call.endpoint).length, 0)
  assert.equal(h.store.read(f.marker()).kind, 'valid')
})

test('storage readback failure prevents POST and concurrent page lease prevents a second command', async () => {
  const broken = f.storage(); broken.setStorageSync = () => {}
  const noStorage = f.harness({ storage: broken })
  await selectAccepted(noStorage); await noStorage.page.submit()
  assert.equal(posts(noStorage).length, 0)
  const storage = f.storage(), store = recovery.createStore({ storage, state: { active: new Set(), faults: new Set() } })
  const sent = deferred(), reply = deferred()
  const first = f.harness({ storage, store, request: (path, config) => {
    if (config.method !== 'POST') return f.response()
    sent.resolve(); return reply.promise.then(() => f.result(f.clone(config.data)))
  } })
  const second = f.harness({ storage, store })
  await selectAccepted(first); await selectAccepted(second)
  const running = first.page.submit(); await sent.promise
  await second.page.submit()
  assert.equal(posts(second).length, 0)
  reply.resolve(); await running
  assert.equal(posts(first).length, 1)
})

test('abnormal acceptance uses only the completed receipt-purpose attachment and explicit rejected SN', async () => {
  let uploadOptions, uploadState = { files: [], availableFiles: [], blocking: true, canChoose: false }
  const uploads = { createFormalFileUploadController(options) {
    uploadOptions = options
    assert.equal(options.purpose, 'receipt_exception_evidence')
    assert.equal(options.multiple, false)
    return { bind() {}, clear() {}, snapshot: () => uploadState, async select() { options.onChange(uploadState) } }
  } }
  const h = f.harness({ uploads })
  await h.page.onShow()
  h.page.chooseSerial(event({ lineId: f.LINE, serialId: f.SERIAL, result: 'rejected' }))
  h.page.changeCondition(event({ lineId: f.LINE }, '5'))
  await h.page.submit(); assert.equal(posts(h).length, 0)
  await h.page.evidenceAction(event({ lineId: f.LINE, action: 'select' }))
  await h.page.submit(); assert.equal(posts(h).length, 0)
  uploadState = { files: [], availableFiles: [{ file_id: f.FILE }], blocking: false, canChoose: false }
  uploadOptions.onChange(uploadState)
  await h.page.submit()
  assert.equal(posts(h).length, 1)
  const line = posts(h)[0].data.lines[0]
  assert.equal(line.condition, 'rejected'); assert.equal(line.accepted_qty, '0.000')
  assert.equal(line.rejected_qty, '1.000'); assert.deepEqual(line.rejected_serial_ids, [f.SERIAL])
  assert.equal(line.exception_evidence_file_id, f.FILE)
  assert.equal(h.page.data.state, 'confirmed')
})
