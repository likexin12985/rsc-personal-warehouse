const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const guard = require('../utils/production-guard')
const api = require('../utils/api')
const config = require('../utils/config')
const appConfig = require('../app.json')

test('formal roles render with retired roles failed closed', () => {
  assert.equal(guard.roleLabel('admin'), '蔚来总部管理员')
  assert.equal(guard.roleLabel('provincial_manager'), '区域公司负责人')
  assert.equal(guard.roleLabel('technician'), '工程师')
  assert.equal(guard.roleLabel('star_headquarters_approver'), '星星总部审批人（外部审批身份）')
  assert.equal(guard.roleLabel(['technician', 'admin']), '蔚来总部管理员')
  assert.equal(guard.canUseOperationalClient(['technician']), true)
  assert.equal(guard.canUseOperationalClient(['technician', 'admin']), true)
  assert.equal(guard.canUseOperationalClient('star_headquarters_approver'), false)
  assert.equal(guard.canUseOperationalClient('auditor'), false)
  assert.equal(guard.canUseOperationalClient('warehouse_manager'), false)
  assert.equal(guard.canUseOperationalClient(['admin', 'unexpected']), false)
  assert.equal(guard.roleLabel('unexpected'), guard.ISOLATED_ROLE_LABEL)
})

test('formal user identity comes from person_id and role_codes only', () => {
  const user = {
    person_id: '00000000-0000-4000-8000-000000000001',
    name: '正式用户',
    role_codes: ['technician', 'admin']
  }
  assert.deepEqual(guard.formalRoleCodes(user), ['technician', 'admin'])
  assert.equal(guard.isFormalAuthenticatedUser(user), true)
  assert.deepEqual(guard.formalRoleCodes({ name: '旧用户', role: 'admin' }), [])
  assert.equal(guard.isFormalAuthenticatedUser({ name: '旧用户', role: 'admin' }), false)
})

test('formal inventory access requires active context, permission and authorization version', () => {
  const allowed = {
    person_id: '00000000-0000-4000-8000-000000000010',
    access_mode: 'active',
    authorization_version: 7,
    role_codes: ['technician'],
    permissions: [
      { resource: 'inventory', action: 'read', field_code: '' }
    ]
  }
  assert.deepEqual(guard.inventoryAccessDecision(allowed), {
    allowed: true,
    code: 'allowed',
    message: '库存只读权限已验证。',
    authorizationVersion: 7
  })
  assert.equal(guard.inventoryAccessDecision(allowed, {
    person_id: allowed.person_id,
    authorization_version: 7
  }).allowed, true)

  const blocked = [
    [Object.assign({}, allowed, { access_mode: 'restricted_handover' }), 'restricted_handover'],
    [Object.assign({}, allowed, { permissions: [] }), 'inventory_permission_missing'],
    [Object.assign({}, allowed, { authorization_version: undefined }), 'authorization_version_missing'],
    [Object.assign({}, allowed, { authorization_version: 0 }), 'authorization_version_missing'],
    [Object.assign({}, allowed, { role_codes: ['star_headquarters_approver'] }), 'role_not_operational'],
    [allowed, 'identity_context_mismatch', {
      person_id: '00000000-0000-4000-8000-000000000099',
      authorization_version: 7
    }],
    [allowed, 'authorization_context_mismatch', {
      person_id: allowed.person_id,
      authorization_version: 8
    }]
  ]
  for (const [context, expectedCode, identity] of blocked) {
    const result = guard.inventoryAccessDecision(context, identity)
    assert.equal(result.allowed, false)
    assert.equal(result.code, expectedCode)
  }
})

test('formal stocktake access keeps read and each write capability independent', () => {
  const context = {
    person_id: '00000000-0000-4000-8000-000000000010',
    access_mode: 'active',
    authorization_version: 9,
    role_codes: ['technician'],
    permissions: [
      { resource: 'stocktake', action: 'read', field_code: '' },
      { resource: 'stocktake', action: 'count', field_code: '' }
    ]
  }
  assert.deepEqual(guard.stocktakeAccessDecision(context, {
    person_id: context.person_id,
    authorization_version: 9
  }), {
    allowed: true,
    code: 'allowed',
    message: '盘点只读权限已验证。',
    authorizationVersion: 9,
    canCount: true,
    canManage: false,
    canReviewRegion: false,
    canReviewHeadquarters: false,
    canPostOpening: false
  })

  const missingRead = Object.assign({}, context, {
    permissions: [{ resource: 'stocktake', action: 'count', field_code: '' }]
  })
  assert.equal(
    guard.stocktakeAccessDecision(missingRead).code,
    'stocktake_permission_missing'
  )
  assert.equal(
    guard.stocktakeAccessDecision(context, {
      person_id: '00000000-0000-4000-8000-000000000099',
      authorization_version: 9
    }).code,
    'identity_context_mismatch'
  )
})

test('all v0.9 business requests are blocked, including history GET', () => {
  const blocked = [
    ['/auth/miniprogram/password-login', 'POST'],
    ['/auth/change-password', 'POST'],
    ['/auth/users/id', 'DELETE'],
    ['/inventory/adjust', 'POST'],
    ['/materials', 'POST'],
    ['/materials/id', 'PUT'],
    ['/warehouses', 'POST'],
    ['/transfers', 'POST'],
    ['/transfers/legacy/approve', 'POST'],
    ['/transfers/legacy/dispatch', 'POST'],
    ['/transfers/legacy/receive', 'POST'],
    ['/transfers/legacy/cancel', 'POST'],
    ['/work-order-materials/batch', 'POST'],
    ['/work-order-materials/id/recover', 'POST'],
    ['/stocktakes', 'POST'],
    ['/stocktakes/id/items/item-id', 'PUT'],
    ['/media/transfer/legacy', 'POST'],
    ['/media/stocktake/legacy', 'POST'],
    ['/integrations/oam/personnel/id/enable', 'POST'],
    ['/dashboard', 'GET'],
    ['/inventory', 'GET'],
    ['/inventory?mine=true', 'GET'],
    ['/transfers?limit=200', 'GET'],
    ['/transfers/legacy', 'GET'],
    ['/stocktakes', 'GET'],
    ['/work-order-materials', 'GET'],
    ['/materials', 'GET'],
    ['/warehouses', 'GET'],
    ['/media/transfer/legacy', 'GET'],
    ['/integrations/oam/orders', 'GET']
  ]
  for (const [path, method] of blocked) {
    assert.match(guard.blockedClientWriteReason(path, method), /已隔离/)
  }
  assert.equal(guard.blockedClientWriteReason('/access/context', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/auth/me', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/inventory/summary', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/inventory/personal/me', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/opening', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/opening/task', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/personal', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/task/rounds/round/scopes/scope/initial-count', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/task/rounds/round/scopes/scope/recount-count', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/task/rounds/round/recount', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/task/reconcile', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktakes/task/close', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/stocktake-options/assignees', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/material-requests', 'GET'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/material-requests/task', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/files/upload-intents', 'POST'), null)
  assert.equal(guard.blockedClientWriteReason('/v1/files/90000000-0000-4000-8000-000000000001/complete', 'POST'), null)
})

test('non-canonical API paths fail closed before route matching', () => {
  const invalid = [
    '/inventory#client-fragment',
    '/v1/inventory/summary#client-fragment',
    '/v1%2finventory/summary',
    '/v1/%2e%2e/inventory',
    '/v1/../inventory',
    '/v1//inventory',
    '\\inventory',
    'https://example.com/v1/inventory/summary',
    '//example.com/v1/inventory/summary'
  ]
  for (const path of invalid) {
    assert.equal(
      guard.blockedClientWriteReason(path, 'GET'),
      guard.INVALID_REQUEST_PATH_MESSAGE
    )
  }
})

test('request wrapper rejects quarantined reads and writes before wx.request', async () => {
  let transported = false
  global.wx = {
    request() { transported = true }
  }
  await assert.rejects(
    api.post('/transfers/legacy/dispatch'),
    (error) => error.status === 403 && /已隔离/.test(error.message)
  )
  await assert.rejects(
    api.get('/inventory'),
    (error) => error.status === 403 && /已隔离/.test(error.message)
  )
  await assert.rejects(
    api.get('/v1/inventory/summary#fragment'),
    (error) => error.status === 403 && /规范/.test(error.message)
  )
  assert.equal(transported, false)
  delete global.wx
})

test('release build has no live endpoint default and fails before transport', async () => {
  assert.equal(config.envVersion, 'release')
  assert.equal(config.API_BASE_URL, '')
  assert.equal(config.reviewedHttpsUrl('http://example.com/api'), '')
  assert.equal(config.reviewedHttpsUrl('https://user@example.com/api'), '')
  assert.equal(
    config.reviewedHttpsUrl('https://approved.example.com/api/'),
    'https://approved.example.com/api'
  )
  let transported = false
  global.wx = {
    request() { transported = true }
  }
  await assert.rejects(
    api.get('/auth/login-options'),
    (error) => error.status === 503 && /未配置服务地址/.test(error.message)
  )
  assert.equal(transported, false)
  delete global.wx
})

test('release navigation registers only reviewed formal workbench, personal warehouse, demand, stocktake and profile pages', () => {
  const quarantinedPages = [
    'pages/inventory/index',
    'pages/transfers/index',
    'pages/transfer-detail/index',
    'pages/media-preview/index',
    'pages/oam-orders/index',
    'pages/transfer-create/index',
    'pages/work-materials/index',
    'pages/work-material-create/index',
    'pages/stocktakes/index',
    'pages/stocktake-detail/index',
    'pages/stocktake-create/index'
  ]
  for (const page of quarantinedPages) {
    assert.equal(appConfig.pages.includes(page), false)
    assert.equal(appConfig.tabBar.list.some((item) => item.pagePath === page), false)
  }
  assert.deepEqual(appConfig.pages, [
    'pages/login/index',
    'pages/home/index',
    'pages/formal-personal-warehouse/index',
    'pages/formal-my-receiving/index',
    'pages/formal-my-inbound/index',
    'pages/formal-my-receipt/index',
    'pages/formal-material-requests/index',
    'pages/formal-operational-stocktakes/index',
    'pages/formal-operational-stocktake-detail/index',
    'pages/formal-stocktakes/index',
    'pages/formal-stocktake-detail/index',
    'pages/profile/index'
  ])
  assert.deepEqual(appConfig.tabBar.list.map((item) => item.pagePath), [
    'pages/home/index',
    'pages/formal-personal-warehouse/index',
    'pages/formal-material-requests/index',
    'pages/formal-stocktakes/index',
    'pages/profile/index'
  ])
})

test('registered formal pages contain no v0.9 business request or deep link', () => {
  const source = [
    'pages/home/index.js',
    'pages/home/index.wxml',
    'pages/formal-personal-warehouse/index.js',
    'pages/formal-personal-warehouse/index.wxml',
    'pages/formal-my-receiving/index.js',
    'pages/formal-my-receiving/index.wxml',
    'pages/formal-my-inbound/index.js',
    'pages/formal-my-inbound/index.wxml',
    'pages/formal-my-receipt/index.js',
    'pages/formal-my-receipt/index.wxml',
    'pages/formal-material-requests/index.js',
    'pages/formal-material-requests/index.wxml',
    'pages/formal-operational-stocktakes/index.js',
    'pages/formal-operational-stocktakes/index.wxml',
    'pages/formal-operational-stocktake-detail/index.js',
    'pages/formal-operational-stocktake-detail/index.wxml',
    'pages/formal-stocktakes/index.js',
    'pages/formal-stocktakes/index.wxml',
    'pages/formal-stocktake-detail/index.js',
    'pages/formal-stocktake-detail/index.wxml',
    'pages/profile/index.js',
    'pages/profile/index.wxml'
  ].map((relativePath) => fs.readFileSync(
    path.resolve(__dirname, '..', relativePath),
    'utf8'
  )).join('\n')
  const forbidden = [
    "api.get('/dashboard')",
    "api.get('/inventory')",
    "api.get('/transfers')",
    "api.get('/stocktakes')",
    "api.get('/work-order-materials')",
    '/pages/inventory',
    '/pages/transfers'
  ]
  for (const value of forbidden) assert.equal(source.includes(value), false)
  assert.equal(source.includes("api.get('/v1/inventory/summary')"), true)
  assert.equal(source.includes("api.get('/v1/inventory/personal/me')"), true)
  assert.equal(source.includes("api.get('/v1/stocktakes/opening')"), true)
  assert.equal(source.includes('/v1/material-requests'), true)
  assert.equal(source.includes('/v1/stocktakes/opening/${this.data.taskId}'), true)
  assert.match(source, /chooseRequestAttachment/)
  assert.match(source, /chooseExternalEvidence/)
  assert.match(source, /chooseCountEvidence/)
  const operationalContractSource = [
    'utils/formal-stocktake-contract.js',
    'utils/formal-stocktake-adapter.js',
    'pages/formal-operational-stocktake-detail/index.js',
    'pages/formal-operational-stocktake-detail/index.wxml'
  ].map((relativePath) => fs.readFileSync(
    path.resolve(__dirname, '..', relativePath),
    'utf8'
  )).join('\n')
  assert.match(operationalContractSource, /\/v1\/stocktakes\/\$\{task\}\/reconcile/)
  assert.match(operationalContractSource, /\/v1\/stocktakes\/\$\{task\}\/close/)
  assert.doesNotMatch(operationalContractSource, /api\.(?:get|post)\('\/stocktakes|\/media\//)
  const uploadUiSource = [
    'pages/formal-material-requests/index.js',
    'pages/formal-material-requests/index.wxml',
    'pages/formal-operational-stocktake-detail/index.js',
    'pages/formal-operational-stocktake-detail/index.wxml'
  ].map((relativePath) => fs.readFileSync(
    path.resolve(__dirname, '..', relativePath),
    'utf8'
  )).join('\n')
  assert.doesNotMatch(uploadUiSource, /附件上传尚未实现|已有正式 file_id|data-field="evidenceFileId"|bindEvidence|\/media/)
  const uploadSource = fs.readFileSync(
    path.resolve(__dirname, '../utils/formal-file-upload.js'),
    'utf8'
  )
  assert.match(uploadSource, /\/v1\/files\/upload-intents/)
  assert.match(uploadSource, /\/v1\/files\/\$\{intent\.fileId\}\/complete/)
  assert.equal(uploadSource.includes('wx.uploadFile'), false)
})
