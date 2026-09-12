const api = require('../../utils/api')
const session = require('../../utils/session')
const {
  formalRoleCodes,
  hasFormalPermission,
  inventoryAccessDecision,
  roleLabel
} = require('../../utils/production-guard')
const {
  availabilityLabel,
  conditionLabel,
  validatePersonalWarehouse
} = require('../../utils/inventory-contract')
const { PC_ORIGIN } = require('../../utils/config')

Page({
  data: {
    loading: true,
    user: null,
    avatarText: '',
    roleLabel: '',
    identityStatus: '',
    organizationName: '',
    authorizationText: '未验证',
    inventoryStatus: '不可访问',
    inventoryAccessMessage: '正式访问上下文尚未校验',
    inventoryStatusTone: 'neutral',
    personalWarehouse: null,
    workOrderAccessAllowed: false,
    personalItems: [],
    personalLocationName: '未读取',
    personalLedgerCursor: '未读取',
    pcOrigin: PC_ORIGIN
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
      identityStatus: '正在校验正式身份',
      organizationName: '',
      authorizationText: '未验证',
      personalWarehouse: null,
      workOrderAccessAllowed: false,
      personalItems: [],
      inventoryStatus: '正在校验',
      inventoryStatusTone: 'neutral',
      inventoryAccessMessage: '正在校验身份、权限和个人仓响应。',
      personalLocationName: '未读取',
      personalLedgerCursor: '未读取'
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
      const decision = inventoryAccessDecision(access, user)
      this.setData({
        user,
        avatarText: user.name ? user.name.charAt(0) : '我',
        roleLabel: roleLabel(formalRoleCodes(user)),
        identityStatus: decision.allowed ? '正式访问上下文已验证' : '库存访问已失败关闭',
        organizationName: user.organization_name || '组织信息待同步',
        authorizationText: Number.isSafeInteger(access.authorization_version)
          ? `v${access.authorization_version}`
          : '未验证',
        inventoryStatus: decision.allowed ? '正在读取' : '不可访问',
        workOrderAccessAllowed: decision.allowed && hasFormalPermission(access, 'work_order_material', 'read'),
        inventoryStatusTone: decision.allowed ? 'neutral' : 'danger',
        inventoryAccessMessage: decision.allowed ? '库存权限已验证，正在读取正式个人仓。' : decision.message
      })
      if (!decision.allowed) return

      const warehouse = validatePersonalWarehouse(
        await api.get('/v1/inventory/personal/me'),
        user.person_id
      )
      if (generation !== this._loadGeneration) return
      const openingEstablished = warehouse.opening_balance_status === 'established'
      const locationConfigured = warehouse.location_id !== null
      const personalItems = openingEstablished
        ? warehouse.items.map((item) => Object.assign({}, item, {
          availabilityLabel: availabilityLabel(item.availability_bucket),
          conditionLabel: conditionLabel(item.condition_code)
        }))
        : []
      this.setData({
        personalWarehouse: warehouse,
        personalItems,
        personalLocationName: locationConfigured ? warehouse.location_name : '尚未配置',
        personalLedgerCursor: String(warehouse.ledger_cursor),
        inventoryStatus: !locationConfigured
          ? '个人仓未配置'
          : (openingEstablished ? '只读可用' : '期初待建立'),
        inventoryStatusTone: !locationConfigured
          ? 'warning'
          : (openingEstablished ? 'success' : 'warning'),
        inventoryAccessMessage: !locationConfigured
          ? '管理员尚未为当前人员建立有效个人仓库位和保管责任。'
          : (openingEstablished
              ? '个人仓数量来自正式不可变流水投影；页面不自行汇总不同物料。'
              : '个人仓库位与保管责任已识别，但首次盘点及两级复核尚未完整建立，所有数量保持隐藏。')
      })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this.setData({
        user: null,
        avatarText: '',
        roleLabel: '',
        organizationName: '',
        identityStatus: '正式访问上下文校验失败',
        authorizationText: '未验证',
        inventoryStatus: '不可访问',
        inventoryStatusTone: 'danger',
        inventoryAccessMessage: '无法确认正式身份、授权或个人仓响应契约，未展示任何库存数据。',
        personalWarehouse: null,
        workOrderAccessAllowed: false,
        personalItems: [],
        personalLocationName: '未读取',
        personalLedgerCursor: '未读取'
      })
      wx.showToast({ title: error.message || '个人仓读取失败', icon: 'none' })
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  copyPcAddress() {
    if (!PC_ORIGIN) {
      wx.showToast({ title: '当前构建未配置 PC 地址', icon: 'none' })
      return
    }
    wx.setClipboardData({ data: PC_ORIGIN })
  },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '退出后需要重新验证账号。',
      confirmColor: '#b43c2d',
      success: async (result) => {
        if (!result.confirm) return
        const refreshToken = session.getRefreshToken()
        api.clearExplicitSession()
        if (refreshToken) {
          await api.post('/auth/miniprogram/logout', { refresh_token: refreshToken }).catch(() => undefined)
        }
        wx.reLaunch({ url: '/pages/login/index' })
      }
    })
  }
})
