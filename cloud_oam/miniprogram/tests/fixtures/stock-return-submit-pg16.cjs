const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const readline = require('node:readline')
require.cache[require.resolve('../../utils/config')] = { exports: { API_BASE_URL: 'https://fixture.invalid/api' } }
require.cache[require.resolve('../../utils/session')] = { exports: { getToken: () => '' } }
const contract = require('../../utils/stock-return-contract')
const { submitReturn, cancelReturn } = require('../../utils/stock-return-submit')
const { recoverPending, sealPending } = require('../../utils/work-order-recovery')
const { createStore } = require('../../utils/work-order-recovery-store')
const replies = readline.createInterface({ input: process.stdin })[Symbol.asyncIterator]()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
async function read() { const next = await replies.next(); assert.equal(next.done, false); return JSON.parse(next.value) }
async function main() {
  const fixture = await read(), records = new Map()
  const makeStore = () => createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
  } })
  const store = makeStore(); let posts = 0, previews = 0, reads = 0, seals = 0, confirmations = 0
  global.wx = { getRandomValues: () => crypto.randomBytes(18), request: options => {
    const path = new URL(options.url).pathname
    if (options.method === 'POST' && (path.endsWith('/returns') || path.endsWith('/cancellations'))) {
      posts++; assert.equal(confirmations, 1); assert.equal(records.size, 1)
      const marker = store.read({ work_order_id: fixture.workOrderId }).value
      assert.equal(marker.kind, contract.KIND); assert.equal(marker.operation_type, fixture.operationType)
      assert.equal(marker.trace_request_id, options.header['X-Request-ID'])
      assert.equal(options.data.request_id, options.header['X-Request-ID'])
      assert.equal(options.data.idempotency_key, options.header['Idempotency-Key'])
      if (fixture.operationType === 'submit_return') assert.equal(options.data.expected_plan_hash, marker.plan_hash)
      for (const key of ['reason', 'quantity', 'serial_no', 'qr_code', 'idempotency_key', 'token']) assert.ok(![...records.values()].join().includes(key))
    } else if (path.endsWith('/preview')) { previews++; assert.equal(records.size, 0) }
    else if (path.endsWith('/seal')) { seals++; assert.equal(records.size, 1); assert.equal(options.header['Idempotency-Key'], undefined) }
    else if (path.includes('/by-request/')) { reads++; assert.equal(records.size, 1) }
    send({ request: { path, method: options.method, data: options.data, headers: options.header } })
    read().then(response => {
      if (response.transportLost) options.fail({ errMsg: 'synthetic transport loss' })
      else options.success({ statusCode: response.status, data: response.body })
    }, () => options.fail({ errMsg: 'fixture transport failed' }))
  } }
  const api = require('../../utils/api'), authorize = async () => 'same-current-fixture-principal'
  const args = { api, store, ...fixture, drafts: Object.fromEntries(fixture.lines.map(row => [row.source_recovery_line_id,
    { quantity: row.quantity, serial_verifications: row.serial_verifications }])), authorize, confirm: async review => {
    confirmations++; assert.equal(posts, 0); assert.equal(records.size, 0); assert.equal(review.rows.length, 1)
    assert.equal(review.rows[0].serials.length, fixture.tracked ? 1 : 0)
    assert.ok(!JSON.stringify(review).includes('qr_code')); return true
  } }
  let result = await (fixture.operationType === 'submit_return' ? submitReturn : cancelReturn)(args)
  if (fixture.mode !== 'post') {
    assert.equal(result.status, 'pending'); assert.equal(records.size, 1)
    result = await recoverPending({ ...args, store: makeStore() })
    if (fixture.mode === 'lost_before') {
      assert.equal(result.status, 'pending')
      result = await sealPending({ ...args, store: makeStore(), confirm: async () => true })
    }
  }
  assert.equal(result.status, fixture.mode === 'lost_before' ? 'sealed' : 'confirmed')
  assert.equal(posts, 1); assert.equal(previews, fixture.operationType === 'submit_return' ? 1 : 0); assert.equal(records.size, 0)
  send({ complete: true, posts, previews, reads, seals, confirmations, status: result.status })
  await replies.return()
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exit(1) })
