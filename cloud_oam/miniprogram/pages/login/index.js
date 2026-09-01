const api = require('../../utils/api')
const session = require('../../utils/session')
const {
  canUseOperationalClient,
  formalRoleCodes,
  isFormalAuthenticatedUser,
  roleLabel
} = require('../../utils/production-guard')

Page({
  data: {
    loading: false,
    checking: true,
    options: { wechat_enabled: false, sms_enabled: false, sms_interval_seconds: 60 },
    mode: 'unavailable',
    mobile: '',
    code: '',
    countdown: 0
  },

  async onLoad() {
    try {
      const options = await api.get('/auth/login-options')
      const formalOptions = {
        wechat_enabled: !!options.wechat_enabled,
        sms_enabled: !!options.sms_enabled,
        sms_interval_seconds: options.sms_interval_seconds || 60
      }
      const mode = formalOptions.wechat_enabled ? 'wechat' : formalOptions.sms_enabled ? 'sms' : 'unavailable'
      this.setData({ options: formalOptions, mode })
      if (session.getToken() || session.getRefreshToken()) {
        const user = await api.get('/auth/me')
        if (!getApp().setUser(user)) return
        this.routeAfterLogin(user)
        return
      }
      if (options.wechat_enabled) {
        try {
          await this.loginWithWechat()
          return
        } catch (error) {
          if (error.status !== 428 && error.status !== 401) this.showError(error)
        }
      }
    } catch (error) {
      if (error.status !== 401) this.showError(error)
    } finally {
      this.setData({ checking: false })
    }
  },

  onUnload() {
    if (this.timer) clearInterval(this.timer)
  },

  setMode(event) {
    this.setData({ mode: event.currentTarget.dataset.mode })
  },

  bindField(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value.trim() })
  },

  async requestCode() {
    if (!/^1[3-9]\d{9}$/.test(this.data.mobile)) {
      wx.showToast({ title: '请输入正确手机号', icon: 'none' })
      return
    }
    try {
      const idempotencyKey = api.createIdempotencyKey()
      await api.post(
        '/auth/sms/request',
        { mobile: this.data.mobile },
        { idempotencyKey }
      )
      wx.showToast({ title: '验证码已发送', icon: 'success' })
      this.startCountdown(this.data.options.sms_interval_seconds || 60)
    } catch (error) {
      this.showError(error)
    }
  },

  startCountdown(seconds) {
    if (this.timer) clearInterval(this.timer)
    this.setData({ countdown: seconds })
    this.timer = setInterval(() => {
      const next = this.data.countdown - 1
      this.setData({ countdown: Math.max(0, next) })
      if (next <= 0) clearInterval(this.timer)
    }, 1000)
  },

  async submit() {
    const { mobile, code, mode } = this.data
    if (mode === 'wechat') return
    if (mode !== 'sms') {
      wx.showToast({ title: '微信和验证码登录均未启用', icon: 'none' })
      return
    }
    if (!/^1[3-9]\d{9}$/.test(mobile)) {
      wx.showToast({ title: '请输入正确手机号', icon: 'none' })
      return
    }
    if (!/^\d{4,8}$/.test(code)) {
      wx.showToast({ title: '请输入验证码', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    try {
      const client = this.clientMetadata()
      const result = await api.post('/auth/miniprogram/sms-login', Object.assign({ mobile, code }, client))
      await api.establishExplicitSession(result)
      this.routeAfterLogin(result.user)
    } catch (error) {
      this.showError(error)
    } finally {
      this.setData({ loading: false })
    }
  },

  async wechatPhoneLogin(event) {
    const phoneCode = event.detail && event.detail.code
    if (!phoneCode) {
      wx.showToast({ title: '未授权手机号，可改用验证码登录', icon: 'none' })
      if (this.data.options.sms_enabled) this.setData({ mode: 'sms' })
      return
    }
    this.setData({ loading: true })
    try {
      await this.loginWithWechat(phoneCode)
    } catch (error) {
      this.showError(error)
    } finally {
      this.setData({ loading: false })
    }
  },

  async loginWithWechat(phoneCode = '') {
    const loginCode = await this.getWechatLoginCode()
    const result = await api.post('/auth/miniprogram/wechat-login', Object.assign({
      login_code: loginCode,
      phone_code: phoneCode || null
    }, this.clientMetadata()))
    await api.establishExplicitSession(result)
    this.routeAfterLogin(result.user)
  },

  getWechatLoginCode() {
    return new Promise((resolve, reject) => {
      wx.login({
        timeout: 10000,
        success(result) {
          if (result.code) resolve(result.code)
          else reject(new Error('微信登录凭证获取失败'))
        },
        fail(error) {
          reject(new Error(error.errMsg || '微信登录凭证获取失败'))
        }
      })
    })
  },

  clientMetadata() {
    let deviceName = '微信小程序'
    try {
      const info = wx.getDeviceInfo ? wx.getDeviceInfo() : wx.getSystemInfoSync()
      deviceName = [info.brand, info.model].filter(Boolean).join(' ') || deviceName
    } catch (_) {}
    return {
      device_id: session.getDeviceId(),
      device_name: deviceName
    }
  },

  routeAfterLogin(user) {
    const roleCodes = formalRoleCodes(user)
    if (!isFormalAuthenticatedUser(user) || !canUseOperationalClient(roleCodes)) {
      const identity = roleCodes.length ? roleLabel(roleCodes) : '未验证正式身份'
      session.clearSession()
      wx.showModal({
        title: '身份已隔离',
        content: `${identity}当前不能进入库存客户端。正式外部审批端尚未接入，请确认有效角色授权。`,
        showCancel: false
      })
      return
    }
    wx.switchTab({ url: '/pages/home/index' })
  },

  showError(error) {
    wx.showToast({ title: error.message || '操作失败', icon: 'none', duration: 2600 })
  }
})
