// Exercise the shipped SDK over real API-role PG16 routes, including lost replies.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const readline = require('node:readline')
require.cache[require.resolve('../../utils/config')] = { exports: { API_BASE_URL: 'https://fixture.invalid/api' } }
require.cache[require.resolve('../../utils/session')] = { exports: { getToken: () => '' } }
const reversal = require('../../utils/work-order-reversal-command')
const { optionsFor } = require('../../utils/work-order-replacement-submit')
const { submitReversal } = require('../../utils/work-order-reversal-submit')
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
  const store = makeStore()
  let posts = 0, previews = 0, reads = 0, seals = 0, confirmations = 0
  let intentHash, planHash
  global.wx = {
    getRandomValues: () => crypto.randomBytes(18),
    request: options => {
      const path = new URL(options.url).pathname
      if (options.method === 'POST' && path.endsWith('/material-reversals')) {
        posts++; assert.equal(confirmations, 1); assert.equal(records.size, 1)
        const marker = store.read({ work_order_id: fixture.workOrderId })
        assert.equal(marker.kind, 'valid'); assert.equal(marker.value.kind, reversal.KIND)
        assert.equal(marker.value.request_hash, intentHash); assert.equal(marker.value.plan_hash, planHash)
        assert.equal(marker.value.trace_request_id, options.header['X-Request-ID'])
        assert.equal(options.data.request_id, options.header['X-Request-ID'])
        assert.equal(options.data.idempotency_key, options.header['Idempotency-Key'])
        assert.equal(options.data.expected_plan_hash, planHash)
        for (const name of ['reason', 'quantity', 'children', 'serial_no', 'idempotency_key', 'qr_code', 'token']) assert.equal([...records.values()].join().includes(name), false)
      } else if (path.endsWith('/preview')) { previews++; assert.equal(records.size, 0) }
      else if (path.endsWith('/seal')) { seals++; assert.equal(records.size, 1) }
      else if (path.includes('/by-request/')) { reads++; assert.equal(records.size, 1) }
      send({ request: { path, method: options.method, data: options.data, headers: options.header } })
      read().then(response => {
        if (path.endsWith('/preview') && response.status === 200) { planHash = response.body.plan_hash; intentHash = response.body.request_hash }
        if (response.transportLost) options.fail({ errMsg: 'synthetic lost transport' })
        else options.success({ statusCode: response.status, data: response.body })
      }, () => options.fail({ errMsg: 'PG16 fixture transport failed' }))
    }
  }
  const api = require('../../utils/api'), authorize = async () => 'same-current-fixture-principal'
  const { expected } = await optionsFor({ api, ...fixture, current: async () => {} })
  const raw = await api.request(`/v1/work-orders/${fixture.workOrderId}/material-reversals/originals`, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store' } })
  const candidates = reversal.validateOriginals(raw, expected)
  const selected = candidates.find(row => row.original_operation_id === fixture.selection.original_operation_id && row.original_replacement_id === fixture.selection.original_replacement_id)
  assert.ok(selected && selected.selectable)
  const args = { api, store, ...fixture, selection: { original_operation_id: selected.original_operation_id, original_replacement_id: selected.original_replacement_id }, authorize,
    confirm: async review => {
      confirmations++; assert.equal(posts, 0); assert.equal(records.size, 0); assert.equal(review.kind, 'reverse')
      assert.equal(review.rows.length, fixture.paired ? 2 : 1)
      assert.equal(review.pairs.length, fixture.paired && fixture.tracked ? 1 : 0)
      assert.ok(review.description.includes(fixture.reason)); assert.ok(review.rows.every(row => row.effect))
      if (fixture.tracked) assert.ok(review.rows.every(row => row.serialNumbers.length === 1 && row.serialEffects.length === 1))
      assert.equal(JSON.stringify(review).includes('qr_code'), false)
      return true
    } }
  let result = await submitReversal(args)
  if (fixture.mode !== 'post') {
    assert.equal(result.status, 'pending'); assert.equal(records.size, 1)
    result = await recoverPending({ ...args, store: makeStore() })
    if (fixture.mode === 'lost_before') {
      assert.equal(result.status, 'pending'); assert.equal(records.size, 1)
      result = await sealPending({ ...args, store: makeStore(), confirm: async () => true })
    }
  }
  assert.equal(result.status, fixture.mode === 'lost_before' ? 'sealed' : 'confirmed')
  if (result.status === 'confirmed') assert.equal(result.command.request_hash, reversal.requestHash(args))
  assert.equal(posts, 1); assert.equal(previews, 1); assert.equal(records.size, 0)
  send({ complete: true, posts, previews, reads, seals, confirmations, status: result.status })
  await replies.return()
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exit(1) })
