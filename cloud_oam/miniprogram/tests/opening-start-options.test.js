const test = require('node:test')
const assert = require('node:assert/strict')
const contract = require('../utils/opening-start-options')

const id = (n) => `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const actor = { person_id: id(1), authorization_version: 7 }
const user = { ...actor, name: '测试管理员', employee_no: 'E-1', organization_code: 'HQ', organization_name: '测试总部',
  role_codes: ['admin'], account_status: 'active', employment_status: 'active', access_mode: 'active' }
const access = { ...actor, role_codes: ['admin'], account_status: 'active', employment_status: 'active', access_mode: 'active',
  assignments: [{ assignment_id: id(9), role_code: 'admin', scope_type: 'national', scope_id: '*', valid_from: '2026-08-01T00:00:00Z', valid_to: null }],
  permissions: [{ resource: 'stocktake', action: 'read', field_code: '' }, { resource: 'stocktake', action: 'manage', field_code: '' }] }
function context(stage) {
  return contract.preparationContext(actor, stage, stage === 'regions' ? {} : stage === 'asset-owners' ? { region_org_id: id(2) }
    : stage === 'locations' ? { region_org_id: id(2), owner_org_id: id(3) } : { region_org_id: id(2), owner_org_id: id(3), location_id: id(4) })
}
function item(stage, n = 5) {
  if (stage === 'regions') return { region_org_id: id(n), code: 'REG', name: '测试区域', province_code: null }
  if (stage === 'asset-owners') return { owner_org_id: id(n), code: 'ASSET', name: '资产组织' }
  if (stage === 'locations') return { location_id: id(n), code: 'LOC', name: '个人库位', location_type: 'personal',
    physical_owner_org_id: id(2), physical_owner_name: '物理组织', custodian_person_id: id(7), custodian_name: '测试工程师' }
  return { person_id: id(n), assignee_user_id: `formal-user-${n}`, name: '测试执行人' }
}
function wire(stage, items = [item(stage)]) {
  const { stage: ignored, ...anchors } = context(stage)
  return { ...anchors, schema_version: '1.0', start_ready: false, control_evidence_status: 'control_evidence_not_evaluated',
    items, [stage === 'assignees' ? 'next_after_person_id' : 'next_after_id']: null }
}

for (const stage of contract.STAGES) {
  test(`${stage}: exact GET-only transport with no-store/noRefresh and no command coordinates`, async () => {
    const calls = []
    const adapter = contract.createReadAdapter({ request: async (path, options) => { calls.push({ path, options }); return wire(stage) } })
    const page = await adapter.options(context(stage))
    assert.equal(calls.length, 1)
    assert.equal(calls[0].path.split('?')[0], `/v1/stocktakes/opening/start-options/${stage}`)
    assert.deepEqual(calls[0].options, contract.NO_STORE)
    assert.equal(calls[0].path.includes('actor_person_id'), false)
    assert.equal(calls[0].path.includes('task_id'), false)
    assert.deepEqual(page.context, context(stage))
    assert.ok(Object.isFrozen(page) && Object.isFrozen(page.items) && Object.isFrozen(page.items[0]))
  })
  test(`${stage}: all actor, version, selected coordinate and not-ready fields are binding`, () => {
    for (const patch of [{ actor_person_id: id(99) }, { authorization_version: 8 }, { authorization_version: '7' },
      { start_ready: true }, { start_ready: 0 }, { schema_version: '2.0' }, { control_evidence_status: 'validated' },
      { control_qty: '0' }, { task_id: id(99) }, { token: 'private' }]) {
      assert.throws(() => contract.validateOptionPage({ ...wire(stage), ...patch }, context(stage)))
    }
    for (const key of ['region_org_id', 'owner_org_id', 'location_id']) assert.throws(() => contract.validateOptionPage({ ...wire(stage), [key]: id(99) }, context(stage)))
  })
  test(`${stage}: strict ascending last-returned pagination and bounded limits`, async () => {
    const cursor = stage === 'assignees' ? 'next_after_person_id' : 'next_after_id'
    assert.equal(contract.validateOptionPage({ ...wire(stage), [cursor]: id(5) }, context(stage), null, 1).nextAfterId, id(5))
    for (const value of [{ ...wire(stage), [cursor]: id(6) }, { ...wire(stage, []), [cursor]: id(5) },
      wire(stage, [item(stage), item(stage)]), wire(stage, [item(stage, 6), item(stage)])]) {
      assert.throws(() => contract.validateOptionPage(value, context(stage), null, 1))
    }
    assert.throws(() => contract.validateOptionPage(wire(stage), context(stage), id(5)))
    const adapter = contract.createReadAdapter({ request: () => { throw new Error('must not transport') } })
    for (const limit of [0, 101, true, '1', 1.5, NaN]) await assert.rejects(adapter.options(context(stage), null, limit))
  })
  test(`${stage}: bad item identifiers, extra fields and untrusted labels fail closed`, () => {
    const field = stage === 'regions' ? 'region_org_id' : stage === 'asset-owners' ? 'owner_org_id' : stage === 'locations' ? 'location_id' : 'person_id'
    for (const patch of [{ [field]: '00000000-0000-0000-0000-000000000000' }, { [field]: '../escape' },
      { name: '' }, { name: ' padded ' }, { name: 'line\nbreak' }, { name: 'x'.repeat(201) }, { book_qty: 1 }]) {
      assert.throws(() => contract.validateOptionPage(wire(stage, [{ ...item(stage), ...patch }]), context(stage)))
    }
  })
}

test('asset/physical/custodian are not equated, while personal custody is mandatory', () => {
  const page = contract.validateOptionPage(wire('locations'), context('locations'))
  assert.notEqual(page.context.owner_org_id, page.items[0].physicalOwnerId)
  for (const patch of [{ custodian_person_id: null }, { custodian_name: null }, { location_type: 'transit' }]) {
    assert.throws(() => contract.validateOptionPage(wire('locations', [{ ...item('locations'), ...patch }]), context('locations')))
  }
  assert.equal(contract.validateOptionPage(wire('locations', [{ ...item('locations'), location_type: 'region', custodian_person_id: null, custodian_name: null }]), context('locations')).items[0].custodianPersonId, null)
})
test('real user strings are retained and repeated or unsafe user bindings rejected', () => {
  assert.equal(contract.validateOptionPage(wire('assignees'), context('assignees')).items[0].userId, 'formal-user-5')
  for (const value of ['', 'x'.repeat(37), 'user name', '用户', '/name', 'name?query']) assert.throws(() => contract.validateOptionPage(wire('assignees', [{ ...item('assignees'), assignee_user_id: value }]), context('assignees')))
  assert.throws(() => contract.validateOptionPage(wire('assignees', [item('assignees'), { ...item('assignees', 6), assignee_user_id: 'formal-user-5' }]), context('assignees')))
})
test('strict context and cross-page identity, cursor and duplicate rejection', () => {
  assert.throws(() => contract.preparationContext({ ...actor, token: 'private' }, 'regions'))
  assert.throws(() => contract.preparationContext(actor, 'regions', { owner_org_id: id(3) }))
  assert.throws(() => contract.preparationContext(actor, '__proto__'))
  const first = contract.validateOptionPage(wire('assignees'), context('assignees'))
  const next = contract.validateOptionPage(wire('assignees', [item('assignees', 6)]), context('assignees'), id(5))
  assert.equal(contract.mergeOptions(first.items, next, context('assignees'), id(5)).length, 2)
  for (const invalid of [first, { ...next, context: { ...next.context, authorization_version: 8 } },
    { ...next, items: [{ ...next.items[0], userId: 'formal-user-5' }] }]) assert.throws(() => contract.mergeOptions(first.items, invalid, context('assignees'), id(5)))
})
test('only active matching internal managers with read+manage access may prepare', () => {
  assert.deepEqual(contract.preparationActor(user, access), actor)
  for (const length of [80, 81, 100]) assert.deepEqual(contract.preparationActor({ ...user, employee_no: 'E'.repeat(length) }, access), actor)
  assert.throws(() => contract.preparationActor({ ...user, employee_no: 'E'.repeat(101) }, access))
  for (const change of [{ account_status: 'disabled' }, { employment_status: 'left' }, { access_mode: 'restricted_handover' },
    { role_codes: ['technician'] }, { role_codes: ['star_headquarters_approver'] }, { authorization_version: 8 }]) {
    assert.throws(() => contract.preparationActor({ ...user, ...change }, access))
  }
  for (const change of [{ person_id: id(99) }, { authorization_version: 8 }, { permissions: [] },
    { role_codes: ['technician'] }]) assert.throws(() => contract.preparationActor(user, { ...access, ...change }))
})
test('identity revalidation can be revoked between its GETs without another request', async () => {
  const calls = []
  let active = true
  const adapter = contract.createReadAdapter({ request: async (path, options) => { calls.push(path); assert.deepEqual(options, contract.NO_STORE); active = false; return user } })
  await assert.rejects(adapter.identity(actor, () => active))
  assert.deepEqual(calls, ['/auth/me'])
})
test('real api transport 401 causes exactly one simulated GET and no refresh or storage writes', async () => {
  const paths = ['../utils/api', '../utils/config', '../utils/session']
  const saved = paths.map((path) => { const key = require.resolve(path); return [key, require.cache[key]] })
  const originalWx = global.wx
  const requests = []
  try {
    const config = require.resolve('../utils/config')
    const session = require.resolve('../utils/session')
    require.cache[config] = { id: config, filename: config, loaded: true, exports: { API_BASE_URL: 'http://127.0.0.1:8000/api' } }
    require.cache[session] = { id: session, filename: session, loaded: true, exports: {
      getToken: () => 'isolated-fixture-token', beginRefreshAttempt: () => assert.fail('refresh must not start'),
      clearSession: () => assert.fail('directory must not clear storage')
    } }
    delete require.cache[require.resolve('../utils/api')]
    global.wx = { request(options) { requests.push(options); options.success({ statusCode: 401, data: { detail: 'test rejection' } }) },
      setStorageSync() { assert.fail('no storage write') }, removeStorageSync() { assert.fail('no storage delete') } }
    const adapter = contract.createReadAdapter(require('../utils/api'))
    await assert.rejects(adapter.options(context('regions')), (error) => error.status === 401)
    assert.equal(requests.length, 1)
    assert.equal(requests[0].method, 'GET')
    assert.equal(requests[0].header['Cache-Control'], 'no-store')
    assert.equal(requests[0].header.Pragma, 'no-cache')
    assert.equal(requests[0].header['Idempotency-Key'], undefined)
  } finally {
    for (const [key, value] of saved) { if (value) require.cache[key] = value; else delete require.cache[key] }
    if (originalWx === undefined) delete global.wx; else global.wx = originalWx
  }
})
