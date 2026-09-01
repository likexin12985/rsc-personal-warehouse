// v0.9 quarantine: this page is intentionally not registered in app.json.
// Keep the stub non-operational until the formal request/allocation/fulfilment APIs exist.
Page({
  onLoad() {
    wx.showModal({
      title: '旧原型写入口已停用',
      content: 'v0.9 Transfer 仅保留历史只读查询，不能代替正式需求与履约流程。',
      showCancel: false,
      success: () => wx.navigateBack({ fail: () => wx.switchTab({ url: '/pages/transfers/index' }) })
    })
  }
})
