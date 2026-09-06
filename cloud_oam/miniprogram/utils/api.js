const { API_BASE_URL } = require('./config')
const session = require('./session')
const { blockedClientWriteReason } = require('./production-guard')

const SAFE_MACHINE_ERROR_FIELD = /^[a-z][a-z0-9_]{0,127}$/

function safeMachineErrorField(value) {
  return typeof value === 'string' && SAFE_MACHINE_ERROR_FIELD.test(value)
    ? value
    : undefined
}

function apiError(status, message, responseReceived = false, metadata = {}) {
  const error = new Error(message || `请求失败 (${status})`)
  error.status = status
  error.responseReceived = responseReceived
  const code = safeMachineErrorField(metadata.code)
  const category = safeMachineErrorField(metadata.category)
  if (code !== undefined) error.code = code
  if (category !== undefined) error.category = category
  return error
}

function responseErrorDetails(payload) {
  const detail = payload && payload.detail
  if (typeof detail === 'string') return { message: detail }
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return {
      message: detail.message,
      code: safeMachineErrorField(detail.code),
      category: safeMachineErrorField(detail.category)
    }
  }
  return { message: '' }
}

const SAFE_REQUEST_ID = /^wxreq-[a-f0-9]{36}$/
const SAFE_IDEMPOTENCY_KEY = /^wxidem-[a-f0-9]{36}$/

function normalizedPath(path) {
  return (String(path || '').split('?', 1)[0].replace(/\/+$/, '') || '/')
}

function isPrivateIdentityRead(path, method) {
  if (String(method || 'GET').toUpperCase() !== 'GET') return false
  const cleanPath = normalizedPath(path)
  return cleanPath === '/auth/me' || cleanPath === '/access/context'
}

function isAuthWriteRequest(path, method) {
  const cleanPath = normalizedPath(path)
  const verb = String(method || 'GET').toUpperCase()
  return (
    (cleanPath === '/auth' || cleanPath.startsWith('/auth/')) &&
    !['GET', 'HEAD', 'OPTIONS'].includes(verb)
  )
}

function isFormalBusinessWriteRequest(path, method) {
  const cleanPath = normalizedPath(path)
  const verb = String(method || 'GET').toUpperCase()
  return (
    cleanPath.startsWith('/v1/') &&
    !['GET', 'HEAD', 'OPTIONS'].includes(verb)
  )
}

function isExplicitLoginRequest(path, method) {
  const cleanPath = normalizedPath(path)
  return String(method || 'GET').toUpperCase() === 'POST' && (
    cleanPath === '/auth/miniprogram/sms-login' ||
    cleanPath === '/auth/miniprogram/wechat-login'
  )
}

function isDefinitiveExplicitLoginRejection(status) {
  return status >= 400 && status < 500 && status !== 408 && status !== 425
}

function headerValue(source = {}, expectedName) {
  const match = Object.keys(source).find(
    (name) => name.toLowerCase() === expectedName.toLowerCase()
  )
  return match ? source[match] : ''
}

function byteView(value) {
  const candidate = value && value.randomValues !== undefined
    ? value.randomValues
    : value
  if (candidate instanceof Uint8Array) return candidate
  if (candidate instanceof ArrayBuffer) return new Uint8Array(candidate)
  if (ArrayBuffer.isView(candidate)) {
    return new Uint8Array(candidate.buffer, candidate.byteOffset, candidate.byteLength)
  }
  if (Array.isArray(candidate)) return Uint8Array.from(candidate)
  return null
}

function secureRandomHex() {
  let bytes = null
  if (typeof wx !== 'undefined' && typeof wx.getRandomValues === 'function') {
    bytes = byteView(wx.getRandomValues({ length: 18 }))
  } else if (
    typeof globalThis !== 'undefined' &&
    globalThis.crypto &&
    typeof globalThis.crypto.getRandomValues === 'function'
  ) {
    bytes = globalThis.crypto.getRandomValues(new Uint8Array(18))
  }
  if (!bytes || bytes.length !== 18) {
    throw apiError(503, '当前小程序环境缺少安全随机数能力，已停止受控写请求')
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function createOpaqueId(prefix) {
  return `${prefix}-${secureRandomHex()}`
}

function createRequestId() {
  return createOpaqueId('wxreq')
}

function createIdempotencyKey() {
  return createOpaqueId('wxidem')
}

function controlledHeaders(source = {}, path, method) {
  const controlledWrite = (
    isAuthWriteRequest(path, method) ||
    isFormalBusinessWriteRequest(path, method)
  )
  return Object.keys(source).reduce((result, name) => {
    const normalizedName = name.toLowerCase()
    if (
      !controlledWrite ||
      (normalizedName !== 'x-request-id' && normalizedName !== 'idempotency-key')
    ) result[name] = source[name]
    return result
  }, {})
}

function prepareAuthenticationWrite(path, method, options) {
  if (!isAuthWriteRequest(path, method)) return options
  const prepared = Object.assign({}, options)
  const suppliedHeaderKey = headerValue(prepared.header, 'idempotency-key')
  const suppliedOptionKey = prepared.idempotencyKey || ''
  if (suppliedHeaderKey && suppliedOptionKey && suppliedHeaderKey !== suppliedOptionKey) {
    throw apiError(400, '认证写请求存在冲突的幂等键')
  }
  const idempotencyKey = suppliedOptionKey || suppliedHeaderKey || createIdempotencyKey()
  if (!SAFE_IDEMPOTENCY_KEY.test(idempotencyKey)) {
    throw apiError(400, '认证写请求缺少安全幂等键')
  }
  const suppliedHeaderRequestId = headerValue(prepared.header, 'x-request-id')
  const suppliedOptionRequestId = prepared.requestId || ''
  if (
    suppliedHeaderRequestId &&
    suppliedOptionRequestId &&
    suppliedHeaderRequestId !== suppliedOptionRequestId
  ) {
    throw apiError(400, '认证写请求存在冲突的请求标识')
  }
  const requestId = suppliedOptionRequestId || suppliedHeaderRequestId || createRequestId()
  if (!SAFE_REQUEST_ID.test(requestId)) {
    throw apiError(400, '认证写请求缺少安全请求标识')
  }
  prepared.idempotencyKey = idempotencyKey
  prepared.requestId = requestId
  return prepared
}

function prepareFormalBusinessWrite(path, method, options) {
  if (!isFormalBusinessWriteRequest(path, method)) return options
  const prepared = Object.assign({}, options)
  // The explicit non-execution seal is a server-side tombstone keyed by the
  // trace coordinate.  It must never receive an Idempotency-Key, because a
  // generated key would create a second replayable write coordinate.
  if (prepared.omitIdempotencyKey === true) {
    const suppliedHeaderKey = headerValue(prepared.header, 'idempotency-key')
    const suppliedOptionKey = prepared.idempotencyKey || ''
    if (suppliedHeaderKey || suppliedOptionKey) {
      throw apiError(400, '未执行封存请求禁止携带幂等键')
    }
    prepared.idempotencyKey = ''
    return prepared
  }
  const suppliedHeaderKey = headerValue(prepared.header, 'idempotency-key')
  const suppliedOptionKey = prepared.idempotencyKey || ''
  if (suppliedHeaderKey && suppliedOptionKey && suppliedHeaderKey !== suppliedOptionKey) {
    throw apiError(400, '正式业务写请求存在冲突的幂等键')
  }
  const idempotencyKey = suppliedOptionKey || suppliedHeaderKey || createIdempotencyKey()
  if (!SAFE_IDEMPOTENCY_KEY.test(idempotencyKey)) {
    throw apiError(400, '正式业务写请求缺少安全幂等键')
  }
  const suppliedHeaderRequestId = headerValue(prepared.header, 'x-request-id')
  const suppliedOptionRequestId = prepared.requestId || ''
  if (
    suppliedHeaderRequestId &&
    suppliedOptionRequestId &&
    suppliedHeaderRequestId !== suppliedOptionRequestId
  ) {
    throw apiError(400, '正式业务写请求存在冲突的请求标识')
  }
  const requestId = suppliedOptionRequestId || suppliedHeaderRequestId || createRequestId()
  if (!SAFE_REQUEST_ID.test(requestId)) {
    throw apiError(400, '正式业务写请求缺少安全请求标识')
  }
  prepared.idempotencyKey = idempotencyKey
  prepared.requestId = requestId
  return prepared
}

let refreshInFlight = null
let authenticationGeneration = 0
let activeExplicitLoginGeneration = null
const explicitLoginResponseGenerations = new WeakMap()

function supersededRefreshError() {
  const error = apiError(409, '会话已由新的显式登录替换')
  error.refreshSuperseded = true
  return error
}

function handleUnauthorized() {
  session.clearSession()
  const pages = getCurrentPages()
  const current = pages.length ? pages[pages.length - 1].route : ''
  if (current !== 'pages/login/index') {
    wx.reLaunch({ url: '/pages/login/index' })
  }
}

function rawRequest(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  const blockedReason = blockedClientWriteReason(path, method)
  if (blockedReason) return Promise.reject(apiError(403, blockedReason))
  if (!API_BASE_URL) {
    return Promise.reject(apiError(503, '当前构建未配置服务地址，已停止网络请求'))
  }
  const explicitLogin = isExplicitLoginRequest(path, method)
  let explicitLoginGeneration = null
  if (explicitLogin) {
    try {
      session.beginExplicitLoginAttempt()
      authenticationGeneration += 1
      explicitLoginGeneration = authenticationGeneration
      activeExplicitLoginGeneration = explicitLoginGeneration
    } catch (error) {
      return Promise.reject(error)
    }
  }
  const token = session.getToken()
  const header = controlledHeaders(options.header, path, method)
  if (token) header.Authorization = `Bearer ${token}`
  if (isPrivateIdentityRead(path, method)) {
    header['Cache-Control'] = 'no-store'
    header.Pragma = 'no-cache'
  }
  const controlledWrite = (
    isAuthWriteRequest(path, method) ||
    isFormalBusinessWriteRequest(path, method)
  )
  if (controlledWrite) {
    if (!SAFE_REQUEST_ID.test(options.requestId || '')) {
      return Promise.reject(apiError(400, '受控写请求缺少安全请求标识'))
    }
    if (options.omitIdempotencyKey !== true && !SAFE_IDEMPOTENCY_KEY.test(options.idempotencyKey || '')) {
      return Promise.reject(apiError(400, '受控写请求缺少安全幂等键'))
    }
    header['X-Request-ID'] = options.requestId
    if (options.omitIdempotencyKey !== true) header['Idempotency-Key'] = options.idempotencyKey
    if (isAuthWriteRequest(path, method)) header['X-Auth-Client'] = 'miniprogram'
  }
  if (options.data && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    header['content-type'] = 'application/json'
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE_URL}${path}`,
      method,
      data: options.data,
      header,
      timeout: options.timeout || 20000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          if (
            explicitLoginGeneration !== null &&
            response.data && typeof response.data === 'object'
          ) {
            explicitLoginResponseGenerations.set(response.data, explicitLoginGeneration)
          }
          resolve(response.data)
          return
        }
        if (
          explicitLoginGeneration !== null &&
          explicitLoginGeneration === authenticationGeneration &&
          isDefinitiveExplicitLoginRejection(response.statusCode)
        ) {
          try {
            session.cancelExplicitLoginAttempt()
            activeExplicitLoginGeneration = null
          } catch (error) {
            reject(error)
            return
          }
        }
        const details = responseErrorDetails(response.data)
        reject(apiError(response.statusCode, details.message, true, details))
      },
      fail(error) {
        reject(apiError(0, error.errMsg || '网络连接失败'))
      }
    })
  })
}

function refreshSession() {
  if (refreshInFlight) return refreshInFlight
  if (activeExplicitLoginGeneration !== null) {
    return Promise.reject(supersededRefreshError())
  }
  const refreshGeneration = authenticationGeneration
  let refreshToken
  let prepared
  try {
    refreshToken = session.beginRefreshAttempt()
    prepared = prepareAuthenticationWrite('/auth/miniprogram/refresh', 'POST', {
      method: 'POST',
      data: {
        refresh_token: refreshToken,
        device_id: session.getDeviceId()
      }
    })
  } catch (error) {
    return Promise.reject(error)
  }
  refreshInFlight = rawRequest('/auth/miniprogram/refresh', prepared).then((result) => {
    if (refreshGeneration !== authenticationGeneration) {
      throw supersededRefreshError()
    }
    session.setSession(result, { mutation: 'refresh' })
    return result
  }).catch((error) => {
    if (refreshGeneration !== authenticationGeneration) {
      throw supersededRefreshError()
    }
    session.clearSession()
    throw error
  }).finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

async function establishExplicitSession(result) {
  const responseGeneration = (
    result && typeof result === 'object'
      ? explicitLoginResponseGenerations.get(result)
      : null
  )
  if (responseGeneration === undefined || responseGeneration === null) {
    authenticationGeneration += 1
  } else if (responseGeneration !== authenticationGeneration) {
    throw supersededRefreshError()
  }
  const pendingRefresh = refreshInFlight
  if (pendingRefresh) {
    try {
      await pendingRefresh
    } catch (_) {}
  }
  if (
    responseGeneration !== undefined &&
    responseGeneration !== null &&
    responseGeneration !== authenticationGeneration
  ) {
    throw supersededRefreshError()
  }
  try {
    session.setSession(result)
    return result
  } finally {
    if (
      responseGeneration === undefined ||
      responseGeneration === null ||
      responseGeneration === activeExplicitLoginGeneration
    ) {
      activeExplicitLoginGeneration = null
    }
  }
}

function clearExplicitSession() {
  authenticationGeneration += 1
  activeExplicitLoginGeneration = null
  return session.clearSession()
}

function handleRefreshFailure(error) {
  if (!error || !error.refreshSuperseded) handleUnauthorized()
}

function shouldRefresh(path) {
  return path === '/auth/me' || !path.startsWith('/auth/')
}

function request(path, options = {}) {
  let prepared = Object.assign({}, options)
  const method = (prepared.method || 'GET').toUpperCase()
  try {
    prepared = prepareAuthenticationWrite(path, method, prepared)
    prepared = prepareFormalBusinessWrite(path, method, prepared)
  } catch (error) {
    return Promise.reject(error)
  }
  const transport = () => rawRequest(path, prepared)
  const initialRequest = isExplicitLoginRequest(path, method) && refreshInFlight
    ? refreshInFlight.catch(() => undefined).then(transport)
    : transport()
  return initialRequest.catch((error) => {
    if (error.status !== 401 || prepared.noRefresh || !shouldRefresh(path)) throw error
    return refreshSession()
      .then(() => rawRequest(path, Object.assign({}, prepared, { noRefresh: true })))
      .catch((refreshError) => {
        handleRefreshFailure(refreshError)
        throw refreshError
      })
  })
}

function queryString(params = {}) {
  return Object.keys(params)
    .filter((key) => params[key] !== '' && params[key] !== null && params[key] !== undefined)
    .map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(params[key])}`)
    .join('&')
}

function get(path, params) {
  const query = queryString(params)
  return request(query ? `${path}?${query}` : path)
}

function post(path, data = {}, options = {}) {
  return request(path, Object.assign({}, options, { method: 'POST', data }))
}

// Durable business commands recover through GET; even a 401 must not replay the POST.
function postNoReplay(path, data = {}, options = {}) {
  return post(path, data, Object.assign({}, options, { noRefresh: true }))
}

// Dedicated write for an uncertain non-opening post.  The server records a
// permanent sealed_not_executed outcome under X-Request-ID and intentionally
// rejects Idempotency-Key; no refresh or automatic replay is permitted.
function postSealNoReplay(path, data = {}, options = {}) {
  return request(path, Object.assign({}, options, {
    method: 'POST', data, noRefresh: true, omitIdempotencyKey: true,
  }))
}

function put(path, data = {}, options = {}) {
  return request(path, Object.assign({}, options, { method: 'PUT', data }))
}

function upload(path, filePath, retried = false) {
  const blockedReason = blockedClientWriteReason(path, 'POST')
  if (blockedReason) return Promise.reject(apiError(403, blockedReason))
  if (!API_BASE_URL) {
    return Promise.reject(apiError(503, '当前构建未配置服务地址，已停止上传'))
  }
  const token = session.getToken()
  const header = token ? { Authorization: `Bearer ${token}` } : {}
  if (isAuthWriteRequest(path, 'POST')) header['X-Request-ID'] = createRequestId()
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE_URL}${path}`,
      filePath,
      name: 'file',
      header,
      timeout: 120000,
      success(response) {
        let payload = response.data
        try { payload = JSON.parse(response.data) } catch (_) {}
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(payload)
          return
        }
        if (response.statusCode === 401 && !retried && session.getRefreshToken()) {
          refreshSession()
            .then(() => upload(path, filePath, true))
            .then(resolve)
            .catch((error) => { handleRefreshFailure(error); reject(error) })
          return
        }
        if (response.statusCode === 401) handleUnauthorized()
        const details = responseErrorDetails(payload)
        reject(apiError(response.statusCode, details.message, true, details))
      },
      fail(error) {
        reject(apiError(0, error.errMsg || '上传失败'))
      }
    })
  })
}

function download(path, retried = false) {
  if (!API_BASE_URL) {
    return Promise.reject(apiError(503, '当前构建未配置服务地址，已停止下载'))
  }
  const token = session.getToken()
  const isAbsolute = /^https?:\/\//i.test(path)
  if (isAbsolute && !/^https:\/\/[^/\s@]+(?:\/[^\s]*)?$/i.test(path)) {
    return Promise.reject(apiError(400, '附件地址必须使用不含凭据的 HTTPS URL'))
  }
  const targetUrl = isAbsolute
    ? path
    : `${API_BASE_URL}${path.replace(/^\/api/, '')}`
  const header = !isAbsolute && token ? { Authorization: `Bearer ${token}` } : {}
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: targetUrl,
      header,
      timeout: 120000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.tempFilePath)
          return
        }
        if (!isAbsolute && response.statusCode === 401 && !retried && session.getRefreshToken()) {
          refreshSession()
            .then(() => download(path, true))
            .then(resolve)
            .catch((error) => { handleRefreshFailure(error); reject(error) })
          return
        }
        if (response.statusCode === 401) handleUnauthorized()
        reject(apiError(response.statusCode, '附件下载失败'))
      },
      fail(error) {
        reject(apiError(0, error.errMsg || '附件下载失败'))
      }
    })
  })
}

module.exports = {
  request,
  get,
  post,
  postNoReplay,
  postSealNoReplay,
  put,
  upload,
  download,
  establishExplicitSession,
  clearExplicitSession,
  createRequestId,
  createIdempotencyKey,
  API_BASE_URL
}
