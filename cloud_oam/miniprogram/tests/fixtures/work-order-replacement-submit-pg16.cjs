// The actual production adapter exchanges synthetic HTTP requests with PG16.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const readline = require('node:readline')
require.cache[require.resolve('../../utils/config')] = { exports: { API_BASE_URL: 'https://fixture.invalid/api' } }
require.cache[require.resolve('../../utils/session')] = { exports: { getToken: () => '' } }
const paired = require('../../utils/work-order-replacement-submit')
const { createStore } = require('../../utils/work-order-recovery-store')
const replies = readline.createInterface({ input: process.stdin })[Symbol.asyncIterator]()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
async function read() { const next = await replies.next(); assert.equal(next.done, false); return JSON.parse(next.value) }
async function main() {
  const fixture = await read(), records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
  } })
  let posts = 0, scans = 0, previews = 0, reads = 0, confirmations = 0
  global.wx = {
    getRandomValues: () => crypto.randomBytes(18),
    request: options => {
      const path = new URL(options.url).pathname
      if (options.method === 'POST' && path.endsWith('/material-replacements')) {
        posts++
        assert.equal(confirmations, 1)
        const marker = store.read({ work_order_id: fixture.workOrderId })
        assert.equal(marker.kind, 'valid'); assert.equal(marker.value.kind, 'work_order_replacement')
        assert.equal(marker.value.operation_type, 'replace'); assert.equal(marker.value.request_hash, fixture.expectedHash)
        assert.equal(marker.value.trace_request_id, options.header['X-Request-ID'])
        assert.equal(options.data.request_id, options.header['X-Request-ID'])
        assert.equal(options.data.idempotency_key, options.header['Idempotency-Key'])
        assert.equal(records.size, 1)
        assert.deepEqual(options.data.consume_lines, fixture.consumeLines)
        assert.deepEqual(options.data.recover_lines, fixture.recoverLines)
        assert.deepEqual(options.data.replacement_pairs, fixture.pairs)
        for (const proof of fixture.consumeLines.concat(fixture.recoverLines).flatMap(line => line.serial_verifications)) {
          assert.equal([...records.values()].join().includes(proof.qr_code), false)
        }
      } else if (path.endsWith('/removed-part')) { scans++; assert.equal(records.size, 0) }
      else if (path.endsWith('/preview')) { previews++; assert.equal(records.size, 0) }
      else if (path.includes('/by-request/')) { reads++; assert.equal(records.size, 1) }
      if (!path.endsWith('/material-replacements')) assert.equal(options.header['Cache-Control'], 'no-store')
      send({ request: { path, method: options.method, data: options.data, headers: options.header } })
      read().then(response => options.success({ statusCode: response.status, data: response.body }),
        () => options.fail({ errMsg: 'PG16 fixture transport failed' }))
    }
  }
  const api = require('../../utils/api'), current = async () => {}
  const { expected } = await paired.optionsFor({ api, ...fixture, current })
  const line = fixture.consumeLines[0], recovery = fixture.recoverLines[0]
  const row = { id: 'removed-1', basisStockAccountId: line.stock_account_id, condition: recovery.condition_before,
    quantity: recovery.quantity, scan: fixture.scan, installedSerialId: fixture.pairs.length ? fixture.pairs[0].installed_serial_id : null }
  row.candidate = await paired.resolveRemoved({ api, row, expected, current })
  const result = await paired.submitReplacement({ api, store, ...fixture,
    drafts: { [line.stock_account_id]: { quantity: line.quantity, serial_verifications: line.serial_verifications } }, removedDrafts: [row],
    authorize: async () => 'same-current-fixture-principal', confirm: async review => {
      confirmations++
      assert.equal(posts, 0); assert.equal(records.size, 0); assert.equal(review.kind, 'replace')
      assert.equal(review.rows.length, 2); assert.equal(review.rows[0].side, '投入消耗'); assert.equal(review.rows[1].side, '拆回入库')
      assert.equal(review.rows[1].quantity, recovery.quantity); assert.equal(review.rows[1].basisLineNo, 1)
      assert.deepEqual(review.rows[0].serialNumbers, line.serial_verifications.map(proof => proof.serial_no))
      assert.deepEqual(review.rows[1].serialNumbers, recovery.serial_verifications.map(proof => proof.serial_no))
      assert.equal(review.pairs.length, fixture.pairs.length)
      for (const proof of line.serial_verifications.concat(recovery.serial_verifications)) assert.equal(JSON.stringify(review).includes(proof.qr_code), false)
      return true
    } })
  assert.equal(result.status, 'confirmed'); assert.equal(result.command.request_hash, fixture.expectedHash)
  assert.notEqual(result.command.consume_transaction_id, result.command.recover_transaction_id)
  assert.notEqual(result.command.consume_operation_id, result.command.recover_operation_id)
  assert.equal(records.size, 0); assert.equal(store.read({ work_order_id: fixture.workOrderId }).kind, 'missing')
  send({ complete: true, posts, scans, previews, reads, confirmations })
  await replies.return()
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exit(1) })
