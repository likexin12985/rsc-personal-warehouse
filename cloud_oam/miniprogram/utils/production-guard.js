const ROLE_LABELS = {
  admin: '蔚来总部管理员',
  provincial_manager: '区域公司负责人',
  technician: '工程师',
  star_headquarters_approver: '星星总部审批人（外部审批身份）'
}

const OPERATIONAL_ROLES = ['admin', 'provincial_manager', 'technician']
const ROLE_DISPLAY_PRIORITY = [
  'admin',
  'provincial_manager',
  'technician',
  'star_headquarters_approver'
]
const ISOLATED_ROLE_LABEL = '未授权旧角色（已隔离）'
const LEGACY_READ_ONLY_LABEL = 'v0.9 历史原型/只读'
const LEGACY_WRITE_BLOCKED_MESSAGE = '正式客户端已隔离 v0.9 历史原型接口；请使用后续正式业务流程。'
const INVALID_REQUEST_PATH_MESSAGE = '请求路径不是规范的站内相对路径，已停止发送。'

// These paths are the v0.9 projection and workflow surface.  They are blocked for
// every method, including GET, so a stale/deep-linked page cannot display prototype
// balances or collapse the V1 state axes.  Future production modules must use a
// separately reviewed formal namespace rather than reopening these routes.
const LEGACY_REQUEST_PREFIXES = [
  '/auth/login',
  '/auth/change-password',
  '/auth/miniprogram/password-login',
  '/auth/users',
  '/dashboard',
  '/inventory',
  '/materials',
  '/warehouses',
  '/transfers',
  '/work-order-materials',
  '/stocktakes',
  '/media',
  '/integrations/oam/personnel',
  '/integrations/oam/orders'
]

function isSupportedRole(role) {
  return Object.prototype.hasOwnProperty.call(ROLE_LABELS, role)
}

function normalizeRoleCodes(value) {
  const source = typeof value === 'string' ? [value] : value
  if (!Array.isArray(source) || !source.length) return []
  if (source.some((role) => typeof role !== 'string' || !role.trim())) return []
  return Array.from(new Set(source.map((role) => role.trim())))
}

function formalRoleCodes(user) {
  if (!user || typeof user !== 'object' || !String(user.person_id || '').trim()) return []
  return normalizeRoleCodes(user.role_codes)
}

function isFormalAuthenticatedUser(user) {
  return !!(
    user &&
    typeof user === 'object' &&
    String(user.person_id || '').trim() &&
    typeof user.name === 'string' &&
    user.name.trim() &&
    formalRoleCodes(user).length
  )
}

function canUseOperationalClient(roleCodes) {
  const normalized = normalizeRoleCodes(roleCodes)
  return !!(
    normalized.length &&
    normalized.every(isSupportedRole) &&
    normalized.some((role) => OPERATIONAL_ROLES.includes(role))
  )
}

function roleLabel(roleCodes) {
  const normalized = normalizeRoleCodes(roleCodes)
  if (!normalized.length || !normalized.every(isSupportedRole)) return ISOLATED_ROLE_LABEL
  const primary = ROLE_DISPLAY_PRIORITY.find((role) => normalized.includes(role))
  return primary ? ROLE_LABELS[primary] : ISOLATED_ROLE_LABEL
}

function isValidAuthorizationVersion(value) {
  return Number.isSafeInteger(value) && value > 0
}

function hasFormalPermission(context, resource, action, fieldCode = '') {
  if (!context || !Array.isArray(context.permissions)) return false
  return context.permissions.some((permission) => (
    permission &&
    permission.resource === resource &&
    permission.action === action &&
    permission.field_code === fieldCode
  ))
}

function inventoryAccessDecision(context, expectedIdentity = null) {
  if (!context || typeof context !== 'object' || !String(context.person_id || '').trim()) {
    return { allowed: false, code: 'context_missing', message: '正式访问上下文缺失，已停止库存读取。' }
  }
  if (expectedIdentity && typeof expectedIdentity === 'object') {
    if (
      String(expectedIdentity.person_id || '').toLowerCase() !==
      String(context.person_id || '').toLowerCase()
    ) {
      return { allowed: false, code: 'identity_context_mismatch', message: '登录身份与授权上下文不一致，已停止库存读取。' }
    }
    if (
      !isValidAuthorizationVersion(expectedIdentity.authorization_version) ||
      expectedIdentity.authorization_version !== context.authorization_version
    ) {
      return { allowed: false, code: 'authorization_context_mismatch', message: '登录身份与授权版本不一致，已停止库存读取。' }
    }
  }
  if (context.access_mode !== 'active') {
    return {
      allowed: false,
      code: context.access_mode === 'restricted_handover' ? 'restricted_handover' : 'access_mode_invalid',
      message: context.access_mode === 'restricted_handover'
        ? '账号当前仅保留交接能力，库存读取已停止。'
        : '账号访问状态无效，已停止库存读取。'
    }
  }
  if (!isValidAuthorizationVersion(context.authorization_version)) {
    return { allowed: false, code: 'authorization_version_missing', message: '授权版本缺失或无效，已停止库存读取。' }
  }
  const roleCodes = normalizeRoleCodes(context.role_codes)
  if (!canUseOperationalClient(roleCodes)) {
    return { allowed: false, code: 'role_not_operational', message: '当前正式角色不能进入库存客户端。' }
  }
  if (!hasFormalPermission(context, 'inventory', 'read')) {
    return { allowed: false, code: 'inventory_permission_missing', message: '当前授权不包含库存只读权限。' }
  }
  return {
    allowed: true,
    code: 'allowed',
    message: '库存只读权限已验证。',
    authorizationVersion: context.authorization_version
  }
}

function stocktakeAccessDecision(context, expectedIdentity = null) {
  if (!context || typeof context !== 'object' || !String(context.person_id || '').trim()) {
    return { allowed: false, code: 'context_missing', message: '正式访问上下文缺失，已停止盘点读取。' }
  }
  if (expectedIdentity && typeof expectedIdentity === 'object') {
    if (
      String(expectedIdentity.person_id || '').toLowerCase() !==
      String(context.person_id || '').toLowerCase()
    ) {
      return { allowed: false, code: 'identity_context_mismatch', message: '登录身份与授权上下文不一致，已停止盘点读取。' }
    }
    if (
      !isValidAuthorizationVersion(expectedIdentity.authorization_version) ||
      expectedIdentity.authorization_version !== context.authorization_version
    ) {
      return { allowed: false, code: 'authorization_context_mismatch', message: '登录身份与授权版本不一致，已停止盘点读取。' }
    }
  }
  if (context.access_mode !== 'active') {
    return {
      allowed: false,
      code: context.access_mode === 'restricted_handover' ? 'restricted_handover' : 'access_mode_invalid',
      message: context.access_mode === 'restricted_handover'
        ? '账号当前仅保留交接能力，盘点读取已停止。'
        : '账号访问状态无效，已停止盘点读取。'
    }
  }
  if (!isValidAuthorizationVersion(context.authorization_version)) {
    return { allowed: false, code: 'authorization_version_missing', message: '授权版本缺失或无效，已停止盘点读取。' }
  }
  const roleCodes = normalizeRoleCodes(context.role_codes)
  if (!canUseOperationalClient(roleCodes)) {
    return { allowed: false, code: 'role_not_operational', message: '当前正式角色不能进入盘点客户端。' }
  }
  if (!hasFormalPermission(context, 'stocktake', 'read')) {
    return { allowed: false, code: 'stocktake_permission_missing', message: '当前授权不包含盘点只读权限。' }
  }
  return {
    allowed: true,
    code: 'allowed',
    message: '盘点只读权限已验证。',
    authorizationVersion: context.authorization_version,
    canCount: hasFormalPermission(context, 'stocktake', 'count'),
    canManage: hasFormalPermission(context, 'stocktake', 'manage'),
    canReviewRegion: hasFormalPermission(context, 'stocktake', 'review_region'),
    canReviewHeadquarters: hasFormalPermission(context, 'stocktake', 'review_headquarters'),
    canPostOpening: hasFormalPermission(context, 'stocktake', 'post_opening')
  }
}

function normalizedPath(path) {
  if (typeof path !== 'string' || !path || path.includes('#') || path.includes('\\')) return null
  const pathname = path.split('?', 1)[0]
  if (
    !pathname.startsWith('/') ||
    pathname.startsWith('//') ||
    pathname.includes('//') ||
    pathname.includes('%') ||
    /[\u0000-\u0020\u007f]/.test(pathname) ||
    pathname.split('/').some((segment) => segment === '.' || segment === '..')
  ) return null
  return pathname.replace(/\/+$/, '') || '/'
}

function blockedClientWriteReason(path, method = 'GET') {
  const cleanPath = normalizedPath(path)
  if (cleanPath === null) return INVALID_REQUEST_PATH_MESSAGE
  if (LEGACY_REQUEST_PREFIXES.some((prefix) => (
    cleanPath === prefix || cleanPath.startsWith(`${prefix}/`)
  ))) return LEGACY_WRITE_BLOCKED_MESSAGE
  return null
}

module.exports = {
  ROLE_LABELS,
  ISOLATED_ROLE_LABEL,
  LEGACY_READ_ONLY_LABEL,
  LEGACY_WRITE_BLOCKED_MESSAGE,
  INVALID_REQUEST_PATH_MESSAGE,
  isSupportedRole,
  normalizeRoleCodes,
  formalRoleCodes,
  isFormalAuthenticatedUser,
  canUseOperationalClient,
  roleLabel,
  isValidAuthorizationVersion,
  hasFormalPermission,
  inventoryAccessDecision,
  stocktakeAccessDecision,
  blockedClientWriteReason
}
