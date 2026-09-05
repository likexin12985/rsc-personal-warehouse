const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const contract = require('../utils/opening-start-options')
const { createPreparationController } = require('../utils/opening-start-preparation')
const id = (n) => `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const USER = { person_id: id(1), authorization_version: 7, name: '管理员', employee_no: 'E-1',
  organization_code: 'HQ', organization_name: '总部', account_status: 'active', employment_status: 'active',
  access_mode: 'active', role_codes: ['admin'] }
const ACTOR = { person_id: USER.person_id, authorization_version: USER.authorization_version }
function deferred() { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
function rows(stage) {
  return [0, 1].map((n) => {
    if (stage === 'regions') return { region_org_id: id(10 + n), code: `R${n}`, name: `区域${n}`, province_code: null }
    if (stage === 'asset-owners') return { owner_org_id: id(20 + n), code: `A${n}`, name: `资产组织${n}` }
    if (stage === 'locations') return { location_id: id(30 + n), code: `L${n}`, name: `库位${n}`, location_type: 'region',
      physical_owner_org_id: id(10), physical_owner_name: '物理组织', custodian_person_id: null, custodian_name: null }
    return { person_id: id(40 + n), assignee_user_id: `counter-${n}`, name: `执行人${n}` }
  })
}
function page(context, after, limit, override) {
  const field = context.stage === 'regions' ? 'region_org_id' : context.stage === 'asset-owners' ? 'owner_org_id' : context.stage === 'locations' ? 'location_id' : 'person_id'
  const eligible = (override || rows(context.stage)).filter((row) => after === null || row[field] > after)
  const items = eligible.slice(0, limit)
  const { stage: ignored, ...anchors } = context
  return contract.validateOptionPage({ ...anchors, schema_version: '1.0', start_ready: false,
    control_evidence_status: 'control_evidence_not_evaluated', items,
    [context.stage === 'assignees' ? 'next_after_person_id' : 'next_after_id']: eligible.length > limit ? items[items.length - 1][field] : null }, context, after, limit)
}
function fixture(limit = 50) {
  const state = { user: USER, view: null, publications: [], reads: [], identityReads: 0, next: null, overrides: null }
  const controller = createPreparationController({ limit, currentIdentity: () => state.user,
    publish(view) { state.view = view; state.publications.push(view) },
    adapter: {
      async identity(expected, live) { state.identityReads += 1; assert.ok(live()); assert.equal(contract.actorKey(expected), contract.actorKey({ person_id: state.user.person_id, authorization_version: state.user.authorization_version })); return expected },
      async options(context, after, count) {
        state.reads.push({ context, after, count })
        if (state.next) { const next = state.next; state.next = null; await next.promise }
        return page(context, after, count, state.overrides)
      }
    }
  })
  controller.activate(ACTOR)
  return { state, controller }
}
async function allLayers(f) {
  await f.controller.toggle()
  for (const stage of contract.STAGES.slice(0, 3)) await f.controller.select(stage, '1')
  await f.controller.select('assignees', '1')
}

test('four-layer preparation is collapsed until requested and keeps owner/custodian/assignee separate', async () => {
  const f = fixture()
  assert.equal(f.state.reads.length, 0)
  assert.equal(f.state.view.expanded, false)
  await allLayers(f)
  assert.deepEqual(f.state.reads.map((call) => call.context.stage), contract.STAGES)
  assert.equal(f.state.identityReads, 8)
  assert.equal(f.state.view.startReady, false)
  assert.equal(f.state.view.controlEvidenceStatus, 'control_evidence_not_evaluated')
  assert.deepEqual(f.state.view.summary, { assetOwnerName: '资产组织0', physicalOwnerName: '物理组织',
    custodianName: '未指定保管人（区域仓）', assigneeName: '执行人0' })
})
for (const stage of contract.STAGES) {
  test(`${stage}: native placeholder clears the selection without choosing or loading any item`, async () => {
    const f = fixture()
    await allLayers(f)
    const index = contract.STAGES.indexOf(stage)
    assert.equal(f.state.view.columns[index].pickerValue, 1)
    const reads = f.state.reads.length
    await f.controller.select(stage, '0')
    assert.equal(f.state.view.columns.length, index + 1)
    assert.equal(f.state.view.columns[index].pickerValue, 0)
    assert.equal(f.state.view.columns[index].selectedName, '')
    assert.deepEqual(f.state.view.columns[index].pickerItems[0], { display: '请选择' })
    assert.equal(f.state.reads.length, reads)
    await f.controller.select(stage, '2')
    assert.equal(f.state.view.columns[index].pickerValue, 2)
    assert.match(f.state.view.columns[index].selectedName, /1$/)
  })
  test(`${stage}: refresh clears selected value and descendants before the read completes`, async () => {
    const f = fixture()
    await allLayers(f)
    const delay = deferred()
    f.state.next = delay
    const pending = f.controller.refresh(stage)
    await Promise.resolve()
    const index = contract.STAGES.indexOf(stage)
    assert.equal(f.state.view.columns.length, index + 1)
    assert.equal(f.state.view.columns[index].selectedName, '')
    assert.equal(f.state.view.columns[index].pickerValue, 0)
    assert.deepEqual(f.state.view.columns[index].pickerItems[0], { display: '请选择' })
    assert.equal(f.state.view.columns[index].loading, true)
    delay.resolve()
    await pending
    assert.equal(f.state.view.columns.length, index + 1)
    assert.equal(f.state.view.columns[index].selectedName, '')
    assert.equal(f.state.view.columns[index].pickerValue, 0)
  })
  test(`${stage}: pagination clears selection/descendants and extends strictly after displayed cursor`, async () => {
    const f = fixture(1)
    await allLayers(f)
    const before = f.state.reads.length
    await f.controller.more(stage)
    const index = contract.STAGES.indexOf(stage)
    assert.equal(f.state.reads.length, before + 1)
    assert.equal(f.state.view.columns.length, index + 1)
    assert.equal(f.state.view.columns[index].items.length, 2)
    assert.equal(f.state.view.columns[index].selectedName, '')
    assert.equal(f.state.view.columns[index].pickerValue, 0)
    assert.equal(f.state.view.columns[index].hasMore, false)
    assert.equal(f.state.reads[f.state.reads.length - 1].after, f.state.view.columns[index].items[0].id)
  })
  test(`${stage}: failed read discards every old label and cannot expose server details`, async () => {
    const f = fixture()
    await allLayers(f)
    const delay = deferred()
    f.state.next = delay
    const pending = f.controller.refresh(stage)
    await Promise.resolve()
    delay.reject(new Error('private upstream details'))
    await pending
    assert.equal(f.state.view.allowed, false)
    assert.deepEqual(f.state.view.columns, [])
    assert.equal(f.state.view.summary, null)
    assert.equal(JSON.stringify(f.state.view).includes('private'), false)
  })
}
for (const terminal of ['hide', 'collapse', 'identity', 'version']) {
  test(`${terminal}: revoke an in-flight service-context read lease and ignore its late resolution`, async () => {
    const f = fixture()
    const delay = deferred()
    f.state.next = delay
    const pending = f.controller.toggle()
    await Promise.resolve()
    if (terminal === 'hide') f.controller.hide()
    else if (terminal === 'collapse') await f.controller.toggle()
    else f.state.user = { ...USER, [terminal === 'identity' ? 'person_id' : 'authorization_version']: terminal === 'identity' ? id(99) : 8 }
    const identityCount = f.state.identityReads
    delay.resolve()
    await pending
    assert.equal(f.state.identityReads, identityCount)
    assert.deepEqual(f.state.view.columns, [])
    assert.equal(f.state.view.summary, null)
  })
}
test('old completion cannot overwrite a new actor activated while its GET was pending', async () => {
  const f = fixture()
  const delay = deferred()
  f.state.next = delay
  const pending = f.controller.toggle()
  await Promise.resolve()
  f.controller.hide()
  f.state.user = { ...USER, person_id: id(99), authorization_version: 8 }
  f.controller.activate({ person_id: id(99), authorization_version: 8 })
  await f.controller.toggle()
  const afterNewIdentity = JSON.stringify(f.state.view)
  delay.resolve()
  await pending
  assert.equal(JSON.stringify(f.state.view), afterNewIdentity)
  assert.equal(f.state.reads[f.state.reads.length - 1].context.actor_person_id, id(99))
})
test('a delayed lower-level response cannot restore descendants after an upstream refresh', async () => {
  const f = fixture()
  await f.controller.toggle()
  const delay = deferred()
  f.state.next = delay
  const pending = f.controller.select('regions', '1')
  await Promise.resolve()
  await f.controller.refresh('regions')
  const before = JSON.stringify(f.state.view)
  delay.resolve()
  await pending
  assert.equal(JSON.stringify(f.state.view), before)
  assert.equal(f.state.view.columns.length, 1)
})
test('duplicate read clicks share a single live invocation and fabricated picker values are ignored', async () => {
  const f = fixture()
  await f.controller.toggle()
  for (const bad of [-1, 'bad', '1.5', 3, 999, true]) await f.controller.select('regions', bad)
  assert.equal(f.state.reads.length, 1)
  const delay = deferred()
  f.state.next = delay
  const pending = f.controller.refresh('regions')
  await Promise.resolve()
  await f.controller.refresh('regions')
  assert.equal(f.state.reads.length, 2)
  delay.resolve(); await pending
})
test('personal location cannot display a different returned assignee', async () => {
  const f = fixture()
  await f.controller.toggle(); await f.controller.select('regions', 1)
  f.state.overrides = [{ ...rows('locations')[0], location_type: 'personal', custodian_person_id: id(99), custodian_name: '保管工程师' }]
  await f.controller.select('asset-owners', 1)
  f.state.overrides = null
  await f.controller.select('locations', 1)
  assert.equal(f.state.view.allowed, false)
  assert.deepEqual(f.state.view.columns, [])
})

test('existing registered page alone receives the preparation UI, without release/config/storage/start expansion', () => {
  const root = path.resolve(__dirname, '..')
  const source = fs.readFileSync(path.join(root, 'pages/formal-stocktakes/index.js'), 'utf8')
  const markup = fs.readFileSync(path.join(root, 'pages/formal-stocktakes/index.wxml'), 'utf8')
  const helpers = ['opening-start-options.js', 'opening-start-preparation.js'].map((file) => fs.readFileSync(path.join(root, 'utils', file), 'utf8')).join('\n')
  assert.match(source, /onHide\(\)/)
  assert.match(source, /onUnload\(\)/)
  assert.match(source, /this\.resetPreparation\(\)/)
  assert.match(markup, /尚未评估省级控制库存，不可启动/)
  assert.match(markup, /资产所有组织/)
  assert.match(markup, /库位物理归属/)
  assert.match(markup, /range="\{\{item\.pickerItems\}\}"/)
  assert.match(markup, /value="\{\{item\.pickerValue\}\}"/)
  for (const forbidden of ['setStorageSync', 'removeStorageSync', 'getApp(', 'ensureLogin(', '.post(', 'createIdempotencyKey', 'createRequestId']) assert.equal(helpers.includes(forbidden), false)
  for (const forbidden of ['bindtap="start', 'bindtap="create', 'bindtap="submit']) assert.equal(markup.includes(forbidden), false)
  const app = require('../app.json')
  assert.equal(app.pages.includes('pages/formal-stocktakes/index'), true)
  assert.equal(app.pages.some((page) => page.includes('preparation')), false)
  assert.deepEqual(require('../utils/release-config'), { API_BASE_URL: '', PC_ORIGIN: '' })
})

function mountedPage() {
  const files = ['../utils/api', '../utils/session', '../utils/opening-start-options',
    '../utils/opening-start-preparation', '../pages/formal-stocktakes/index']
  const saved = files.map((file) => { const key = require.resolve(file); return [key, require.cache[key]] })
  const oldGlobals = { wx: global.wx, Page: global.Page, getApp: global.getApp }
  const state = { user: USER, sessionUser: USER, requests: [], existingListUserWrites: 0, next: null, login: true, updates: 0 }
  const access = () => ({ person_id: state.user.person_id, authorization_version: state.user.authorization_version,
    role_codes: state.user.role_codes, account_status: state.user.account_status,
    employment_status: state.user.employment_status, access_mode: state.user.access_mode,
    assignments: [{ assignment_id: id(9), role_code: state.user.role_codes[0],
      scope_type: state.user.role_codes[0] === 'admin' ? 'national' : 'person',
      scope_id: state.user.role_codes[0] === 'admin' ? '*' : state.user.person_id,
      valid_from: '2026-08-01T00:00:00Z', valid_to: null }],
    permissions: [{ resource: 'stocktake', action: 'read', field_code: '' },
      { resource: 'stocktake', action: 'manage', field_code: '' }] })
  const api = {
    async get(path) {
      if (path === '/auth/me') return state.user
      if (path === '/access/context') return access()
      assert.equal(path, '/v1/stocktakes/opening')
      return { schema_version: '1.0', items: [], next_after_id: null }
    },
    async request(path, options) {
      state.requests.push(path)
      assert.deepEqual(options, contract.NO_STORE)
      if (path === '/auth/me') return state.user
      if (path === '/access/context') return access()
      if (state.next) { const delay = state.next; state.next = null; await delay.promise }
      const url = new URL(path, 'https://isolated.invalid')
      const stage = url.pathname.split('/').pop()
      const coordinates = {}
      for (const key of ['region_org_id', 'owner_org_id', 'location_id']) if (url.searchParams.has(key)) coordinates[key] = url.searchParams.get(key)
      const context = contract.preparationContext({ person_id: state.user.person_id, authorization_version: state.user.authorization_version }, stage, coordinates)
      const { stage: ignored, ...anchors } = context
      return { ...anchors, schema_version: '1.0', start_ready: false, control_evidence_status: 'control_evidence_not_evaluated',
        items: rows(stage), [stage === 'assignees' ? 'next_after_person_id' : 'next_after_id']: null }
    }
  }
  for (const [key] of saved) delete require.cache[key]
  for (const [file, exports] of [['../utils/api', api], ['../utils/session', { getUser: () => state.sessionUser, ensureLogin: () => state.login }]]) {
    const key = require.resolve(file); require.cache[key] = { id: key, filename: key, loaded: true, exports }
  }
  global.wx = { showToast() {}, stopPullDownRefresh() {} }
  global.getApp = () => ({ setUser(value) { state.existingListUserWrites += 1; state.sessionUser = value; return true } })
  let definition
  global.Page = (value) => { definition = value }
  require('../pages/formal-stocktakes/index')
  const instance = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)),
    setData(update) { state.updates += 1; Object.assign(this.data, update) } })
  return { instance, state, restore() {
    for (const [key, value] of saved) { if (value) require.cache[key] = value; else delete require.cache[key] }
    for (const [key, value] of Object.entries(oldGlobals)) { if (value === undefined) delete global[key]; else global[key] = value }
  } }
}

test('mounted page opens directory only for verified managers and adds no user-storage write', async () => {
  const f = mountedPage()
  try {
    await f.instance.onShow()
    assert.equal(f.instance.data.canPrepare, true)
    assert.equal(f.state.requests.length, 0)
    const existingWrites = f.state.existingListUserWrites
    await f.instance.togglePreparation()
    assert.equal(f.instance.data.preparation.columns.length, 1)
    assert.equal(f.state.existingListUserWrites, existingWrites)
    await f.instance.selectPreparation({ currentTarget: { dataset: { stage: 'regions' } }, detail: { value: '1' } })
    assert.equal(f.instance.data.preparation.columns.length, 2)
    assert.equal(f.state.existingListUserWrites, existingWrites)
  } finally { f.restore() }
})
for (const lifecycle of ['onHide', 'onUnload']) {
  test(`mounted ${lifecycle} clears all labels and late directory responses never setData`, async () => {
    const f = mountedPage()
    try {
      await f.instance.onShow()
      const delay = deferred(); f.state.next = delay
      const pending = f.instance.togglePreparation()
      for (let n = 0; n < 5; n += 1) await Promise.resolve()
      f.instance[lifecycle]()
      assert.equal(f.instance.data.canPrepare, false)
      assert.deepEqual(f.instance.data.preparation.columns, [])
      const updates = f.state.updates
      const reads = f.state.requests.length
      await f.instance.togglePreparation()
      await f.instance.refreshPreparation({ currentTarget: { dataset: { stage: 'regions' } } })
      delay.resolve(); await pending
      assert.equal(f.state.updates, updates)
      assert.equal(f.state.requests.length, reads)
    } finally { f.restore() }
  })
}
test('onShow after identity change and pull refresh require fresh empty preparation', async () => {
  const f = mountedPage()
  try {
    await f.instance.onShow(); await f.instance.togglePreparation()
    await f.instance.selectPreparation({ currentTarget: { dataset: { stage: 'regions' } }, detail: { value: '1' } })
    f.instance.onHide()
    f.state.user = { ...USER, person_id: id(99), authorization_version: 8 }
    await f.instance.onShow()
    assert.equal(f.instance.data.preparation.expanded, false)
    assert.deepEqual(f.instance.data.preparation.columns, [])
    await f.instance.togglePreparation()
    const pending = f.instance.onPullDownRefresh()
    assert.deepEqual(f.instance.data.preparation.columns, [])
    await pending
    assert.equal(f.instance.data.preparation.expanded, false)
  } finally { f.restore() }
})
test('technician with anomalous manage permission and missing local login never reveal preparation', async () => {
  const f = mountedPage()
  try {
    f.state.user = { ...USER, role_codes: ['technician'] }
    await f.instance.onShow()
    assert.equal(f.instance.data.canPrepare, false)
    assert.equal(f.state.requests.length, 0)
    f.state.login = false
    f.instance.onShow()
    assert.equal(f.instance.data.canPrepare, false)
    assert.deepEqual(f.instance.data.preparation.columns, [])
  } finally { f.restore() }
})
