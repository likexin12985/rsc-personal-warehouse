const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { randomFillSync } = require('node:crypto')
const { spawnSync } = require('node:child_process')
const test = require('node:test')

const SESSION_MODULE = require.resolve('../utils/session')
const API_MODULE = require.resolve('../utils/api')
const CONFIG_MODULE = require.resolve('../utils/config')
const APP_MODULE = require.resolve('../app')

const TOKEN_KEY = 'rsc_oam_access_token'
const REFRESH_TOKEN_KEY = 'rsc_oam_refresh_token'
const USER_KEY = 'rsc_oam_user'
const SESSION_KEY = 'rsc_oam_session_id'
const DEVICE_KEY = 'rsc_oam_device_id'
const SENTINEL_KEY = 'rsc_oam_refresh_sentinel'
const SENTINEL_VALUE = 'pending-v1'
const LOGIN_SENTINEL_VALUE = 'explicit-login-pending-v1'
const LOGOUT_SENTINEL_VALUE = 'logout-pending-v1'

const formalUser = {
  person_id: '00000000-0000-4000-8000-000000000099',
  name: '哨兵测试工程师',
  employee_no: 'E-SENTINEL',
  role_codes: ['technician']
}

function resetModules() {
  for (const modulePath of [SESSION_MODULE, API_MODULE, CONFIG_MODULE, APP_MODULE]) {
    delete require.cache[modulePath]
  }
}

function randomValues({ length }) {
  return randomFillSync(new Uint8Array(length))
}

function storageWx(storage, overrides = {}) {
  return Object.assign({
    getRandomValues: randomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync(key) {
      return storage.has(key) ? storage.get(key) : ''
    },
    setStorageSync(key, value) {
      storage.set(key, value)
    },
    removeStorageSync(key) {
      storage.delete(key)
    }
  }, overrides)
}

function authenticatedStorage() {
  return new Map([
    [TOKEN_KEY, 'old-access-sensitive'],
    [REFRESH_TOKEN_KEY, 'old-refresh-sensitive'],
    [SESSION_KEY, 'old-session-sensitive'],
    [DEVICE_KEY, 'device-sensitive'],
    [USER_KEY, formalUser]
  ])
}

function installGlobals(context, wxValue, app = { globalData: {} }) {
  global.wx = wxValue
  global.getApp = () => app
  global.getCurrentPages = () => [{ route: 'pages/home/index' }]
  resetModules()
  context.after(() => {
    resetModules()
    delete global.wx
    delete global.getApp
    delete global.getCurrentPages
    delete global.App
  })
  return app
}

function runHardCrashMutation(storage, operation, crashCode) {
  const tempDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'rsc-auth-crash-'))
  const storagePath = path.join(tempDirectory, 'storage.json')
  fs.writeFileSync(storagePath, JSON.stringify(Object.fromEntries(storage)))
  const childScript = `
    const fs = require('node:fs')
    const storagePath = ${JSON.stringify(storagePath)}
    const sessionModule = ${JSON.stringify(SESSION_MODULE)}
    let storage = JSON.parse(fs.readFileSync(storagePath, 'utf8'))
    const save = () => fs.writeFileSync(storagePath, JSON.stringify(storage))
    global.getApp = () => ({ globalData: {} })
    global.wx = {
      getStorageSync(key) {
        return Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : ''
      },
      setStorageSync(key, value) {
        storage[key] = value
        save()
        if (${JSON.stringify(operation)} === 'login' && key === ${JSON.stringify(REFRESH_TOKEN_KEY)} && value === 'crash-new-refresh') {
          process.exit(${crashCode})
        }
      },
      removeStorageSync(key) {
        delete storage[key]
        save()
        if (${JSON.stringify(operation)} === 'logout' && key === ${JSON.stringify(TOKEN_KEY)}) {
          process.exit(${crashCode})
        }
      }
    }
    const session = require(sessionModule)
    if (${JSON.stringify(operation)} === 'login') {
      session.setSession({
        access_token: 'crash-new-access',
        refresh_token: 'crash-new-refresh',
        session_id: 'crash-new-session',
        user: ${JSON.stringify(formalUser)}
      })
    } else {
      session.clearSession()
    }
  `
  const child = spawnSync(process.execPath, ['-e', childScript], { encoding: 'utf8' })
  const persisted = new Map(Object.entries(JSON.parse(fs.readFileSync(storagePath, 'utf8'))))
  return { child, persisted, tempDirectory }
}

test('successful refresh persists a non-sensitive sentinel before transport and clears it after setSession', async (context) => {
  const storage = authenticatedStorage()
  const sentinelSnapshots = []
  let inventoryAttempt = 0
  let refreshCount = 0
  const wxValue = storageWx(storage, {
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') {
        refreshCount += 1
        sentinelSnapshots.push(storage.get(SENTINEL_KEY))
        queueMicrotask(() => options.success({
          statusCode: 200,
          data: {
            access_token: 'new-access-sensitive',
            refresh_token: 'new-refresh-sensitive',
            session_id: 'new-session-sensitive',
            user: formalUser
          }
        }))
        return
      }
      inventoryAttempt += 1
      queueMicrotask(() => options.success(
        inventoryAttempt === 1
          ? { statusCode: 401, data: { detail: 'expired' } }
          : { statusCode: 200, data: { ok: true } }
      ))
    },
    reLaunch() {
      assert.fail('successful refresh must not force a login')
    }
  })
  installGlobals(context, wxValue)

  const api = require('../utils/api')
  assert.deepEqual(await api.get('/access/context'), { ok: true })

  assert.equal(refreshCount, 1)
  assert.deepEqual(sentinelSnapshots, [SENTINEL_VALUE])
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(storage.get(TOKEN_KEY), 'new-access-sensitive')
  assert.equal(storage.get(REFRESH_TOKEN_KEY), 'new-refresh-sensitive')
  const serializedSentinel = JSON.stringify(sentinelSnapshots)
  for (const sensitive of [
    'old-access-sensitive',
    'old-refresh-sensitive',
    'old-session-sensitive',
    'device-sensitive',
    formalUser.name,
    formalUser.person_id
  ]) {
    assert.equal(serializedSentinel.includes(sensitive), false)
  }
})

test('SMS and WeChat login persist a non-sensitive login sentinel before wx.request and clear it after a verified session write', async (context) => {
  const storage = authenticatedStorage()
  const sentinelAtTransport = []
  let loginSequence = 0
  const wxValue = storageWx(storage, {
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      assert.ok([
        '/auth/miniprogram/sms-login',
        '/auth/miniprogram/wechat-login'
      ].includes(pathname))
      sentinelAtTransport.push(storage.get(SENTINEL_KEY))
      loginSequence += 1
      options.success({
        statusCode: 200,
        data: {
          access_token: `explicit-access-${loginSequence}`,
          refresh_token: `explicit-refresh-${loginSequence}`,
          session_id: `explicit-session-${loginSequence}`,
          user: formalUser
        }
      })
    }
  })
  installGlobals(context, wxValue)
  const api = require('../utils/api')

  const smsResult = await api.post('/auth/miniprogram/sms-login', {
    mobile: '13800000000',
    code: '246810',
    device_id: 'device-sensitive'
  })
  assert.equal(storage.get(SENTINEL_KEY), LOGIN_SENTINEL_VALUE)
  await api.establishExplicitSession(smsResult)
  assert.equal(storage.has(SENTINEL_KEY), false)

  const wechatResult = await api.post('/auth/miniprogram/wechat-login', {
    login_code: 'wechat-code-sensitive',
    phone_code: 'phone-code-sensitive',
    device_id: 'device-sensitive'
  })
  assert.equal(storage.get(SENTINEL_KEY), LOGIN_SENTINEL_VALUE)
  await api.establishExplicitSession(wechatResult)

  assert.deepEqual(sentinelAtTransport, [LOGIN_SENTINEL_VALUE, LOGIN_SENTINEL_VALUE])
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(storage.get(TOKEN_KEY), 'explicit-access-2')
  assert.equal(storage.get(REFRESH_TOKEN_KEY), 'explicit-refresh-2')
  assert.equal(storage.get(SESSION_KEY), 'explicit-session-2')
  const serializedSentinels = JSON.stringify(sentinelAtTransport)
  for (const sensitive of [
    '13800000000',
    '246810',
    'wechat-code-sensitive',
    'phone-code-sensitive',
    'device-sensitive',
    'explicit-access-2',
    'explicit-refresh-2'
  ]) {
    assert.equal(serializedSentinels.includes(sensitive), false)
  }
})

test('a definitive explicit-login HTTP rejection removes only the login sentinel and restores the old complete tuple', async (context) => {
  const storage = authenticatedStorage()
  const before = Object.fromEntries(storage)
  let sentinelAtTransport
  const wxValue = storageWx(storage, {
    request(options) {
      sentinelAtTransport = storage.get(SENTINEL_KEY)
      options.success({ statusCode: 422, data: { detail: 'invalid code' } })
    }
  })
  installGlobals(context, wxValue)
  const api = require('../utils/api')

  await assert.rejects(
    api.post('/auth/miniprogram/sms-login', {
      mobile: '13800000000',
      code: '000000',
      device_id: 'device-sensitive'
    }),
    (error) => error.status === 422
  )

  assert.equal(sentinelAtTransport, LOGIN_SENTINEL_VALUE)
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.deepEqual(Object.fromEntries(storage), before)
  assert.equal(require('../utils/session').getToken(), 'old-access-sensitive')
})

test('an uncertain explicit-login transport keeps the pending sentinel and startup fails closed', async (context) => {
  const storage = authenticatedStorage()
  let sentinelAtTransport
  let appDefinition
  let reLaunchUrl = ''
  const wxValue = storageWx(storage, {
    request(options) {
      sentinelAtTransport = storage.get(SENTINEL_KEY)
      options.fail({ errMsg: 'network timeout' })
    },
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  })
  const app = installGlobals(context, wxValue)
  const api = require('../utils/api')

  await assert.rejects(
    api.post('/auth/miniprogram/wechat-login', {
      login_code: 'uncertain-code',
      phone_code: null,
      device_id: 'device-sensitive'
    }),
    (error) => error.status === 0
  )
  assert.equal(sentinelAtTransport, LOGIN_SENTINEL_VALUE)
  assert.equal(storage.get(SENTINEL_KEY), LOGIN_SENTINEL_VALUE)
  assert.equal(require('../utils/session').getToken(), '')

  resetModules()
  global.App = (definition) => {
    appDefinition = definition
    Object.assign(app, definition)
  }
  require('../app')

  assert.equal(appDefinition.globalData.user, null)
  assert.equal(storage.has(TOKEN_KEY), false)
  assert.equal(storage.has(REFRESH_TOKEN_KEY), false)
  assert.equal(storage.has(SESSION_KEY), false)
  assert.equal(storage.has(USER_KEY), false)
  assert.equal(storage.has(SENTINEL_KEY), false)
  appDefinition.onLaunch()
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('hard process death during explicit login leaves a mixed tuple guarded by login pending and restart removes it', (context) => {
  const crash = runHardCrashMutation(authenticatedStorage(), 'login', 73)
  context.after(() => fs.rmSync(crash.tempDirectory, { recursive: true, force: true }))

  assert.equal(crash.child.status, 73)
  assert.equal(crash.persisted.get(SENTINEL_KEY), LOGIN_SENTINEL_VALUE)
  assert.equal(crash.persisted.get(TOKEN_KEY), 'crash-new-access')
  assert.equal(crash.persisted.get(REFRESH_TOKEN_KEY), 'crash-new-refresh')
  assert.equal(crash.persisted.get(SESSION_KEY), 'old-session-sensitive')

  let appDefinition
  let reLaunchUrl = ''
  const app = installGlobals(context, storageWx(crash.persisted, {
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  }))
  global.App = (definition) => {
    appDefinition = definition
    Object.assign(app, definition)
  }
  require('../app')

  assert.equal(appDefinition.globalData.user, null)
  assert.equal(crash.persisted.has(TOKEN_KEY), false)
  assert.equal(crash.persisted.has(REFRESH_TOKEN_KEY), false)
  assert.equal(crash.persisted.has(SESSION_KEY), false)
  assert.equal(crash.persisted.has(USER_KEY), false)
  assert.equal(crash.persisted.has(SENTINEL_KEY), false)
  appDefinition.onLaunch()
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('hard process death during explicit logout cannot auto-restore a residual refresh token', (context) => {
  const crash = runHardCrashMutation(authenticatedStorage(), 'logout', 74)
  context.after(() => fs.rmSync(crash.tempDirectory, { recursive: true, force: true }))

  assert.equal(crash.child.status, 74)
  assert.equal(crash.persisted.get(SENTINEL_KEY), LOGOUT_SENTINEL_VALUE)
  assert.equal(crash.persisted.has(TOKEN_KEY), false)
  assert.equal(crash.persisted.get(REFRESH_TOKEN_KEY), 'old-refresh-sensitive')

  let appDefinition
  let reLaunchUrl = ''
  const app = installGlobals(context, storageWx(crash.persisted, {
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  }))
  global.App = (definition) => {
    appDefinition = definition
    Object.assign(app, definition)
  }
  require('../app')

  assert.equal(appDefinition.globalData.user, null)
  assert.equal(crash.persisted.has(REFRESH_TOKEN_KEY), false)
  assert.equal(crash.persisted.has(SESSION_KEY), false)
  assert.equal(crash.persisted.has(USER_KEY), false)
  assert.equal(crash.persisted.has(SENTINEL_KEY), false)
  appDefinition.onLaunch()
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('a process crash before transport leaves pending state that a simulated app restart consumes', (context) => {
  const storage = authenticatedStorage()
  let randomCount = 0
  let reLaunchUrl = ''
  let appDefinition
  const wxValue = storageWx(storage, {
    getRandomValues(input) {
      randomCount += 1
      return randomValues(input)
    },
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  })
  const app = installGlobals(context, wxValue)

  const session = require('../utils/session')
  assert.equal(session.beginRefreshAttempt(), 'old-refresh-sensitive')
  assert.equal(storage.get(SENTINEL_KEY), SENTINEL_VALUE)
  assert.equal(randomCount, 0)

  resetModules()
  global.App = (definition) => {
    appDefinition = definition
    Object.assign(app, definition)
  }
  require('../app')

  assert.equal(appDefinition.globalData.user, null)
  assert.equal(storage.has(TOKEN_KEY), false)
  assert.equal(storage.has(REFRESH_TOKEN_KEY), false)
  assert.equal(storage.has(SESSION_KEY), false)
  assert.equal(storage.has(USER_KEY), false)
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(storage.get(DEVICE_KEY), 'device-sensitive')
  appDefinition.onLaunch()
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('an unknown persisted refresh state clears the session before creating a new key or refresh request', async (context) => {
  const storage = authenticatedStorage()
  storage.set(SENTINEL_KEY, 'unknown-future-state')
  let randomCount = 0
  let refreshCount = 0
  let reLaunchUrl = ''
  const wxValue = storageWx(storage, {
    getRandomValues(input) {
      randomCount += 1
      return randomValues(input)
    },
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') refreshCount += 1
      queueMicrotask(() => options.success({ statusCode: 401, data: { detail: 'expired' } }))
    },
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  })
  installGlobals(context, wxValue)

  const api = require('../utils/api')
  await assert.rejects(api.get('/access/context'), (error) => error.status === 401)

  assert.equal(refreshCount, 0)
  assert.equal(randomCount, 0)
  assert.equal(storage.has(TOKEN_KEY), false)
  assert.equal(storage.has(REFRESH_TOKEN_KEY), false)
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('sentinel storage failure stops refresh before key generation and clears the formal session', async (context) => {
  const storage = authenticatedStorage()
  let randomCount = 0
  let refreshCount = 0
  let reLaunchUrl = ''
  const wxValue = storageWx(storage, {
    getRandomValues(input) {
      randomCount += 1
      return randomValues(input)
    },
    setStorageSync(key, value) {
      if (key === SENTINEL_KEY) throw new Error('storage unavailable')
      storage.set(key, value)
    },
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') refreshCount += 1
      queueMicrotask(() => options.success({ statusCode: 401, data: { detail: 'expired' } }))
    },
    reLaunch({ url }) {
      reLaunchUrl = url
    }
  })
  installGlobals(context, wxValue)

  const api = require('../utils/api')
  await assert.rejects(api.get('/access/context'), (error) => error.status === 503)

  assert.equal(refreshCount, 0)
  assert.equal(randomCount, 0)
  assert.equal(storage.has(TOKEN_KEY), false)
  assert.equal(storage.has(REFRESH_TOKEN_KEY), false)
  assert.equal(storage.has(SESSION_KEY), false)
  assert.equal(storage.has(USER_KEY), false)
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(reLaunchUrl, '/pages/login/index')
})

test('an explicit formal login and confirmed local logout both clear a stale sentinel', (context) => {
  const storage = authenticatedStorage()
  storage.set(SENTINEL_KEY, 'unknown-stale-state')
  const app = installGlobals(context, storageWx(storage))
  const session = require('../utils/session')

  session.setSession({
    access_token: 'fresh-access',
    refresh_token: 'fresh-refresh',
    session_id: 'fresh-session',
    user: formalUser
  })
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(storage.get(TOKEN_KEY), 'fresh-access')
  assert.deepEqual(app.globalData.user, formalUser)

  storage.set(SENTINEL_KEY, SENTINEL_VALUE)
  const api = require('../utils/api')
  assert.equal(api.clearExplicitSession(), true)
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(storage.has(TOKEN_KEY), false)
  assert.equal(storage.has(REFRESH_TOKEN_KEY), false)
  assert.equal(storage.get(DEVICE_KEY), 'device-sensitive')
  assert.equal(app.globalData.user, null)
})

test('a late successful refresh cannot overwrite an explicit SMS or WeChat login session', async (context) => {
  const storage = authenticatedStorage()
  let releaseRefresh
  let reLaunchCount = 0
  const wxValue = storageWx(storage, {
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') {
        releaseRefresh = () => options.success({
          statusCode: 200,
          data: {
            access_token: 'late-refresh-access',
            refresh_token: 'late-refresh-token',
            session_id: 'late-refresh-session',
            user: formalUser
          }
        })
        return
      }
      queueMicrotask(() => options.success({ statusCode: 401, data: { detail: 'expired' } }))
    },
    reLaunch() {
      reLaunchCount += 1
    }
  })
  installGlobals(context, wxValue)
  const api = require('../utils/api')

  const oldRequestOutcome = api.get('/access/context').then(
    (value) => ({ value }),
    (error) => ({ error })
  )
  while (!releaseRefresh) await new Promise((resolve) => setImmediate(resolve))
  assert.equal(storage.get(SENTINEL_KEY), SENTINEL_VALUE)

  const explicitLogin = api.establishExplicitSession({
    access_token: 'explicit-access',
    refresh_token: 'explicit-refresh',
    session_id: 'explicit-session',
    user: formalUser
  })
  releaseRefresh()
  await explicitLogin
  const oldOutcome = await oldRequestOutcome

  assert.equal(oldOutcome.error.refreshSuperseded, true)
  assert.equal(storage.get(TOKEN_KEY), 'explicit-access')
  assert.equal(storage.get(REFRESH_TOKEN_KEY), 'explicit-refresh')
  assert.equal(storage.get(SESSION_KEY), 'explicit-session')
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(reLaunchCount, 0)
})

test('a late failed refresh cannot clear an explicit login session', async (context) => {
  const storage = authenticatedStorage()
  let failRefresh
  let reLaunchCount = 0
  const wxValue = storageWx(storage, {
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') {
        failRefresh = () => options.fail({ errMsg: 'simulated timeout' })
        return
      }
      queueMicrotask(() => options.success({ statusCode: 401, data: { detail: 'expired' } }))
    },
    reLaunch() {
      reLaunchCount += 1
    }
  })
  installGlobals(context, wxValue)
  const api = require('../utils/api')

  const oldRequestOutcome = api.get('/access/context').then(
    (value) => ({ value }),
    (error) => ({ error })
  )
  while (!failRefresh) await new Promise((resolve) => setImmediate(resolve))
  const explicitLogin = api.establishExplicitSession({
    access_token: 'explicit-access-after-failure',
    refresh_token: 'explicit-refresh-after-failure',
    session_id: 'explicit-session-after-failure',
    user: formalUser
  })
  failRefresh()
  await explicitLogin
  const oldOutcome = await oldRequestOutcome

  assert.equal(oldOutcome.error.refreshSuperseded, true)
  assert.equal(storage.get(TOKEN_KEY), 'explicit-access-after-failure')
  assert.equal(storage.get(REFRESH_TOKEN_KEY), 'explicit-refresh-after-failure')
  assert.equal(storage.has(SENTINEL_KEY), false)
  assert.equal(reLaunchCount, 0)
})
