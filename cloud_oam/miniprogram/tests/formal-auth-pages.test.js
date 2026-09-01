const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

function loadPage(relativePath, stubs) {
  const savedModules = []
  for (const [modulePath, exports] of Object.entries(stubs)) {
    const resolved = require.resolve(modulePath)
    savedModules.push([resolved, require.cache[resolved]])
    require.cache[resolved] = {
      id: resolved,
      filename: resolved,
      loaded: true,
      exports
    }
  }

  const resolvedPage = require.resolve(relativePath)
  delete require.cache[resolvedPage]
  let definition
  global.Page = (value) => { definition = value }
  require(resolvedPage)

  return {
    definition,
    restore() {
      delete require.cache[resolvedPage]
      for (const [resolved, saved] of savedModules) {
        if (saved) require.cache[resolved] = saved
        else delete require.cache[resolved]
      }
      delete global.Page
    }
  }
}

function pageInstance(definition, data = {}) {
  return Object.assign({}, definition, {
    data: Object.assign({}, definition.data, data),
    setData(update) {
      Object.assign(this.data, update)
    }
  })
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue
    reject = rejectValue
  })
  return { promise, resolve, reject }
}

function unopenedSummary(personId) {
  return {
    schema_version: '1.0',
    projection_status: 'ready',
    opening_balance_status: 'not_established',
    projected_at: null,
    ledger_cursor: 0,
    scopes: [{ scope_type: 'person', scope_id: personId }],
    quantity_status: 'opening_not_established',
    physical_in_stock_qty: null,
    available_qty: null,
    reserved_qty: null,
    committed_qty: null,
    frozen_qty: null,
    physical_in_transit_qty: null,
    expected_supply_qty: null,
    expected_supply_status: 'not_available'
  }
}

function unopenedPersonal(personId) {
  return {
    schema_version: '1.0',
    projection_status: 'ready',
    opening_balance_status: 'not_established',
    projected_at: null,
    ledger_cursor: 0,
    person_id: personId,
    location_id: '20000000-0000-4000-8000-000000000003',
    location_code: 'PERSON-E003',
    location_name: '张工程师个人仓',
    location_status: 'active',
    custody_effective_from: '2026-08-30T00:00:00Z',
    items: []
  }
}

test('login routes by formal role_codes and fails closed on legacy role', (context) => {
  let switchedTo = ''
  let clearCount = 0
  let modalCount = 0
  global.wx = {
    switchTab({ url }) {
      switchedTo = url
    },
    showModal() {
      modalCount += 1
    }
  }
  const loaded = loadPage('../pages/login/index', {
    '../utils/api': {},
    '../utils/session': {
      clearSession() {
        clearCount += 1
      }
    }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  loaded.definition.routeAfterLogin({
    person_id: '00000000-0000-4000-8000-000000000001',
    name: '多角色人员',
    role_codes: ['technician', 'admin']
  })
  assert.equal(switchedTo, '/pages/home/index')
  assert.equal(clearCount, 0)

  loaded.definition.routeAfterLogin({
    person_id: '00000000-0000-4000-8000-000000000002',
    name: '外部审批人',
    role_codes: ['star_headquarters_approver']
  })
  loaded.definition.routeAfterLogin({
    id: 'legacy-user',
    mobile: '13800000000',
    name: '旧角色用户',
    role: 'admin'
  })
  assert.equal(clearCount, 2)
  assert.equal(modalCount, 2)
})

test('one SMS button invocation creates one reusable idempotency key', async (context) => {
  let keyCreationCount = 0
  let posted
  let countdown
  global.wx = {
    showToast() {}
  }
  const loaded = loadPage('../pages/login/index', {
    '../utils/api': {
      createIdempotencyKey() {
        keyCreationCount += 1
        return 'wxidem-one-click-00000001'
      },
      async post(pathname, data, options) {
        posted = { pathname, data, options }
      }
    },
    '../utils/session': {}
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition, {
    mobile: '13800000000',
    options: { sms_interval_seconds: 90 }
  })
  instance.startCountdown = (seconds) => { countdown = seconds }
  await instance.requestCode()

  assert.equal(keyCreationCount, 1)
  assert.deepEqual(posted, {
    pathname: '/auth/sms/request',
    data: { mobile: '13800000000' },
    options: { idempotencyKey: 'wxidem-one-click-00000001' }
  })
  assert.equal(countdown, 90)
})

test('profile renders a verified formal identity without a mobile field', async (context) => {
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000003',
    name: '张工程师',
    employee_no: 'E-003',
    organization_name: '江苏区域公司',
    authorization_version: 11,
    role_codes: ['technician']
  }
  const formalAccess = {
    person_id: formalUser.person_id,
    access_mode: 'active',
    authorization_version: 11,
    role_codes: ['technician'],
    permissions: [
      { resource: 'inventory', action: 'read', field_code: '' },
      { resource: 'material_request', action: 'read', field_code: '' }
    ]
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/profile/index', {
    '../utils/api': {
      get: async (pathname) => {
        if (pathname === '/auth/me') return formalUser
        if (pathname === '/access/context') return formalAccess
        if (pathname === '/v1/inventory/personal/me') {
          return unopenedPersonal(formalUser.person_id)
        }
        throw new Error(`unexpected path ${pathname}`)
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(instance.data.user, formalUser)
  assert.equal(instance.data.identityStatus, '正式访问上下文已验证')
  assert.equal(instance.data.organizationName, '江苏区域公司')
  assert.equal(instance.data.roleLabel, '工程师')
  assert.equal(instance.data.authorizationText, 'v11')
  assert.equal(instance.data.inventoryStatus, '期初待建立')
  assert.equal(instance.data.personalWarehouse.opening_balance_status, 'not_established')
  assert.equal(loaded.definition.openMyInventory, undefined)
  assert.equal(Object.hasOwn(instance.data, 'mobileMasked'), false)

  const template = fs.readFileSync(
    path.resolve(__dirname, '../pages/profile/index.wxml'),
    'utf8'
  )
  assert.match(template, /\{\{identityStatus\}\}/)
  assert.doesNotMatch(template, /user\.mobile|mobileMasked/)
})

test('formal workbench reads only the formal summary and exposes no unopened quantity', async (context) => {
  const calls = []
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000013',
    name: '工作台工程师',
    organization_name: '浙江区域公司',
    authorization_version: 9,
    role_codes: ['technician']
  }
  const formalAccess = {
    person_id: formalUser.person_id,
    access_mode: 'active',
    authorization_version: 9,
    role_codes: ['technician'],
    permissions: [
      { resource: 'inventory', action: 'read', field_code: '' },
      { resource: 'material_request', action: 'read', field_code: '' }
    ]
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      async get(pathname) {
        calls.push(pathname)
        if (pathname === '/auth/me') return formalUser
        if (pathname === '/access/context') return formalAccess
        if (pathname === '/v1/inventory/summary') {
          return unopenedSummary(formalUser.person_id)
        }
        throw new Error(`unexpected path ${pathname}`)
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(calls, ['/auth/me', '/access/context', '/v1/inventory/summary'])
  assert.equal(instance.data.inventoryAccessAllowed, true)
  assert.equal(instance.data.authorizationText, 'v9')
  assert.equal(instance.data.moduleTitle, '期初库存尚未建立')
  assert.equal(instance.data.materialRequestAccessAllowed, true)
  assert.equal(instance.data.inventorySummary.available_qty, null)
  assert.equal(Object.hasOwn(instance.data, 'summary'), false)
  assert.equal(Object.hasOwn(instance.data, 'inventory'), false)
  const homeTemplate = fs.readFileSync(
    path.resolve(__dirname, '../pages/home/index.wxml'),
    'utf8'
  )
  assert.match(homeTemplate, /\/pages\/formal-material-requests\/index/)
})

test('formal workbench exposes the demand entry independently of inventory permission', async (context) => {
  const calls = []
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000113',
    name: '需求工程师',
    organization_name: '江苏区域公司',
    authorization_version: 13,
    role_codes: ['technician']
  }
  const formalAccess = {
    person_id: formalUser.person_id,
    access_mode: 'active',
    authorization_version: 13,
    role_codes: ['technician'],
    permissions: [
      { resource: 'material_request', action: 'read', field_code: '' }
    ]
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      async get(pathname) {
        calls.push(pathname)
        if (pathname === '/auth/me') return formalUser
        if (pathname === '/access/context') return formalAccess
        throw new Error(`unexpected path ${pathname}`)
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(calls, ['/auth/me', '/access/context'])
  assert.equal(instance.data.materialRequestAccessAllowed, true)
  assert.equal(instance.data.inventoryAccessAllowed, false)
  assert.equal(instance.data.moduleTitle, '库存访问已失败关闭')
})

test('formal workbench fails closed for restricted handover access', async (context) => {
  const calls = []
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000014',
    name: '待交接工程师',
    organization_name: '江苏区域公司',
    authorization_version: 12,
    role_codes: ['technician']
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      async get(pathname) {
        calls.push(pathname)
        if (pathname === '/auth/me') return formalUser
        return {
          person_id: formalUser.person_id,
          access_mode: 'restricted_handover',
          authorization_version: 12,
          role_codes: ['technician'],
          permissions: [{ resource: 'inventory', action: 'read', field_code: '' }]
        }
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(calls, ['/auth/me', '/access/context'])
  assert.equal(instance.data.inventoryAccessAllowed, false)
  assert.equal(instance.data.moduleTitle, '库存访问已失败关闭')
  assert.match(instance.data.moduleMessage, /交接能力/)
})

test('identity-context mismatch never starts an inventory request', async (context) => {
  const calls = []
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000015',
    name: '身份甲',
    organization_name: '江苏区域公司',
    authorization_version: 13,
    role_codes: ['technician']
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      async get(pathname) {
        calls.push(pathname)
        if (pathname === '/auth/me') return formalUser
        if (pathname === '/access/context') {
          return {
            person_id: '00000000-0000-4000-8000-000000000099',
            access_mode: 'active',
            authorization_version: 13,
            role_codes: ['technician'],
            permissions: [{ resource: 'inventory', action: 'read', field_code: '' }]
          }
        }
        throw new Error(`unexpected inventory request ${pathname}`)
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  await instance.load()

  assert.deepEqual(calls, ['/auth/me', '/access/context'])
  assert.equal(instance.data.inventoryAccessAllowed, false)
  assert.equal(instance.data.inventorySummary, null)
  assert.match(instance.data.moduleMessage, /身份与授权上下文不一致/)
})

test('failed reload clears stale identity and inventory presentation', async (context) => {
  global.wx = { showToast() {} }
  global.getApp = () => ({ setUser: () => true })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      async get(pathname) {
        if (pathname === '/auth/me') throw new Error('identity unavailable')
        throw new Error('context unavailable')
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition, {
    user: { name: '旧账号' },
    avatarText: '旧',
    roleLabel: '旧角色',
    organizationName: '旧组织',
    authorizationText: 'v1',
    inventorySummary: { available_qty: '999.000' },
    inventoryAccessAllowed: true
  })
  await instance.load()

  assert.equal(instance.data.user, null)
  assert.equal(instance.data.avatarText, '')
  assert.equal(instance.data.roleLabel, '')
  assert.equal(instance.data.organizationName, '')
  assert.equal(instance.data.authorizationText, '未验证')
  assert.equal(instance.data.inventorySummary, null)
  assert.equal(instance.data.inventoryAccessAllowed, false)
})

test('older account load cannot overwrite a newer account result', async (context) => {
  const firstUser = deferred()
  const firstAccess = deferred()
  let authCalls = 0
  let accessCalls = 0
  const installedUsers = []
  const newerUser = {
    person_id: '00000000-0000-4000-8000-000000000017',
    name: '新账号',
    organization_name: '浙江区域公司',
    authorization_version: 17,
    role_codes: ['technician']
  }
  const newerAccess = {
    person_id: newerUser.person_id,
    access_mode: 'active',
    authorization_version: 17,
    role_codes: ['technician'],
    permissions: [{ resource: 'inventory', action: 'read', field_code: '' }]
  }
  global.wx = { showToast() {} }
  global.getApp = () => ({
    setUser(user) {
      installedUsers.push(user)
      return true
    }
  })
  const loaded = loadPage('../pages/home/index', {
    '../utils/api': {
      get(pathname) {
        if (pathname === '/auth/me') {
          authCalls += 1
          return authCalls === 1 ? firstUser.promise : Promise.resolve(newerUser)
        }
        if (pathname === '/access/context') {
          accessCalls += 1
          return accessCalls === 1 ? firstAccess.promise : Promise.resolve(newerAccess)
        }
        if (pathname === '/v1/inventory/summary') {
          return Promise.resolve(unopenedSummary(newerUser.person_id))
        }
        return Promise.reject(new Error(`unexpected path ${pathname}`))
      }
    },
    '../utils/session': { ensureLogin: () => true }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
    delete global.getApp
  })

  const instance = pageInstance(loaded.definition)
  const olderLoad = instance.load()
  const newerLoad = instance.load()
  await newerLoad
  firstUser.resolve({
    person_id: '00000000-0000-4000-8000-000000000016',
    name: '旧账号',
    authorization_version: 16,
    role_codes: ['technician']
  })
  firstAccess.resolve({
    person_id: '00000000-0000-4000-8000-000000000016',
    access_mode: 'active',
    authorization_version: 16,
    role_codes: ['technician'],
    permissions: [{ resource: 'inventory', action: 'read', field_code: '' }]
  })
  await olderLoad

  assert.equal(instance.data.user.name, '新账号')
  assert.equal(instance.data.authorizationText, 'v17')
  assert.equal(instance.data.moduleTitle, '期初库存尚未建立')
  assert.deepEqual(installedUsers, [newerUser])
})

test('SMS and WeChat login responses use the explicit-session barrier', async (context) => {
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000004',
    name: '竞态测试工程师',
    role_codes: ['technician']
  }
  const established = []
  const postedPaths = []
  global.wx = {
    getDeviceInfo() {
      return { brand: 'Test', model: 'Device' }
    },
    switchTab() {},
    showToast() {}
  }
  const loaded = loadPage('../pages/login/index', {
    '../utils/api': {
      async post(pathname) {
        postedPaths.push(pathname)
        return {
          access_token: `${pathname}-access`,
          refresh_token: `${pathname}-refresh`,
          session_id: `${pathname}-session`,
          user: formalUser
        }
      },
      async establishExplicitSession(result) {
        established.push(result)
      }
    },
    '../utils/session': {
      getDeviceId() {
        return 'test-device'
      },
      clearSession() {}
    }
  })
  context.after(() => {
    loaded.restore()
    delete global.wx
  })

  const instance = pageInstance(loaded.definition, {
    mode: 'sms',
    mobile: '13800000000',
    code: '246810'
  })
  await instance.submit()
  instance.getWechatLoginCode = async () => 'wechat-login-code'
  await instance.loginWithWechat('wechat-phone-code')

  assert.deepEqual(postedPaths, [
    '/auth/miniprogram/sms-login',
    '/auth/miniprogram/wechat-login'
  ])
  assert.equal(established.length, 2)
  assert.equal(established[0].access_token, '/auth/miniprogram/sms-login-access')
  assert.equal(established[1].access_token, '/auth/miniprogram/wechat-login-access')
})
