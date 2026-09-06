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
    loadIdentityNoReplay: async () => ({ person_id: sentinel.person_id, authorization_version: 7 }),
    loadAccessNoReplay: async () => ({ can_read: true, can_read_allocation_options: true, person_id: sentinel.person_id, authorization_version: 7 }),
    allocationCommandStatusNoReplay: async () => ({ schema_version: '1.0', lookup_status: 'not_observed', command: null }),
    detailNoReplay: async () => { throw new Error('detail must not be read') }
  }
  const result = await recovery.recover(sentinel, adapter)
  assert.equal(result.status, 'pending')
  assert.equal(result.detail, null)
})

test('confirmed allocation with a different source projection remains blocked', async () => {
  const adapter = {
    loadIdentityNoReplay: async () => ({ person_id: sentinel.person_id, authorization_version: 7 }),
    loadAccessNoReplay: async () => ({ can_read: true, can_read_allocation_options: true, person_id: sentinel.person_id, authorization_version: 7 }),
    allocationCommandStatusNoReplay: async () => ({ schema_version: '1.0', lookup_status: 'confirmed', command: {
      request_id: sentinel.request_id, allocation_id: '50000000-0000-4000-8000-000000000001', allocation_no: 'AL-TEST',
      request_version: 4, current_request_version: 4, revision_id: '60000000-0000-4000-8000-000000000001', revision_no: 1,
      request_line_id: sentinel.request_line_id, source_stock_account_id: sentinel.source_stock_account_id,
      source_balance_version: 99, source_ledger_cursor: 9, allocated_qty: '1.000', allocation_status: 'allocated',
      request_status: 'approved', idempotency_replayed: true, state_axes: {
        request_status: 'approved', allocation_status: 'allocated', reservation_status: 'not_reserved',
        outbound_status: 'not_started', shipment_status: 'not_started', logistics_signature_status: 'not_signed',
        oam_receipt_status: 'not_occurred', personal_inbound_status: 'not_started', notification_status: 'not_started', reconciliation_status: 'not_started'
      }
    } }),
    detailNoReplay: async () => { throw new Error('detail must not be read') }
  }
  await assert.rejects(recovery.recover(sentinel, adapter), /锚点不一致/)
})

test('recovery rejects adapters without explicit no-replay methods', async () => {
  await assert.rejects(recovery.recover(sentinel, {
    loadIdentity: async () => ({ person_id: sentinel.person_id, authorization_version: 7 }),
    loadAccess: async () => ({ can_read: true, can_read_allocation_options: true }),
    allocationCommandStatus: async () => ({ schema_version: '1.0', lookup_status: 'not_observed', command: null }),
    detail: async () => ({})
  }), /no-replay/)
})

test('factory sends identity, access, status and detail as noRefresh reads', async () => {
  const calls = []
  const transport = { request: async (path, options) => {
    calls.push({ path, options })
    if (path === '/auth/me') return { person_id: sentinel.person_id, authorization_version: 7 }
    if (path === '/access/context') return { person_id: sentinel.person_id, authorization_version: 7, can_read: true, can_read_allocation_options: true }
    return { schema_version: '1.0', lookup_status: 'not_observed', command: null }
  } }
  const adapter = recovery.createMaterialRequestAllocationRecoveryAdapter({ person_id: sentinel.person_id, authorization_version: 7 }, transport)
  await adapter.loadIdentityNoReplay(); await adapter.loadAccessNoReplay(); await adapter.allocationCommandStatusNoReplay(sentinel.trace_request_id); await adapter.detailNoReplay(sentinel.request_id)
  assert.equal(calls.length, 4)
  for (const call of calls) assert.equal(call.options.noRefresh, true)
})
