// v0.9 quarantine: keep this stub non-operational even if somebody accidentally
// re-registers the page. Formal clients use SMS only and never change passwords.
Page({
  onLoad() {
    wx.showModal({
      title: '密码入口已停用',
      content: '正式客户端仅支持手机验证码登录，不提供密码登录、改密或微信登录。',
      showCancel: false,
      success: () => wx.navigateBack({ fail: () => wx.reLaunch({ url: '/pages/login/index' }) })
    })
  }
})
