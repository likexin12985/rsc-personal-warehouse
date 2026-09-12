const api = require('../../utils/api')
const session = require('../../utils/session')
const {
  formalRoleCodes,
  hasFormalPermission,
  inventoryAccessDecision,
  roleLabel
} = require('../../utils/production-guard')
const { validateInventorySummary } = require('../../utils/inventory-contract')

function projectedAtLabel(value) {
  if (!value) return '尚无已过账流水'
  const parsed = new Date(value)
  if (!Number.isFinite(parsed.getTime())) return '时间证据无效'
  const pad = (part) => String(part).padStart(2, '0')
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
}

Page({
  data: {
    loading: true,
    user: null,
    avatarText: '',
    roleLabel: '',
    organizationName: '',
    authorizationText: '未验证',
    materialRequestAccessAllowed: false,
    stocktakeAccessAllowed: false,
    inventoryAccessAllowed: false,
    workOrderAccessAllowed: false,
    inventorySummary: null,
    accessStatusLabel: '正在校验',
    accessStatusTone: 'neutral',
    moduleTitle: '正在读取正式库存账',
    moduleMessage: '正在校验身份、权限、账本游标和期初状态。',
    projectionCursorText: '未读取',
    projectedAtText: '未读取',
    openingStatusLabel: '未读取'
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  async load() {
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this.setData({
      loading: true,
      user: null,
      avatarText: '',
      roleLabel: '',
      organizationName: '',
      authorizationText: '未验证',
      materialRequestAccessAllowed: false,
      stocktakeAccessAllowed: false,
      inventoryAccessAllowed: false,
      workOrderAccessAllowed: false,
      inventorySummary: null,
      accessStatusLabel: '正在校验',
      accessStatusTone: 'neutral',
      moduleTitle: '正在读取正式库存账',
      moduleMessage: '正在校验身份、权限、账本游标和期初状态。',
      projectionCursorText: '未读取',
      projectedAtText: '未读取',
      openingStatusLabel: '未读取'
    })
    try {
      const [user, access] = await Promise.all([
        api.get('/auth/me'),
        api.get('/access/context')
      ])
      if (generation !== this._loadGeneration) return
      if (!getApp().setUser(user)) {
        session.ensureLogin()
        return
      }
      this.setData({
        user,
        avatarText: user.name ? user.name.charAt(0) : '我',
        roleLabel: roleLabel(formalRoleCodes(user)),
        organizationName: user.organization_name || '组织信息待同步'
      })

      const decision = inventoryAccessDecision(access, user)
      const materialRequestAccessAllowed = (
        String(access.person_id || '').toLowerCase() ===
          String(user.person_id || '').toLowerCase() &&
        Number.isSafeInteger(user.authorization_version) &&
        user.authorization_version === access.authorization_version &&
        access.access_mode === 'active' &&
        hasFormalPermission(access, 'material_request', 'read')
      )
      const stocktakeAccessAllowed = (
        String(access.person_id || '').toLowerCase() ===
          String(user.person_id || '').toLowerCase() &&
        Number.isSafeInteger(user.authorization_version) &&
        user.authorization_version === access.authorization_version &&
        access.access_mode === 'active' &&
        hasFormalPermission(access, 'stocktake', 'read')
      )
      this.setData({
        authorizationText: Number.isSafeInteger(access.authorization_version)
          ? `v${access.authorization_version}`
          : '未验证',
        materialRequestAccessAllowed,
        stocktakeAccessAllowed,
        inventoryAccessAllowed: decision.allowed,
        workOrderAccessAllowed: decision.allowed && hasFormalPermission(access, 'work_order_material', 'read'),
        accessStatusLabel: decision.allowed ? '库存权限已验证' : '库存访问已停止',
        accessStatusTone: decision.allowed ? 'success' : 'danger',
        moduleTitle: decision.allowed ? '正在读取正式库存账' : '库存访问已失败关闭',
        moduleMessage: decision.allowed ? '权限已验证，正在读取正式账本投影。' : decision.message
      })
      if (!decision.allowed) return

      const summary = validateInventorySummary(
        await api.get('/v1/inventory/summary')
      )
      if (generation !== this._loadGeneration) return
      const openingEstablished = summary.opening_balance_status === 'established'
      this.setData({
        inventoryAccessAllowed: true,
        inventorySummary: summary,
        accessStatusLabel: openingEstablished ? '正式库存账只读可用' : '账本可读，期初待建立',
        accessStatusTone: openingEstablished ? 'success' : 'warning',
        moduleTitle: openingEstablished ? '正式库存账已建立' : '期初库存尚未建立',
        moduleMessage: openingEstablished
          ? '期初状态已由服务端确认。不同物料和计量单位不会在本页相加，库存数量须按物料维度读取。'
          : '正式账本投影已就绪，但首次实物盘点、区域负责人复核和蔚来总部管理员复核尚未形成完整事实，因此不展示任何库存数量。',
        projectionCursorText: String(summary.ledger_cursor),
        projectedAtText: projectedAtLabel(summary.projected_at),
        openingStatusLabel: openingEstablished ? '已建立' : '未建立'
      })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this.setData({
        user: null,
        avatarText: '',
        roleLabel: '',
        organizationName: '',
        authorizationText: '未验证',
        materialRequestAccessAllowed: false,
        stocktakeAccessAllowed: false,
        inventoryAccessAllowed: false,
        workOrderAccessAllowed: false,
        inventorySummary: null,
        accessStatusLabel: '上下文校验失败',
        accessStatusTone: 'danger',
        moduleTitle: '库存访问已失败关闭',
        moduleMessage: '无法确认正式身份、授权或库存响应契约，未展示任何库存数据。',
        projectionCursorText: '未读取',
        projectedAtText: '未读取',
        openingStatusLabel: '未确认'
      })
      wx.showToast({ title: error.message || '正式库存读取失败', icon: 'none' })
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  }
})
