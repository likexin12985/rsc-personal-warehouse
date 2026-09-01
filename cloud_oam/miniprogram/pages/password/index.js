// v0.9 quarantine: keep this stub non-operational even if somebody accidentally
// re-registers the page. Formal clients use WeChat or SMS and never change passwords.
Page({
  onLoad() {
    wx.showModal({
      title: '密码入口已停用',
      content: '正式客户端仅支持微信或手机验证码登录，不提供密码登录或改密。',
      showCancel: false,
      success: () => wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/login/index' }) })
    })
  }
})
