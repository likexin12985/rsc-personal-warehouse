const assert = require('node:assert/strict')
const test = require('node:test')

const recovery = require('../utils/material-request-allocation-recovery')

const sentinel = {
  v: 1, kind: 'material_request_allocation', trace_request_id: `wxreq-${'a'.repeat(36)}`,
  person_id: '10000000-0000-4000-8000-000000000001', authorization_version: 7,
  request_id: '20000000-0000-4000-8000-000000000001', request_line_id: '30000000-0000-4000-8000-000000000001',
  request_version: 3, source_stock_account_id: '40000000-0000-4000-8000-000000000001',
  allocated_qty: '1.000', source_balance_version: 8, source_ledger_cursor: 9
}

function store(initial = '') {
  let value = initial
  return {
    getStorageSync: () => value,
    setStorageSync: (_, next) => { value = next },
    removeStorageSync: () => { value = '' }
  }
}

test('allocation recovery persists exact anchors and refuses replacement', () => {
  const storage = store()
  assert.equal(recovery.read({ storage }), null)
  recovery.persist(sentinel, { storage })
  assert.deepEqual(recovery.read({ storage }), sentinel)
  assert.throws(() => recovery.persist({ ...sentinel, source_ledger_cursor: 10 }, { storage }), /禁止覆盖/)
  assert.throws(() => recovery.clear({ ...sentinel, source_ledger_cursor: 10 }, { storage }), /已变化/)
  recovery.clear(sentinel, { storage })
  assert.equal(recovery.read({ storage }), null)
})

test('not_observed allocation history remains pending', async () => {
  const adapter = {
    loadIdentity: async () => ({ person_id: sentinel.person_id, authorization_version: 7 }),
    loadAccess: async () => ({ can_read: true, can_read_allocation_options: true, person_id: sentinel.person_id, authorization_version: 7 }),
    allocationCommandStatus: async () => ({ schema_version: '1.0', lookup_status: 'not_observed', command: null })
  }
  const result = await recovery.recover(sentinel, adapter)
  assert.equal(result.status, 'pending')
  assert.equal(result.detail, null)
})
