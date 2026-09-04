const assert = require('node:assert/strict')
const test = require('node:test')
const { createOpeningCountRecoveryAdapter, validateOpeningCountCommandStatus,
  validateOpeningCountRecoveredProjection, validateOpeningCountDetail, recoverOpeningCountCommand } = require('../utils/opening-count-recovery')
const { createOpeningCountRecoveryStore, createOpeningCountCoordinator } = require('../utils/opening-count-recovery-store')

const TASK = '10000000-0000-4000-8000-000000000001'
const ROUND = '20000000-0000-4000-8000-000000000002'
const SCOPE = '30000000-0000-4000-8000-000000000003'
const PERSON = '40000000-0000-4000-8000-000000000004'
const COMPLETION = '50000000-0000-4000-8000-000000000005'
const NEXT = '60000000-0000-4000-8000-000000000006'
const REGION = '70000000-0000-4000-8000-000000000007'
const LOCATION = '80000000-0000-4000-8000-000000000008'
const DIFFERENCE = '90000000-0000-4000-8000-000000000009'
const OBSERVATION = 'a0000000-0000-4000-8000-00000000000a'
const MATERIAL = 'b0000000-0000-4000-8000-00000000000b'
const WHEN = '2026-09-05T01:00:00Z'
const identity = Object.freeze({ person_id: PERSON, authorization_version: 7 })
const sentinel = Object.freeze({ v: 1, kind: 'opening_scope_count', task_id: TASK, round_id: ROUND,
  round_no: 1, scope_id: SCOPE, actor_person_id: PERSON, actor_authorization_version: 7,
  trace_request_id: 'wxreq-1234567890abcdef1234567890abcdef1234' })
function clone(value) { return JSON.parse(JSON.stringify(value)) }
function self() { return Object.assign({}, identity, { name: '私密工程师', employee_no: 'PRIVATE-EMP', organization_code: 'ORG',
  organization_name: '测试区域', account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['technician'] }) }
function access() { return Object.assign({}, identity, { account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['technician'],
  assignments: [{ assignment_id: NEXT, role_code: 'technician', scope_type: 'person', scope_id: PERSON, valid_from: '2026-09-01T00:00:00Z', valid_to: null }],
  permissions: ['read', 'count'].map((action) => ({ resource: 'stocktake', action, field_code: '' })) }) }
function status(confirmed = true) { return { schema_version: '1.0', task_id: TASK, round_id: ROUND, scope_id: SCOPE,
  actor_person_id: PERSON, actor_authorization_version: 7, trace_request_id: sentinel.trace_request_id,
  lookup_status: confirmed ? 'confirmed' : 'not_observed', command: confirmed
    ? { completion_id: COMPLETION, completed_at: WHEN, round_no: 1, scope_completed: true, caused_round_submission: true } : null } }
function detail() { return { schema_version: '1.0', task_id: TASK, task_no: 'OPENING-RECOVERY', region_org_id: REGION,
  status: 'submitted', blind_count: true, task_version: 4, deadline: null, cutoff_at: '2026-09-04T00:00:00Z',
  current_round: { round_id: ROUND, round_no: 1, round_type: 'initial', status: 'submitted', started_at: '2026-09-05T00:00:00Z', submitted_at: WHEN },
  evidence_status: 'sealed', scopes: [{ scope_id: SCOPE, scope_no: 1, location_id: LOCATION, owner_org_id: REGION,
    assigned_to_me: true, completion_status: 'completed', completed_at: WHEN, zero_confirmed: true,
    count_line_count: 0, observation_line_count: 0, serial_count: 0, total_counted_qty: '0.000' }],
  observations: [], differences: [], reviews: [], allowed_actions: ['review_region'] } }
function counting(later = false) {
  const row = detail()
  row.status = 'counting'; row.evidence_status = 'counting_hidden'; row.allowed_actions = ['count']
  row.current_round.status = 'counting'; row.current_round.submitted_at = null
  Object.assign(row.scopes[0], { zero_confirmed: null, count_line_count: null, observation_line_count: null, serial_count: null, total_counted_qty: null })
  if (later) {
    row.task_version = 8
    Object.assign(row.current_round, { round_id: NEXT, round_no: 2, round_type: 'recount', started_at: '2026-09-05T02:00:00Z' })
    Object.assign(row.scopes[0], { completion_status: 'pending', completed_at: null })
  }
  return row
}
function observed() {
  const row = detail()
  row.allowed_actions = []
  Object.assign(row.scopes[0], { zero_confirmed: false, observation_line_count: 1, count_line_count: 1, total_counted_qty: '2.000' })
  row.observations = [{ observation_id: OBSERVATION, difference_id: DIFFERENCE, observation_no: 1, scope_id: SCOPE,
    material_identifier_type: 'unknown', material_identifier_raw: '现场标识', condition_code: 'new', availability_bucket: 'available',
    counted_qty: '2.000', verification_status: 'pending_verification', material_id: null, lot_id: null, lot_no_raw: null,
    serial_id: null, serial_no_raw: null, serial_identifier_type: null, disposition: null,
    allowed_dispositions: ['pending_verification', 'requires_recount'] }]
  row.differences = [{ difference_id: DIFFERENCE, difference_no: 1, scope_id: SCOPE, difference_type: 'excess', material_id: null,
    book_qty: '0.000', counted_qty: '2.000', difference_qty: '2.000', affected_qty: '2.000', reason_code: 'opening_pending_verification', evidence_required: true }]
  return row
}
async function fixture(options = {}) {
  const values = new Map()
  const storage = { getStorageInfoSync: () => ({ keys: Array.from(values.keys()) }), getStorageSync: (key) => values.has(key) ? values.get(key) : '',
    setStorageSync: (key, value) => { values.set(key, value) }, removeStorageSync: (key) => { values.delete(key) } }
  const store = createOpeningCountRecoveryStore({ storage, coordinator: createOpeningCountCoordinator() })
  await store.withTaskLease(TASK, async (lease) => { lease.persist(sentinel) })
  let identityReads = 0
  let accessReads = 0
  const calls = []
  const transport = { async request(path, init) {
    calls.push({ path, init })
    if (options.error) throw options.error
    if (path === '/auth/me') return options.identities ? options.identities[Math.min(identityReads++, options.identities.length - 1)] : self()
    if (path === '/access/context') return options.accesses ? options.accesses[Math.min(accessReads++, options.accesses.length - 1)] : access()
    if (path.includes('count-command-status')) return options.status === undefined ? status() : options.status
    if (path === `/v1/stocktakes/opening/${TASK}`) return options.detail === undefined ? detail() : options.detail
    throw new Error('Unexpected test path')
  } }
  const adapter = createOpeningCountRecoveryAdapter(identity, transport)
  return { store, values, storage, calls, adapter,
    run: (guard) => store.withTaskLease(TASK, (lease) => recoverOpeningCountCommand(lease, sentinel, adapter, guard)) }
}
function assertPending(f) { assert.deepEqual(f.store.read(TASK), { kind: 'valid', value: sentinel }) }

test('unknown history remains durable and explicit rechecks never POST or generate new coordinates', async () => {
  const f = await fixture({ status: status(false) })
  const before = Array.from(f.values.entries())
  await assert.rejects(f.run(), /不能据此重新提交/)
  await assert.rejects(f.run(), /不能据此重新提交/)
  assertPending(f)
  assert.deepEqual(Array.from(f.values.entries()), before)
  assert.equal(f.calls.length, 6)
  assert(f.calls.every((call) => call.init.method === 'GET'))
})

test('confirmed history plus current detail and fresh final identity/access clears exactly once', async () => {
  const f = await fixture()
  const result = await f.run()
  assert.deepEqual(result, { command: status().command, detail: detail() })
  assert.deepEqual(f.store.read(TASK), { kind: 'missing' })
  assert(Object.isFrozen(result)); assert(Object.isFrozen(result.command))
  assert.equal(f.calls.length, 6)
  assert.deepEqual(f.calls.map((call) => call.path), ['/auth/me', '/access/context',
    `/v1/stocktakes/opening/${TASK}/rounds/${ROUND}/scopes/${SCOPE}/count-command-status?actor_person_id=${PERSON}&actor_authorization_version=7&trace_request_id=${sentinel.trace_request_id}`,
    `/v1/stocktakes/opening/${TASK}`, '/auth/me', '/access/context'])
  for (const { init } of f.calls) {
    assert.deepEqual(init, { method: 'GET', noRefresh: true, header: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } })
    assert.equal(init.data, undefined); assert.equal(init.idempotencyKey, undefined); assert.equal(init.requestId, undefined)
  }
  assert(!JSON.stringify(result.command).includes('PRIVATE'))
})

test('partial historical completion does not manufacture whole-round submission', async () => {
  const history = status(); history.command.caused_round_submission = false
  const f = await fixture({ status: history, detail: counting() })
  const result = await f.run()
  assert.equal(result.command.caused_round_submission, false)
  assert.equal(result.detail.current_round.status, 'counting')
  assert.equal(result.detail.scopes[0].completion_status, 'completed')
})
for (const sealed of [false, true]) test(`historical sealed=${sealed} recovery preserves a pending later round`, async () => {
  const history = status(); history.command.caused_round_submission = sealed
  const f = await fixture({ status: history, detail: counting(true) })
  const result = await f.run()
  assert.equal(result.detail.current_round.round_no, 2)
  assert.equal(result.detail.scopes[0].completion_status, 'pending')
  assert.equal(result.detail.scopes[0].completed_at, null)
  assert.equal(f.store.read(TASK).kind, 'missing')
})
for (const state of ['posted', 'closed']) test(`historical recovery still validates an independently ${state} task`, async () => {
  const row = detail(); row.status = state; row.allowed_actions = state === 'posted' ? ['close'] : []
  const f = await fixture({ detail: row })
  assert.equal((await f.run()).detail.status, state)
  assert.equal(f.store.read(TASK).kind, 'missing')
})

const badStatuses = [
  ['schema', (row) => { row.schema_version = '2.0' }],
  ...['task_id', 'round_id', 'scope_id', 'actor_person_id'].map((key) => [key, (row) => { row[key] = LOCATION }]),
  ['authorization', (row) => { row.actor_authorization_version = 8 }],
  ['trace', (row) => { row.trace_request_id = 'other-trace-0001' }],
  ['extra key', (row) => { row.idempotency_key = 'must-never-return' }],
  ['missing key', (row) => { delete row.scope_id }],
  ['numeric completed', (row) => { row.command.scope_completed = 1 }],
  ['false completed', (row) => { row.command.scope_completed = false }],
  ['numeric sealed', (row) => { row.command.caused_round_submission = 1 }],
  ['command extra key', (row) => { row.command.quantity = '2.000' }],
  ['command wrong round', (row) => { row.command.round_no = 2 }],
  ['zero completion id', (row) => { row.command.completion_id = '00000000-0000-0000-0000-000000000000' }],
  ['uppercase completion', (row) => { row.command.completion_id = MATERIAL.toUpperCase() }],
  ['unknown status', (row) => { row.lookup_status = 'accepted' }],
  ['unknown with command', (row) => { row.lookup_status = 'not_observed' }]
]
for (const [name, mutate] of badStatuses) test(`malformed ${name} status keeps the durable marker`, async () => {
  const row = status(); mutate(row)
  const f = await fixture({ status: row })
  await assert.rejects(f.run())
  assertPending(f)
  assert.equal(f.calls.length, 3)
})

for (const stamp of ['2026-02-30T01:00:00Z', '2026-09-05T01:00:00+24:00', '2026-09-05T01:00:00+08:60',
  '2026-09-05T01:00:00.1234567Z', '2026-09-05T25:00:00Z', '2026-09-05T01:00:00', '2026-09-05T01:00:60Z']) {
  test(`invalid timestamp ${stamp} cannot confirm a command`, () => {
    const row = status(); row.command.completed_at = stamp
    assert.throws(() => validateOpeningCountCommandStatus(row, sentinel))
  })
}

const badProjections = [
  ['task', (row) => { row.task_id = NEXT }],
  ['scope', (row) => { row.scopes[0].scope_id = NEXT }],
  ['round id', (row) => { row.current_round.round_id = NEXT }],
  ['wrong round type', (row) => { row.current_round.round_type = 'recount' }],
  ['superseded round', (row) => { row.current_round.status = 'superseded' }],
  ['scope time', (row) => { row.scopes[0].completed_at = '2026-09-05T01:00:01Z' }],
  ['sealing time', (row) => { row.current_round.submitted_at = '2026-09-05T01:00:01Z' }],
  ['reverse start time', (row) => { row.current_round.started_at = '2026-09-05T01:00:00.000001Z' }],
  ['sealed evidence in counting state', (row) => { row.status = 'counting'; row.blind_count = false; row.allowed_actions = [] }],
  ['sealed completed summary missing', (row) => { row.scopes[0].total_counted_qty = null }],
  ['missing observations', (row) => { delete row.observations }]
]
for (const [name, mutate] of badProjections) test(`conflicting current ${name} preserves history`, async () => {
  const row = detail(); mutate(row)
  const f = await fixture({ detail: row })
  await assert.rejects(f.run())
  assertPending(f)
})

test('microseconds remain distinct for completion after a non-sealing round submission', async () => {
  const history = status(); history.command.caused_round_submission = false; history.command.completed_at = '2026-09-05T01:00:00.000002Z'
  const row = detail(); row.scopes[0].completed_at = history.command.completed_at; row.current_round.submitted_at = '2026-09-05T09:00:00.000001+08:00'
  const f = await fixture({ status: history, detail: row })
  await assert.rejects(f.run()); assertPending(f)
})
test('microsecond comparison rejects a later round that begins one microsecond too early across zones', async () => {
  const history = status(); history.command.completed_at = '2026-09-05T01:00:00.999999Z'
  const row = counting(true); row.current_round.started_at = '2026-09-05T09:00:00.999998+08:00'
  const f = await fixture({ status: history, detail: row })
  await assert.rejects(f.run()); assertPending(f)
})
test('equal or later instants in a negative timezone preserve microseconds without a large integer runtime', async () => {
  const history = status(); history.command.completed_at = '2026-09-05T01:00:00.999999Z'
  const row = counting(true); row.current_round.started_at = '2026-09-04T18:00:00.999999-07:00'
  const f = await fixture({ status: history, detail: row })
  assert.equal((await f.run()).detail.current_round.round_no, 2)
})
test('a later round cannot reuse the original round id even with a larger round number', async () => {
  const row = counting(true); row.current_round.round_id = ROUND
  const f = await fixture({ detail: row }); await assert.rejects(f.run()); assertPending(f)
})

for (const stage of [0, 1]) for (const field of ['person_id', 'authorization_version']) test(`fresh identity ${field} drift at read ${stage + 1} preserves history`, async () => {
  const identities = [self(), self()]; identities[stage][field] = field === 'person_id' ? NEXT : 8
  const f = await fixture({ identities }); await assert.rejects(f.run()); assertPending(f)
})
for (const stage of [0, 1]) test(`count permission lost at read ${stage + 1} preserves history`, async () => {
  const accesses = [access(), access()]; accesses[stage].permissions = [{ resource: 'stocktake', action: 'read', field_code: '' }]
  const f = await fixture({ accesses }); await assert.rejects(f.run()); assertPending(f)
})
test('changed page generation never clears a fully confirmed history', async () => {
  const f = await fixture(); await assert.rejects(f.run(() => false), /页面已变化/)
  assertPending(f); assert.equal(f.calls.length, 6)
})
test('a corrupt marker is never replaced or queried', async () => {
  const f = await fixture(); const key = Array.from(f.values.keys())[0]; f.values.set(key, '{broken')
  await assert.rejects(f.run()); assert.equal(f.store.read(TASK).kind, 'corrupt')
  assert.equal(f.values.get(key), '{broken'); assert.equal(f.calls.length, 0)
})
test('transport errors never clear history or trigger retries', async () => {
  const error = Object.assign(new Error('network unavailable'), { status: 0 })
  const f = await fixture({ error }); await assert.rejects(f.run(), /network unavailable/)
  assertPending(f); assert.equal(f.calls.length, 1)
})
test('clear failure reports uncertainty and preserves the durable barrier', async () => {
  const f = await fixture(); f.storage.removeStorageSync = () => { throw new Error('storage unavailable') }
  await assert.rejects(f.run()); assert.equal(f.store.read(TASK).kind, 'unavailable')
  assert.equal(f.values.size, 1)
})

test('identity adapter exposes only the captured public identity and drops validated PII', async () => {
  const f = await fixture(); const result = await f.adapter.loadIdentity()
  assert.deepEqual(result, identity); assert(Object.isFrozen(result)); assert(!JSON.stringify(result).includes('PRIVATE'))
})
for (const [field, value] of [['name', ' name '], ['employee_no', 'E\n1'], ['role_codes', ['star_headquarters_approver']],
  ['account_status', 'disabled'], ['authorization_version', '7'], ['name', 'a'.repeat(161)]]) {
  test(`invalid private identity ${field}/${JSON.stringify(value).slice(0, 25)} fails before history query`, async () => {
    const row = self(); row[field] = value
    const f = await fixture({ identities: [row] }); await assert.rejects(f.run()); assertPending(f)
    assert.equal(f.calls.length, 1)
  })
}
test('identity response and adapter configuration must have the exact safe fields', async () => {
  const row = self(); row.session_token = 'unexpected'
  const f = await fixture({ identities: [row] }); await assert.rejects(f.run()); assertPending(f)
  assert.throws(() => createOpeningCountRecoveryAdapter(Object.assign({}, identity, { name: 'extra' }), { request() {} }))
  assert.throws(() => createOpeningCountRecoveryAdapter(identity, { get() {} }))
})
test('wrong persisted identity and unsafe path do not call transport', async () => {
  const calls = []; const adapter = createOpeningCountRecoveryAdapter(identity, { request(...args) { calls.push(args) } })
  assert.throws(() => adapter.commandStatus(Object.assign({}, sentinel, { actor_person_id: NEXT })))
  assert.throws(() => adapter.detail(`${TASK}?unsafe=true`)); assert.deepEqual(calls, [])
})

test('valid pending observation evidence remains independently actionable after historical count confirmation', async () => {
  const row = observed(); const f = await fixture({ detail: row })
  const result = await f.run(); assert.deepEqual(result.detail, row); assert.equal(result.detail.reviews.length, 0)
  assert.equal(result.detail.observations[0].disposition, null)
})
const badObservations = [
  ['duplicate observation', (row) => { row.observations.push(clone(row.observations[0])) }],
  ['foreign scope', (row) => { row.observations[0].scope_id = NEXT }],
  ['foreign difference', (row) => { row.observations[0].difference_id = NEXT }],
  ['wrong quantity', (row) => { row.differences[0].affected_qty = '1.000' }],
  ['wrong reason', (row) => { row.differences[0].reason_code = 'other' }],
  ['unresolved observation allows review', (row) => { row.allowed_actions = ['review_region'] }],
  ['verified observation lacks master', (row) => { row.observations[0].verification_status = 'verified' }],
  ['unknown disposition grant', (row) => { row.observations[0].allowed_dispositions = ['erase'] }],
  ['SN quantity mismatch', (row) => { Object.assign(row.observations[0], { serial_no_raw: 'SN-1', serial_identifier_type: 'serial_no' }) }],
  ['resolved disposition without material', (row) => { row.observations[0].allowed_dispositions = []; row.observations[0].disposition = {
    disposition_id: NEXT, disposition: 'resolved_existing_master', resolved_material_id: null, resolved_lot_id: null, resolved_serial_id: null,
    reason_code: 'MASTER', decided_at: WHEN } }]
]
for (const [name, mutate] of badObservations) test(`invalid current ${name} cannot release the recovery barrier`, async () => {
  const row = observed(); mutate(row)
  const f = await fixture({ detail: row }); await assert.rejects(f.run()); assertPending(f)
})
test('hidden unblinded rounds also prohibit quantity and observation leakage', () => {
  const row = counting(true); row.blind_count = false; row.scopes[0].total_counted_qty = '2.000'
  assert.throws(() => validateOpeningCountDetail(row, TASK))
  row.scopes[0].total_counted_qty = null; row.observations = observed().observations
  assert.throws(() => validateOpeningCountDetail(row, TASK))
})
test('first-count preflight reuses the same enhanced current-detail validator without inventing completion', () => {
  const row = counting(true)
  assert.equal(validateOpeningCountDetail(row, TASK), row)
  assert.equal(row.scopes[0].completion_status, 'pending')
  const confirmed = validateOpeningCountCommandStatus(status(), sentinel)
  assert.equal(validateOpeningCountRecoveredProjection(row, sentinel, confirmed.command), row)
})
