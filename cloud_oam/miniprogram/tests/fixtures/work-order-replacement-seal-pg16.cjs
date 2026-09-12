const assert = require('node:assert/strict')
const fs = require('node:fs')
const { validateLookup } = require('../../utils/work-order-replacement-command')
const { createStore, validateMarker } = require('../../utils/work-order-recovery-store')
const { recoverPending } = require('../../utils/work-order-recovery')
async function main() {
  const { response, authorizationVersion } = JSON.parse(fs.readFileSync(0, 'utf8'))
  const seal = response.seal
  const marker = validateMarker({ v: 1, kind: 'work_order_replacement', operation_type: 'replace',
    work_order_id: seal.work_order_id, person_id: seal.operator_person_id, authorization_version: authorizationVersion,
    trace_request_id: seal.request_id, request_hash: seal.request_hash })
  validateLookup(response, marker)
  const records = new Map()
  const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
    getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.has(key) ? records.get(key) : '',
    setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
  } })
  await store.withLease(marker, lease => lease.persist(marker))
  const result = await recoverPending({ store, workOrderId: marker.work_order_id, personId: marker.person_id,
    authorize: async () => 'synthetic-current-authority', api: { async request(path, options) {
      assert.equal(path, `/v1/work-orders/${marker.work_order_id}/material-replacements/by-request/${marker.trace_request_id}`)
      assert.equal(options.method, 'GET'); assert.equal(options.noRefresh, true)
      return response
    } } })
  assert.equal(result.status, 'sealed'); assert.equal(store.read(marker).kind, 'missing')
  process.stdout.write(JSON.stringify({ validated: true }))
}
main().catch(error => { process.stderr.write(error.stack); process.exitCode = 1 })
