const assert = require('node:assert/strict')
const { randomFillSync } = require('node:crypto')
const test = require('node:test')

const API_MODULES = [
  require.resolve('../utils/api'),
  require.resolve('../utils/config'),
  require.resolve('../utils/session')
]

function resetApiModules() {
  for (const modulePath of API_MODULES) delete require.cache[modulePath]
}

function getRandomValues({ length }) {
  return randomFillSync(new Uint8Array(length))
}

test('exact identity reads force no-store while ordinary reads keep default headers', async (context) => {
  const requests = []
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      requests.push(options)
      options.success({ statusCode: 200, data: { ok: true } })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await api.get('/auth/me')
  await api.get('/access/context', { view: 'current' })
  await api.get('/v1/inventory/summary')

  for (const request of requests.slice(0, 2)) {
    assert.equal(request.header['Cache-Control'], 'no-store')
    assert.equal(request.header.Pragma, 'no-cache')
  }
  assert.equal(requests[2].header['Cache-Control'], undefined)
  assert.equal(requests[2].header.Pragma, undefined)
})

test('lifecycle command-status GET preserves only the supplied trace request id', async (context) => {
  const requests = []
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      requests.push(options)
      options.success({
        statusCode: 200,
        data: { schema_version: '1.0', lookup_status: 'not_observed', command: null }
      })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  const requestId = `wxreq-${'a'.repeat(36)}`
  await api.request('/v1/material-request-lifecycle-command-status', {
    method: 'GET',
    header: {
      'X-Request-ID': requestId,
      'Cache-Control': 'no-store',
      Pragma: 'no-cache'
    }
  })

  assert.equal(requests.length, 1)
  assert.equal(requests[0].header['X-Request-ID'], requestId)
  assert.equal(requests[0].header['Idempotency-Key'], undefined)
  assert.equal(requests[0].header['Cache-Control'], 'no-store')
  assert.equal(requests[0].method, 'GET')
})

test('all authentication writes use safe idempotency keys without sensitive values', async (context) => {
  const requests = []
  const storage = new Map()
  let loginSequence = 0
  global.wx = {
    getRandomValues,
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
    },
    request(options) {
      requests.push(options)
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (
        pathname === '/auth/miniprogram/sms-login' ||
        pathname === '/auth/miniprogram/wechat-login'
      ) {
        loginSequence += 1
        options.success({
          statusCode: 200,
          data: {
            access_token: `login-access-${loginSequence}`,
            refresh_token: `login-refresh-${loginSequence}`,
            session_id: `login-session-${loginSequence}`,
            user: {
              person_id: '00000000-0000-4000-8000-000000000001',
              name: '测试工程师',
              role_codes: ['technician']
            }
          }
        })
        return
      }
      options.success({ statusCode: 200, data: { ok: true } })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  const mobile = '13800000000'
  const code = '246810'
  const token = 'secret-refresh-token'
  const device = 'secret-device-id'
  const sessionId = 'secret-session-id'
  const retryableKey = api.createIdempotencyKey()

  await api.post('/auth/sms/request', { mobile }, { idempotencyKey: retryableKey })
  await api.post('/auth/sms/request', { mobile }, { idempotencyKey: retryableKey })
  const smsLogin = await api.post('/auth/miniprogram/sms-login', { mobile, code })
  await api.establishExplicitSession(smsLogin)
  const wechatLogin = await api.post('/auth/miniprogram/wechat-login', {
    login_code: 'wechat-login-code',
    phone_code: 'wechat-phone-code',
    device_id: device
  })
  await api.establishExplicitSession(wechatLogin)
  await api.post('/auth/miniprogram/refresh', { refresh_token: token, device_id: device })
  await api.post('/auth/miniprogram/logout', { refresh_token: token })
  await api.post(`/auth/sessions/${sessionId}/revoke`)
  await api.get('/auth/login-options')
  await api.post('/access/provincial-managers/assignments', { reason: 'test' })

  assert.equal(requests.length, 9)
  const writeHeaders = requests.slice(0, 7).map((request) => request.header)
  const requestIds = writeHeaders.map((header) => header['X-Request-ID'])
  assert.equal(new Set(requestIds).size, requestIds.length)
  for (const requestId of requestIds) {
    assert.match(requestId, /^wxreq-[a-z0-9-]+$/)
    assert.equal(requestId.includes(mobile), false)
    assert.equal(requestId.includes(code), false)
    assert.equal(requestId.includes(token), false)
    assert.equal(requestId.includes(device), false)
    assert.equal(requestId.includes(sessionId), false)
  }
  for (const header of writeHeaders) {
    assert.equal(header['X-Auth-Client'], 'miniprogram')
  }

  const idempotencyKeys = writeHeaders.map((header) => header['Idempotency-Key'])
  assert.match(retryableKey, /^wxidem-[a-f0-9]{36}$/)
  assert.equal(writeHeaders[0]['Idempotency-Key'], retryableKey)
  assert.equal(writeHeaders[1]['Idempotency-Key'], retryableKey)
  assert.equal(new Set(idempotencyKeys.slice(1)).size, idempotencyKeys.length - 1)
  for (const key of idempotencyKeys) {
    assert.match(key, /^wxidem-[a-f0-9]{36}$/)
    for (const sensitive of [mobile, code, token, device, sessionId]) {
      assert.equal(key.includes(sensitive), false)
    }
  }
  assert.equal(requests[7].header['X-Request-ID'], undefined)
  assert.equal(requests[7].header['Idempotency-Key'], undefined)
  assert.equal(requests[8].header['Idempotency-Key'], undefined)
})

test('SMS request wrapper generates an idempotency key when the caller omits one', async (context) => {
  let transported
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      transported = options
      options.success({ statusCode: 200, data: { ok: true } })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await api.post('/auth/sms/request', { mobile: '13900000000' })

  assert.match(transported.header['Idempotency-Key'], /^wxidem-[a-f0-9]{36}$/)
  assert.match(transported.header['X-Request-ID'], /^wxreq-[a-f0-9]{36}$/)
  assert.equal(transported.header['X-Auth-Client'], 'miniprogram')
})

test('request preserves a safe caller key and rejects invalid or conflicting coordinates', async (context) => {
  const requests = []
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      requests.push(options)
      options.success({ statusCode: 204 })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  const safeKey = api.createIdempotencyKey()
  await api.post('/auth/miniprogram/logout', {}, {
    header: { 'IDEMPOTENCY-KEY': safeKey }
  })
  await assert.rejects(
    api.post('/auth/miniprogram/logout', {}, {
      header: { 'Idempotency-Key': 'mobile-13800000000' }
    }),
    (error) => error.status === 400
  )
  await assert.rejects(
    api.post('/auth/miniprogram/logout', {}, {
      idempotencyKey: safeKey,
      header: { 'Idempotency-Key': api.createIdempotencyKey() }
    }),
    (error) => error.status === 400
  )

  assert.equal(requests.length, 1)
  assert.equal(requests[0].header['Idempotency-Key'], safeKey)
  assert.equal(requests[0].header['IDEMPOTENCY-KEY'], undefined)
})

test('formal v1 business writes receive safe opaque coordinates and reads do not', async (context) => {
  const requests = []
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      requests.push(options)
      options.success({ statusCode: 200, data: { ok: true } })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await api.post(
    '/v1/stocktakes/opening/task-id/rounds/round-id/scopes/scope-id/count',
    { zero_confirmed: true, physical_observations: [] }
  )
  const callerRequestId = `wxreq-${'a'.repeat(36)}`
  const callerIdempotencyKey = `wxidem-${'b'.repeat(36)}`
  await api.post(
    '/v1/material-requests/request-id/submit',
    { expected_version: 1 },
    {
      header: {
        'X-Request-ID': callerRequestId,
        'Idempotency-Key': callerIdempotencyKey
      },
      requestId: callerRequestId,
      idempotencyKey: callerIdempotencyKey
    }
  )
  await api.get('/v1/stocktakes/opening/task-id')

  assert.equal(requests.length, 3)
  assert.match(
    requests[0].header['Idempotency-Key'],
    /^wxidem-[a-f0-9]{36}$/
  )
  assert.match(requests[0].header['X-Request-ID'], /^wxreq-[a-f0-9]{36}$/)
  assert.equal(requests[0].header['X-Auth-Client'], undefined)
  assert.equal(requests[1].header['Idempotency-Key'], callerIdempotencyKey)
  assert.equal(requests[1].header['X-Request-ID'], callerRequestId)
  assert.equal(requests[2].header['Idempotency-Key'], undefined)
  assert.equal(requests[2].header['X-Request-ID'], undefined)
})

test('HTTP rejections are marked as received while local transport guards remain uncertain', async (context) => {
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      options.success({
        statusCode: 422,
        data: { detail: { code: 'supply_task_expected_qty_invalid', category: 'invalid_request', message: '明确拒绝' } }
      })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await assert.rejects(
    api.post('/v1/material-requests/request-id/submit', { expected_version: 1 }),
    (error) => (
      error.status === 422 &&
      error.responseReceived === true &&
      error.message === '明确拒绝' &&
      error.code === 'supply_task_expected_qty_invalid' &&
      error.category === 'invalid_request'
    )
  )
  await assert.rejects(
    api.get('/inventory'),
    (error) => error.status === 403 && error.responseReceived === false &&
      error.code === undefined && error.category === undefined
  )
})

test('HTTP errors discard unsafe machine metadata without retaining response details', async (context) => {
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync() {
      return ''
    },
    request(options) {
      options.success({
        statusCode: 409,
        data: { detail: {
          code: 'material_request_version_conflict\nprivate',
          category: 'x'.repeat(129),
          message: '版本冲突',
          private_context: { token: 'must-not-be-copied' }
        } }
      })
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await assert.rejects(
    api.post('/v1/material-requests/request-id/submit', { expected_version: 1 }),
    (error) => error.status === 409 && error.responseReceived === true &&
      error.message === '版本冲突' && error.code === undefined &&
      error.category === undefined && error.private_context === undefined
  )
})

test('formal v1 write retry after session refresh reuses its business coordinates', async (context) => {
  const storage = new Map([
    ['rsc_oam_access_token', 'expired-access-token'],
    ['rsc_oam_refresh_token', 'refresh-token-sensitive'],
    ['rsc_oam_session_id', 'session-id-sensitive'],
    ['rsc_oam_device_id', 'device-id-sensitive'],
    ['rsc_oam_user', {
      person_id: '00000000-0000-4000-8000-000000000001',
      name: '测试工程师',
      role_codes: ['technician']
    }]
  ])
  const businessRequests = []
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync(key) {
      return storage.get(key) || ''
    },
    setStorageSync(key, value) {
      storage.set(key, value)
    },
    removeStorageSync(key) {
      storage.delete(key)
    },
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') {
        options.success({
          statusCode: 200,
          data: {
            access_token: 'new-access-token',
            refresh_token: 'new-refresh-token',
            session_id: 'new-session-id',
            user: {
              person_id: '00000000-0000-4000-8000-000000000001',
              name: '测试工程师',
              role_codes: ['technician']
            }
          }
        })
        return
      }
      businessRequests.push(options)
      options.success(
        businessRequests.length === 1
          ? { statusCode: 401, data: { detail: 'expired' } }
          : { statusCode: 200, data: { ok: true } }
      )
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  await api.post(
    '/v1/stocktakes/opening/task-id/rounds/round-id/scopes/scope-id/count',
    { zero_confirmed: true, physical_observations: [] }
  )

  assert.equal(businessRequests.length, 2)
  assert.equal(
    businessRequests[0].header['Idempotency-Key'],
    businessRequests[1].header['Idempotency-Key']
  )
  assert.equal(
    businessRequests[0].header['X-Request-ID'],
    businessRequests[1].header['X-Request-ID']
  )
})

test('concurrent unauthorized requests share one refresh request with one fixed key', async (context) => {
  const storage = new Map([
    ['rsc_oam_access_token', 'expired-access-token'],
    ['rsc_oam_refresh_token', 'refresh-token-sensitive'],
    ['rsc_oam_session_id', 'session-id-sensitive'],
    ['rsc_oam_device_id', 'device-id-sensitive'],
    ['rsc_oam_user', {
      person_id: '00000000-0000-4000-8000-000000000001',
      name: '测试工程师',
      role_codes: ['technician']
    }]
  ])
  const attempts = new Map()
  const refreshRequests = []
  let releaseRefresh
  global.wx = {
    getRandomValues,
    getAccountInfoSync() {
      return { miniProgram: { envVersion: 'develop' } }
    },
    getStorageSync(key) {
      return storage.get(key) || ''
    },
    setStorageSync(key, value) {
      storage.set(key, value)
    },
    removeStorageSync(key) {
      storage.delete(key)
    },
    request(options) {
      const pathname = new URL(options.url).pathname.replace(/^\/api/, '')
      if (pathname === '/auth/miniprogram/refresh') {
        refreshRequests.push(options)
        releaseRefresh = () => options.success({
          statusCode: 200,
          data: {
            access_token: 'new-access-token',
            refresh_token: 'new-refresh-token',
            session_id: 'new-session-id',
            user: {
              person_id: '00000000-0000-4000-8000-000000000001',
              name: '测试工程师',
              role_codes: ['technician']
            }
          }
        })
        return
      }
      const attempt = (attempts.get(pathname) || 0) + 1
      attempts.set(pathname, attempt)
      queueMicrotask(() => options.success(
        attempt === 1
          ? { statusCode: 401, data: { detail: 'expired' } }
          : { statusCode: 200, data: { ok: true } }
      ))
    }
  }
  global.getApp = () => ({ globalData: {} })
  resetApiModules()
  context.after(() => {
    resetApiModules()
    delete global.wx
    delete global.getApp
  })

  const api = require('../utils/api')
  const first = api.get('/access/context')
  const second = api.get('/work-orders')
  while (!releaseRefresh) await new Promise((resolve) => setImmediate(resolve))

  assert.equal(refreshRequests.length, 1)
  const refreshKey = refreshRequests[0].header['Idempotency-Key']
  assert.match(refreshKey, /^wxidem-[a-f0-9]{36}$/)
  assert.equal(refreshKey.includes('refresh-token-sensitive'), false)
  assert.equal(refreshKey.includes('device-id-sensitive'), false)
  releaseRefresh()
  assert.deepEqual(await Promise.all([first, second]), [{ ok: true }, { ok: true }])
  assert.equal(refreshRequests.length, 1)
})

test('session accepts formal role_codes and rejects a legacy role-only user', (context) => {
  const storage = new Map()
  const app = { globalData: { user: null } }
  global.wx = {
    getStorageSync(key) {
      return storage.get(key) || ''
    },
    setStorageSync(key, value) {
      storage.set(key, value)
    },
    removeStorageSync(key) {
      storage.delete(key)
    }
  }
  global.getApp = () => app
  delete require.cache[require.resolve('../utils/session')]
  context.after(() => {
    delete require.cache[require.resolve('../utils/session')]
    delete global.wx
    delete global.getApp
  })

  const session = require('../utils/session')
  const formalUser = {
    person_id: '00000000-0000-4000-8000-000000000001',
    name: '李工程师',
    employee_no: 'E-001',
    role_codes: ['technician', 'admin']
  }
  session.setSession({
    access_token: 'access',
    refresh_token: 'refresh',
    session_id: 'session',
    user: formalUser
  })

  assert.deepEqual(session.getUser(), formalUser)
  assert.deepEqual(app.globalData.user, formalUser)
  assert.equal(session.ensureLogin(), true)

  session.clearSession()
  assert.throws(
    () => session.setSession({
      access_token: 'legacy-access',
      refresh_token: 'legacy-refresh',
      session_id: 'legacy-session',
      user: { id: 'legacy-user', mobile: '13800000000', name: '旧用户', role: 'admin' }
    }),
    (error) => error.status === 403 && /未验证正式身份/.test(error.message)
  )
  assert.equal(session.getToken(), '')
})
