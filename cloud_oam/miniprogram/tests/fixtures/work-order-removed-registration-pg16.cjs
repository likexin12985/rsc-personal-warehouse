// Actual mini SDK and command/recovery modules, backed by rollback-only PG16 HTTP.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const readline = require('node:readline')
require.cache[require.resolve('../../utils/config')] = { exports: { API_BASE_URL: 'https://fixture.invalid/api' } }
require.cache[require.resolve('../../utils/session')] = { exports: { getToken: () => '' } }
const registration = require('../../utils/work-order-removed-registration-submit')
const paired = require('../../utils/work-order-replacement-submit')
const recovery = require('../../utils/work-order-recovery')
const { validateCompletion } = require('../../utils/work-order-completion-contract')
const { createStore } = require('../../utils/work-order-recovery-store')
const replies = readline.createInterface({ input: process.stdin })[Symbol.asyncIterator]()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
async function read() { const next = await replies.next(); assert.equal(next.done, false); return JSON.parse(next.value) }
async function main() {
  const fixture = await read(), records = new Map()
  const storage = { getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key) }
  const restart = () => createStore({ storage, state: { active: new Set(), faults: new Set() } })
  let store = restart(), registrationPosts = 0, pairedPosts = 0, seals = 0, reviews = 0, originalTrace
  global.wx = { getRandomValues: () => crypto.randomBytes(18), request: options => {
    const path = new URL(options.url).pathname
    const registering = options.method === 'POST' && path.endsWith('/removed-registrations')
    const replacing = options.method === 'POST' && path.endsWith('/material-replacements')
    const sealing = path.endsWith('/seal')
    if (registering || replacing || sealing) {
      const marker = store.read({ work_order_id: fixture.workOrderId })
      assert.equal(marker.kind, 'valid'); assert.equal(records.size, 1)
      assert.equal(marker.value.trace_request_id, options.header['X-Request-ID'])
      assert.equal(marker.value.kind, replacing ? 'work_order_replacement' : 'work_order_removed_registration')
      if (sealing) { seals++; assert.equal(options.header['Idempotency-Key'], undefined) }
      else {
        assert.equal(options.data.request_id, marker.value.trace_request_id)
        assert.equal(options.data.idempotency_key, options.header['Idempotency-Key'])
        if (registering) {
          registrationPosts++; assert.equal(reviews, 1); originalTrace = marker.value.trace_request_id
          assert.equal(marker.value.request_hash, fixture.expectedHash)
          assert.equal(options.data.serial_no, fixture.scan.serial_no); assert.equal(options.data.qr_code, fixture.scan.qr_code)
        } else { pairedPosts++; assert.equal(reviews, 2); assert.notEqual(marker.value.trace_request_id, originalTrace) }
      }
      for (const privateValue of [fixture.scan.qr_code, fixture.scan.serial_no, 'idempotency_key', 'sku_code']) {
        assert.equal([...records.values()].join().includes(privateValue), false)
      }
    } else assert.equal(options.header['Cache-Control'], 'no-store')
    if (fixture.mode === 'sealed' && registering) {
      // Simulate a request that never reached the server; no response is proof.
      options.fail({ errMsg: 'synthetic network loss before dispatch' }); return
    }
    send({ request: { path, method: options.method, data: options.data, headers: options.header } })
    read().then(response => {
      if (fixture.mode === 'lost_response' && registering) options.fail({ errMsg: 'synthetic response loss after server write' })
      else options.success({ statusCode: response.status, data: response.body })
    }, () => options.fail({ errMsg: 'PG16 fixture transport failed' }))
  } }
  const api = require('../../utils/api'), current = async () => {}, authorize = async () => 'same-current-fixture-principal'
  const line = fixture.consumeLine
  const row = { id: 'unknown-removed', basisStockAccountId: line.stock_account_id, condition: fixture.scan.condition_before,
    quantity: '1', scan: fixture.scan, installedSerialId: null }
  const { expected } = await paired.optionsFor({ api, ...fixture, current })
  async function completion() {
    const raw = await api.request(`/v1/work-orders/${fixture.workOrderId}/material-completion-check`,
      { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
    assert.equal(JSON.stringify(raw).includes(fixture.scan.qr_code), false)
    return validateCompletion(raw, expected)
  }
  const initialCheck = await completion()
  assert.deepEqual(initialCheck.blockers.map(row => row.code), ['unreleased_reservation'])
  assert.equal(initialCheck.issues[0].quantity, '1.000')
  await assert.rejects(paired.resolveRemoved({ api, row, expected, current }), error =>
    error.responseReceived === true && error.status === 412 && error.code === 'removed_serial_not_found')
  let result = await registration.submitRegistration({ api, store, ...fixture, row, authorize, confirm: async review => {
    reviews++; assert.equal(records.size, 0); assert.equal(registrationPosts, 0)
    assert.equal(review.kind, 'register_removed'); assert.match(review.description, /库存不会增加/)
    assert.equal(review.rows.length, 1); assert.equal(review.rows[0].basisSku, line.serial_verifications[0].sku_code)
    assert.ok(review.rows[0].basisCondition); assert.equal(typeof review.rows[0].basisLot, 'string')
    assert.equal(review.rows[0].sku, fixture.scan.sku_code); assert.equal(review.rows[0].lot, fixture.scan.lot_no || '')
    assert.deepEqual(review.rows[0].serialNumbers, [fixture.scan.serial_no]); assert.deepEqual(review.pairs, [])
    assert.equal(JSON.stringify(review).includes(fixture.scan.qr_code), false)
    return true
  } })
  if (fixture.mode !== 'direct') {
    assert.equal(result.status, 'pending'); assert.equal(records.size, 1)
    store = restart()
    assert.equal(store.listPending(fixture.personId).items.length, 1)
    await assert.rejects(registration.submitRegistration({ api, store, ...fixture, row, authorize, confirm: async () => true }))
    assert.equal(registrationPosts, 1)
    result = await recovery.recoverPending({ api, store, ...fixture, authorize })
  }
  if (fixture.mode === 'sealed') {
    assert.equal(result.status, 'pending'); assert.equal(records.size, 1)
    result = await recovery.sealPending({ api, store, ...fixture, authorize, confirm: async () => true })
    assert.equal(result.status, 'sealed'); assert.equal(result.seal.operation_type, 'register_removed')
    assert.equal(result.seal.request_id, originalTrace); assert.equal(result.seal.request_hash, fixture.expectedHash)
    assert.deepEqual((await completion()).blockers.map(row => row.code), ['unreleased_reservation'])
  } else {
    assert.equal(result.status, 'confirmed'); assert.equal(result.command.status, 'registered')
    assert.equal(result.command.request_hash, fixture.expectedHash); assert.equal(records.size, 0)
    const registeredSerial = result.command.serial_id
    const registeredCheck = await completion()
    assert.deepEqual(registeredCheck.blockers.map(row => row.code), ['pending_recovery', 'unreleased_reservation'])
    assert.equal(registeredCheck.issues.find(row => row.kind === 'pending_recovery').serials[0].serial_id, registeredSerial)
    const fresh = await paired.optionsFor({ api, ...fixture, current })
    row.candidate = await paired.resolveRemoved({ api, row, expected: fresh.expected, current })
    assert.equal(row.candidate.serial_id, registeredSerial)
    row.installedSerialId = line.serial_ids[0]
    result = await paired.submitReplacement({ api, store, ...fixture,
      drafts: { [line.stock_account_id]: { quantity: line.quantity, serial_verifications: line.serial_verifications } },
      removedDrafts: [row], authorize, confirm: async review => {
        reviews++; assert.equal(pairedPosts, 0); assert.equal(records.size, 0); assert.equal(review.kind, 'replace')
        assert.equal(review.rows.length, 2); assert.equal(review.rows[1].side, '拆回入库')
        assert.equal(review.pairs[0].removed, fixture.scan.serial_no)
        assert.equal(review.pairs[0].installed, line.serial_verifications[0].serial_no)
        assert.equal(JSON.stringify(review).includes(fixture.scan.qr_code), false)
        return true
      } })
    assert.equal(result.status, 'confirmed')
    assert.notEqual(result.command.consume_transaction_id, result.command.recover_transaction_id)
    assert.notEqual(result.command.consume_operation_id, result.command.recover_operation_id)
    const postedCheck = await completion()
    assert.deepEqual(postedCheck.blockers.map(row => row.code), ['pending_return'])
    assert.equal(postedCheck.issues[0].operation_id, result.command.recover_operation_id)
    assert.equal(postedCheck.issues[0].serials[0].serial_id, registeredSerial)
  }
  assert.equal(records.size, 0)
  send({ complete: true, registrationPosts, pairedPosts, seals, reviews })
  await replies.return()
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exit(1) })
