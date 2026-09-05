// Read-only preparation hints, never control inventory or permission to start.
const api = require('./api')
const { projectAccess } = require('./formal-stocktake-adapter')

const LIMIT = 50
const STAGES = Object.freeze(['regions', 'asset-owners', 'locations', 'assignees'])
const ANCHORS = Object.freeze({ regions: [], 'asset-owners': ['region_org_id'],
  locations: ['region_org_id', 'owner_org_id'], assignees: ['region_org_id', 'owner_org_id', 'location_id'] })
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NO_STORE = Object.freeze({ method: 'GET', noRefresh: true,
  header: Object.freeze({ 'Cache-Control': 'no-store', Pragma: 'no-cache' }) })

function fail() {
  const error = new Error('期初盘点准备目录与当前身份、权限或范围不一致，请重新核验')
  error.status = 409
  error.code = 'opening_preparation_unconfirmed'
  throw error
}
function own(value, key) { return Object.prototype.hasOwnProperty.call(value, key) }
function exact(value, fields) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== fields.length || fields.some((key) => !own(value, key))) fail()
  return value
}
function uuid(value) {
  if (typeof value !== 'string' || !UUID.test(value)) fail()
  return value.toLowerCase()
}
function positive(value) {
  if (!Number.isSafeInteger(value) || value < 1) fail()
  return value
}
function label(value, maximum) {
  if (typeof value !== 'string' || !value || value.trim() !== value || value.length > maximum
    || /[\u0000-\u001f\u007f]/.test(value)) fail()
  return value
}
function checkedActor(value) {
  exact(value, ['person_id', 'authorization_version'])
  return Object.freeze({ person_id: uuid(value.person_id), authorization_version: positive(value.authorization_version) })
}
function actorKey(value) { return JSON.stringify(checkedActor(value)) }
function activeIdentity(value) {
  exact(value, ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name',
    'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes'])
  const actor = checkedActor({ person_id: value.person_id, authorization_version: value.authorization_version })
  if (value.account_status !== 'active' || value.employment_status !== 'active' || value.access_mode !== 'active') fail()
  for (const pair of [['name', 160], ['employee_no', 100], ['organization_code', 120], ['organization_name', 240]]) label(value[pair[0]], pair[1])
  if (!Array.isArray(value.role_codes) || !value.role_codes.length
    || new Set(value.role_codes).size !== value.role_codes.length
    || value.role_codes.some((role) => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(role))
    || !value.role_codes.some((role) => role === 'admin' || role === 'provincial_manager')) fail()
  return actor
}
function preparationActor(user, access) {
  const actor = activeIdentity(user)
  const projected = projectAccess(access, actor)
  if (!projected.can_read || !projected.can_manage
    || user.role_codes.slice().sort().join('|') !== access.role_codes.slice().sort().join('|')) fail()
  return actor
}
function preparationContext(actor, stage, coordinates = {}) {
  const identity = checkedActor(actor)
  if (!STAGES.includes(stage)) fail()
  exact(coordinates, ANCHORS[stage])
  const result = { stage, actor_person_id: identity.person_id, authorization_version: identity.authorization_version }
  for (const key of ANCHORS[stage]) result[key] = uuid(coordinates[key])
  return Object.freeze(result)
}
function checkedContext(value) {
  if (!value || !STAGES.includes(value.stage)) fail()
  exact(value, ['stage', 'actor_person_id', 'authorization_version'].concat(ANCHORS[value.stage]))
  const coordinates = {}
  for (const key of ANCHORS[value.stage]) coordinates[key] = value[key]
  return preparationContext({ person_id: value.actor_person_id, authorization_version: value.authorization_version }, value.stage, coordinates)
}
function contextKey(value) { return JSON.stringify(checkedContext(value)) }
function option(stage, value) {
  let result
  if (stage === 'regions') {
    exact(value, ['region_org_id', 'code', 'name', 'province_code'])
    result = { stage, id: uuid(value.region_org_id), code: label(value.code, 80), name: label(value.name, 200),
      provinceCode: value.province_code === null ? null : label(value.province_code, 12) }
  } else if (stage === 'asset-owners') {
    exact(value, ['owner_org_id', 'code', 'name'])
    result = { stage, id: uuid(value.owner_org_id), code: label(value.code, 80), name: label(value.name, 200) }
  } else if (stage === 'locations') {
    exact(value, ['location_id', 'code', 'name', 'location_type', 'physical_owner_org_id',
      'physical_owner_name', 'custodian_person_id', 'custodian_name'])
    if (!['region', 'personal'].includes(value.location_type)) fail()
    const custodianPersonId = value.custodian_person_id === null ? null : uuid(value.custodian_person_id)
    const custodianName = value.custodian_name === null ? null : label(value.custodian_name, 120)
    if ((custodianPersonId === null) !== (custodianName === null)
      || (value.location_type === 'personal' && custodianPersonId === null)) fail()
    result = { stage, id: uuid(value.location_id), code: label(value.code, 100), name: label(value.name, 200),
      locationType: value.location_type, physicalOwnerId: uuid(value.physical_owner_org_id),
      physicalOwnerName: label(value.physical_owner_name, 200), custodianPersonId, custodianName }
  } else {
    exact(value, ['assignee_user_id', 'person_id', 'name'])
    const userId = label(value.assignee_user_id, 36)
    if (!/^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$/.test(userId)) fail()
    result = { stage, id: uuid(value.person_id), userId, name: label(value.name, 120) }
  }
  return Object.freeze(result)
}
function validateOptionPage(value, expected, afterId = null, limit = LIMIT) {
  const context = checkedContext(expected)
  const after = afterId === null ? null : uuid(afterId)
  if (positive(limit) > 100) fail()
  const cursor = context.stage === 'assignees' ? 'next_after_person_id' : 'next_after_id'
  exact(value, ['schema_version', 'actor_person_id', 'authorization_version', 'start_ready',
    'control_evidence_status', 'items', cursor].concat(ANCHORS[context.stage]))
  if (value.schema_version !== '1.0' || value.start_ready !== false
    || value.control_evidence_status !== 'control_evidence_not_evaluated'
    || uuid(value.actor_person_id) !== context.actor_person_id
    || positive(value.authorization_version) !== context.authorization_version) fail()
  for (const key of ANCHORS[context.stage]) if (uuid(value[key]) !== context[key]) fail()
  if (!Array.isArray(value.items) || value.items.length > limit) fail()
  let previous = after
  const users = new Set()
  const items = value.items.map((raw) => {
    const item = option(context.stage, raw)
    if (previous !== null && item.id <= previous) fail()
    previous = item.id
    if (item.stage === 'assignees') {
      if (users.has(item.userId)) fail()
      users.add(item.userId)
    }
    return item
  })
  const nextAfterId = value[cursor] === null ? null : uuid(value[cursor])
  if (nextAfterId !== null && (items.length !== limit || nextAfterId !== previous
    || (after !== null && nextAfterId <= after))) fail()
  return Object.freeze({ context, items: Object.freeze(items), nextAfterId })
}
function mergeOptions(previous, page, expected, after) {
  if (contextKey(page.context) !== contextKey(expected)
    || (after === null && previous.length)
    || (after !== null && (!previous.length || previous[previous.length - 1].id !== after))) fail()
  const result = previous.concat(page.items)
  const users = new Set()
  let last = null
  for (const item of result) {
    if (item.stage !== expected.stage || (last !== null && item.id <= last)) fail()
    last = item.id
    if (item.stage === 'assignees') {
      if (users.has(item.userId)) fail()
      users.add(item.userId)
    }
  }
  if (page.nextAfterId !== null && (!page.items.length || last !== page.nextAfterId)) fail()
  return Object.freeze(result)
}
function createReadAdapter(transport = api) {
  if (!transport || typeof transport.request !== 'function') fail()
  return Object.freeze({
    async identity(expected, canContinue = () => true) {
      if (!canContinue()) fail()
      const user = await transport.request('/auth/me', NO_STORE)
      if (!canContinue()) fail()
      if (actorKey(activeIdentity(user)) !== actorKey(expected)) fail()
      const access = await transport.request('/access/context', NO_STORE)
      if (!canContinue()) fail()
      return preparationActor(user, access)
    },
    async options(expected, afterId = null, limit = LIMIT) {
      const context = checkedContext(expected)
      const after = afterId === null ? null : uuid(afterId)
      if (positive(limit) > 100) fail()
      const params = { limit }
      for (const key of ANCHORS[context.stage]) params[key] = context[key]
      if (after !== null) params[context.stage === 'assignees' ? 'after_person_id' : 'after_id'] = after
      const query = Object.keys(params).map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(params[key])}`).join('&')
      const result = await transport.request(`/v1/stocktakes/opening/start-options/${context.stage}?${query}`, NO_STORE)
      return validateOptionPage(result, context, after, limit)
    }
  })
}
module.exports = { LIMIT, STAGES, NO_STORE, actorKey, activeIdentity, preparationActor,
  preparationContext, contextKey, validateOptionPage, mergeOptions, createReadAdapter }
