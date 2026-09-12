// Driven only by the disposable PG16 gate. JSON pipes replace wx transport;
// the production API adapter, command builder and recovery store are unchanged.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const readline = require('node:readline')
require.cache[require.resolve('../../utils/config')] = { exports: { API_BASE_URL: 'https://fixture.invalid/api' } }
require.cache[require.resolve('../../utils/session')] = { exports: { getToken: () => '' } }
const { submitDraft } = require('../../utils/work-order-submit')
const { createStore } = require('../../utils/work-order-recovery-store')
const { validateMaterialOptions } = require('../../utils/work-order-query-contract')
const replies = readline.createInterface({ input: process.stdin })[Symbol.asyncIterator]()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
async function read() {
  const next = await replies.next()
  assert.equal(next.done, false, 'PG16 fixture transport closed')
  return JSON.parse(next.value)
}

async function main() {
  const fixture = await read()
  const records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }),
    getStorageSync: key => records.get(key) || '',
    setStorageSync: (key, value) => records.set(key, value),
    removeStorageSync: key => records.delete(key)
  } })
  let posts = 0, previews = 0, reads = 0, confirmations = 0, activeKind
  global.wx = {
    getRandomValues: () => crypto.randomBytes(18),
    request: options => {
      const path = new URL(options.url).pathname
      const stockPost = options.method === 'POST' && !path.endsWith('/preview')
      if (stockPost) {
        posts++
        const marker = store.read({ work_order_id: fixture.workOrderId })
        assert.equal(marker.kind, 'valid')
        assert.equal(marker.value.operation_type, activeKind)
        assert.equal(marker.value.trace_request_id, options.header['X-Request-ID'])
        assert.equal(options.data.request_id, options.header['X-Request-ID'])
        assert.equal(options.data.idempotency_key, options.header['Idempotency-Key'])
        assert.equal(records.size, 1)
        for (const proof of fixture.proofs) assert.equal([...records.values()].join().includes(proof.qr_code), false)
      } else if (path.endsWith('/preview')) {
        previews++
        assert.equal(records.size, 0)
      } else if (path.includes('/by-request/')) {
        reads++
        assert.equal(options.header['Cache-Control'], 'no-store')
        assert.equal(records.size, 1)
      }
      send({ request: { path, method: options.method, data: options.data, headers: options.header } })
      read().then(response => options.success({ statusCode: response.status, data: response.body }),
        () => options.fail({ errMsg: 'PG16 fixture transport failed' }))
    }
  }
  const api = require('../../utils/api')
  for (const kind of ['occupy', 'release', 'occupy', 'consume']) {
    activeKind = kind
    const raw = await api.request(`/v1/work-orders/${fixture.workOrderId}/material-options`, { method: 'GET', noRefresh: true })
    const options = validateMaterialOptions(raw, fixture.personId, fixture.authorizationVersion, fixture.workOrderId)
    const item = options.items.find(row => row.material_id === fixture.materialId &&
      row.condition_code === fixture.condition && row.availability_bucket === (kind === 'occupy' ? 'available' : 'reserved'))
    assert.ok(item)
    const result = await submitDraft({ api, store, workOrderId: fixture.workOrderId, personId: fixture.personId,
      authorizationVersion: fixture.authorizationVersion, kind,
      drafts: { [item.stock_account_id]: { quantity: '1.000', serial_verifications: fixture.proofs } },
      authorize: async () => 'same-fixture-principal',
      confirm: async review => {
        confirmations++
        assert.equal(records.size, 0)
        assert.equal(posts, confirmations - 1)
        assert.equal(review.kind, kind)
        assert.equal(review.workOrderNo, options.workOrder.work_order_no)
        assert.equal(review.rows.length, 1)
        assert.equal(review.rows[0].quantity, '1.000')
        assert.deepEqual(review.rows[0].serialNumbers, fixture.proofs.map(proof => proof.serial_no))
        for (const proof of fixture.proofs) assert.equal(JSON.stringify(review).includes(proof.qr_code), false)
        return true
      }
    })
    assert.equal(result.status, 'confirmed')
    assert.equal(result.command.operation_type, kind)
    assert.equal(result.command.work_order_id, fixture.workOrderId)
    assert.equal(store.read({ work_order_id: fixture.workOrderId }).kind, 'missing')
    assert.equal(records.size, 0)
  }
  assert.equal(posts, 4); assert.equal(previews, 4); assert.equal(reads, 4); assert.equal(confirmations, 4)
  send({ complete: true, posts, previews, reads, confirmations })
  await replies.return()
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exit(1) })
