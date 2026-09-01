const TOKEN_KEY = 'rsc_oam_access_token'
const REFRESH_TOKEN_KEY = 'rsc_oam_refresh_token'
const USER_KEY = 'rsc_oam_user'
const SESSION_KEY = 'rsc_oam_session_id'
const DEVICE_KEY = 'rsc_oam_device_id'
const REFRESH_SENTINEL_KEY = 'rsc_oam_refresh_sentinel'
const REFRESH_SENTINEL_PENDING = 'pending-v1'
const EXPLICIT_LOGIN_SENTINEL_PENDING = 'explicit-login-pending-v1'
const LOGOUT_SENTINEL_PENDING = 'logout-pending-v1'
const CREDENTIAL_KEYS = [TOKEN_KEY, REFRESH_TOKEN_KEY, SESSION_KEY, USER_KEY]
let storageQuarantined = false
const {
  canUseOperationalClient,
  formalRoleCodes,
  isFormalAuthenticatedUser,
  roleLabel
} = require('./production-guard')

function getToken() {
  if (storageQuarantined) return ''
  try {
    return wx.getStorageSync(TOKEN_KEY) || ''
  } catch (_) {
    storageQuarantined = true
    return ''
  }
}

function storageError(message) {
  const error = new Error(message || '小程序本地会话存储不可用，已停止认证操作')
  error.status = 503
  return error
}

function refreshUncertainError() {
  const error = new Error('上次会话刷新结果不确定，已清除本地会话，请重新登录')
  error.status = 401
  error.forceLogin = true
  return error
}

function hasRefreshSentinel(value) {
  return value !== '' && value !== null && value !== undefined
}

function canonicalValue(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalValue).join(',')}]`
  return `{${Object.keys(value).sort().map(
    (key) => `${JSON.stringify(key)}:${canonicalValue(value[key])}`
  ).join(',')}}`
}

function persistSentinel(value) {
  wx.setStorageSync(REFRESH_SENTINEL_KEY, value)
  if (wx.getStorageSync(REFRESH_SENTINEL_KEY) !== value) {
    throw storageError('无法确认认证变更哨兵已持久化')
  }
}

function storedSessionMatches(sessionPayload) {
  return (
    wx.getStorageSync(TOKEN_KEY) === sessionPayload.access_token &&
    wx.getStorageSync(REFRESH_TOKEN_KEY) === sessionPayload.refresh_token &&
    wx.getStorageSync(SESSION_KEY) === sessionPayload.session_id &&
    canonicalValue(wx.getStorageSync(USER_KEY)) === canonicalValue(sessionPayload.user)
  )
}

function readCredentialTuple() {
  return {
    accessToken: wx.getStorageSync(TOKEN_KEY) || '',
    refreshToken: wx.getStorageSync(REFRESH_TOKEN_KEY) || '',
    sessionId: wx.getStorageSync(SESSION_KEY) || '',
    user: wx.getStorageSync(USER_KEY) || null
  }
}

function tupleIsEmpty(tuple) {
  return !tuple.accessToken && !tuple.refreshToken && !tuple.sessionId && !tuple.user
}

function tupleIsComplete(tuple) {
  return !!tuple.accessToken && !!tuple.refreshToken && !!tuple.sessionId && !!tuple.user
}

function updateAppUser(user) {
  try {
    const app = typeof getApp === 'function' ? getApp() : null
    if (app && app.globalData) app.globalData.user = user
  } catch (_) {}
}

function accessError(user) {
  const roleCodes = formalRoleCodes(user)
  const identity = roleCodes.length ? roleLabel(roleCodes) : '未验证正式身份'
  const error = new Error(`${identity}当前不能进入库存客户端`)
  error.status = 403
  return error
}

function setSession(sessionPayload, options = {}) {
  const user = sessionPayload && sessionPayload.user
  const roleCodes = formalRoleCodes(user)
  if (!isFormalAuthenticatedUser(user) || !canUseOperationalClient(roleCodes)) {
    const error = accessError(user)
    clearSession()
    throw error
  }
  if (
    typeof sessionPayload.access_token !== 'string' || !sessionPayload.access_token ||
    typeof sessionPayload.refresh_token !== 'string' || !sessionPayload.refresh_token ||
    typeof sessionPayload.session_id !== 'string' || !sessionPayload.session_id
  ) {
    clearSession()
    throw storageError('认证响应中的会话字段不完整')
  }
  try {
    if (options.mutation === 'refresh') {
      if (wx.getStorageSync(REFRESH_SENTINEL_KEY) !== REFRESH_SENTINEL_PENDING) {
        throw storageError('会话刷新哨兵已丢失或被替换')
      }
    } else {
      persistSentinel(EXPLICIT_LOGIN_SENTINEL_PENDING)
    }
    wx.setStorageSync(TOKEN_KEY, sessionPayload.access_token)
    wx.setStorageSync(REFRESH_TOKEN_KEY, sessionPayload.refresh_token)
    wx.setStorageSync(SESSION_KEY, sessionPayload.session_id)
    wx.setStorageSync(USER_KEY, user)
    if (!storedSessionMatches(sessionPayload)) {
      throw storageError('无法确认新会话已完整落盘')
    }
    wx.removeStorageSync(REFRESH_SENTINEL_KEY)
    if (hasRefreshSentinel(wx.getStorageSync(REFRESH_SENTINEL_KEY))) {
      throw storageError('无法确认会话刷新标记已清除')
    }
  } catch (_) {
    storageQuarantined = true
    clearSession()
    throw storageError()
  }
  storageQuarantined = false
  updateAppUser(user)
}

function getRefreshToken() {
  if (storageQuarantined) return ''
  try {
    return wx.getStorageSync(REFRESH_TOKEN_KEY) || ''
  } catch (_) {
    storageQuarantined = true
    return ''
  }
}

function getDeviceId() {
  if (storageQuarantined) throw storageError()
  try {
    let deviceId = wx.getStorageSync(DEVICE_KEY) || ''
    if (!deviceId) {
      deviceId = `wx-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
      wx.setStorageSync(DEVICE_KEY, deviceId)
    }
    return deviceId
  } catch (_) {
    storageQuarantined = true
    throw storageError()
  }
}

function getUser() {
  if (storageQuarantined) return null
  try {
    return wx.getStorageSync(USER_KEY) || null
  } catch (_) {
    storageQuarantined = true
    return null
  }
}

function setUser(user) {
  if (user && (
    !isFormalAuthenticatedUser(user) ||
    !canUseOperationalClient(formalRoleCodes(user))
  )) {
    clearSession()
    return false
  }
  try {
    if (user) wx.setStorageSync(USER_KEY, user)
    else wx.removeStorageSync(USER_KEY)
  } catch (_) {
    storageQuarantined = true
    clearSession()
    return false
  }
  return true
}

function clearSession() {
  let sentinelPersisted = false
  try {
    persistSentinel(LOGOUT_SENTINEL_PENDING)
    sentinelPersisted = true
  } catch (_) {
    storageQuarantined = true
  }
  const cleared = clearCredentialTupleAndSentinel(sentinelPersisted)
  storageQuarantined = !cleared
  updateAppUser(null)
  return cleared
}

function clearCredentialTupleAndSentinel(canClearSentinel) {
  let credentialsCleared = true
  for (const key of CREDENTIAL_KEYS) {
    try {
      wx.removeStorageSync(key)
    } catch (_) {
      credentialsCleared = false
    }
  }
  try {
    if (!tupleIsEmpty(readCredentialTuple())) credentialsCleared = false
  } catch (_) {
    credentialsCleared = false
  }
  if (!credentialsCleared || !canClearSentinel) return false

  try {
    wx.removeStorageSync(REFRESH_SENTINEL_KEY)
    if (hasRefreshSentinel(wx.getStorageSync(REFRESH_SENTINEL_KEY))) return false
  } catch (_) {
    return false
  }
  return true
}

function recoverPersistedMutation() {
  const cleared = clearCredentialTupleAndSentinel(true)
  storageQuarantined = !cleared
  updateAppUser(null)
  return cleared
}

function beginExplicitLoginAttempt() {
  if (storageQuarantined) throw storageError()
  try {
    persistSentinel(EXPLICIT_LOGIN_SENTINEL_PENDING)
  } catch (_) {
    storageQuarantined = true
    throw storageError()
  }
  storageQuarantined = true
  return true
}

function cancelExplicitLoginAttempt() {
  let sentinel
  let tuple
  try {
    sentinel = wx.getStorageSync(REFRESH_SENTINEL_KEY)
    tuple = readCredentialTuple()
    if (sentinel !== EXPLICIT_LOGIN_SENTINEL_PENDING) throw storageError()
    if (!tupleIsEmpty(tuple) && !tupleIsComplete(tuple)) throw storageError()
    wx.removeStorageSync(REFRESH_SENTINEL_KEY)
    if (hasRefreshSentinel(wx.getStorageSync(REFRESH_SENTINEL_KEY))) {
      throw storageError()
    }
  } catch (_) {
    storageQuarantined = true
    throw storageError('无法安全恢复显式登录前的本地会话')
  }
  storageQuarantined = false
  return true
}

function beginRefreshAttempt() {
  if (storageQuarantined) {
    clearSession()
    throw storageError()
  }
  let sentinel
  try {
    sentinel = wx.getStorageSync(REFRESH_SENTINEL_KEY)
  } catch (_) {
    storageQuarantined = true
    clearSession()
    throw storageError()
  }
  if (hasRefreshSentinel(sentinel)) {
    const cleared = recoverPersistedMutation()
    if (!cleared) throw storageError()
    throw refreshUncertainError()
  }

  let tuple
  try {
    tuple = readCredentialTuple()
  } catch (_) {
    storageQuarantined = true
    clearSession()
    throw storageError()
  }
  if (!tupleIsComplete(tuple)) {
    clearSession()
    const error = new Error('登录已失效')
    error.status = 401
    throw error
  }

  try {
    persistSentinel(REFRESH_SENTINEL_PENDING)
  } catch (_) {
    storageQuarantined = true
    clearSession()
    throw storageError()
  }
  return tuple.refreshToken
}

function initializeSessionState() {
  let sentinel
  try {
    sentinel = wx.getStorageSync(REFRESH_SENTINEL_KEY)
  } catch (_) {
    storageQuarantined = true
    clearSession()
    return { user: null, forceLogin: true }
  }
  if (hasRefreshSentinel(sentinel)) {
    recoverPersistedMutation()
    return { user: null, forceLogin: true }
  }
  let tuple
  try {
    tuple = readCredentialTuple()
  } catch (_) {
    storageQuarantined = true
    clearSession()
    return { user: null, forceLogin: true }
  }
  if (!tupleIsEmpty(tuple) && !tupleIsComplete(tuple)) {
    clearSession()
    return { user: null, forceLogin: true }
  }
  if (tupleIsComplete(tuple) && (
    !isFormalAuthenticatedUser(tuple.user) ||
    !canUseOperationalClient(formalRoleCodes(tuple.user))
  )) {
    clearSession()
    return { user: null, forceLogin: true }
  }
  storageQuarantined = false
  return { user: tuple.user, forceLogin: false }
}

function ensureLogin() {
  if (getToken()) {
    const user = getUser()
    const roleCodes = formalRoleCodes(user)
    if (
      isFormalAuthenticatedUser(user) &&
      canUseOperationalClient(roleCodes)
    ) return true
    const identity = roleCodes.length ? roleLabel(roleCodes) : '未验证正式身份'
    clearSession()
    wx.showModal({
      title: '身份已隔离',
      content: `${identity}当前不能进入库存客户端。请重新验证正式登录身份和有效角色授权。`,
      showCancel: false,
      success: () => wx.reLaunch({ url: '/pages/login/index' })
    })
    return false
  }
  wx.reLaunch({ url: '/pages/login/index' })
  return false
}

module.exports = {
  getToken,
  getRefreshToken,
  getDeviceId,
  setSession,
  getUser,
  setUser,
  clearSession,
  beginExplicitLoginAttempt,
  cancelExplicitLoginAttempt,
  beginRefreshAttempt,
  initializeSessionState,
  ensureLogin
}
