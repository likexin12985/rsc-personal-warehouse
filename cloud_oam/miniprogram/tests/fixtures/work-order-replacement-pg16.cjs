// Synthetic PG16 facts only. This script has no HTTP transport or DB credentials.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const replacement = require('../../utils/work-order-replacement-command')
const removed = require('../../utils/work-order-removed-scan')
const { createStore, validateMarker } = require('../../utils/work-order-recovery-store')
const { recoverPending } = require('../../utils/work-order-recovery')
async function main() {
  const fixture = JSON.parse(fs.readFileSync(0, 'utf8'))
  assert.equal(replacement.requestHash(fixture.input), fixture.expectedHash)
  if (fixture.mode === 'build') {
    process.stdout.write(JSON.stringify({ body: replacement.payload(fixture.input), scan: removed.scanPayload(fixture.scan),
      request_hash: replacement.requestHash(fixture.input) }))
  } else {
    assert.equal(fixture.mode, 'validate')
    const expected = { ...fixture.input, ...fixture.context, scan: fixture.scan }
    replacement.validatePreview(fixture.preview, expected)
    removed.validateRemoved(fixture.removed, expected)
    const line = removed.recoveryLine(fixture.removed, expected, fixture.input.recoverLines[0].quantity)
    assert.deepEqual(line, replacement.payload(fixture.input).recover_lines[0])
    const marker = validateMarker({ v: 1, kind: 'work_order_replacement', operation_type: 'replace',
      authorization_version: fixture.context.authorizationVersion,
      work_order_id: fixture.input.workOrderId, person_id: fixture.input.personId,
      trace_request_id: fixture.trace, request_hash: fixture.expectedHash })
    replacement.validateResult(fixture.result, marker)
    const records = new Map()
    const store = createStore({ state: { active: new Set(), faults: new Set() }, storage: {
      getStorageInfoSync: () => ({ keys: [...records.keys()] }), getStorageSync: key => records.has(key) ? records.get(key) : '',
      setStorageSync: (key, value) => records.set(key, value), removeStorageSync: key => records.delete(key)
    } })
    await store.withLease(marker, lease => lease.persist(marker))
    const recovered = await recoverPending({ store, workOrderId: marker.work_order_id, personId: marker.person_id,
      authorize: async () => 'synthetic-current-authority', api: { async request(path, options) {
        assert.equal(path, `/v1/work-orders/${marker.work_order_id}/material-replacements/by-request/${fixture.trace}`)
        assert.equal(options.method, 'GET'); assert.equal(options.noRefresh, true)
        return fixture.result
      } } })
    assert.equal(recovered.status, 'confirmed'); assert.equal(store.read(marker).kind, 'missing')
    process.stdout.write(JSON.stringify({ validated: true }))
  }
}
main().catch(error => { process.stderr.write(error.stack); process.exitCode = 1 })
