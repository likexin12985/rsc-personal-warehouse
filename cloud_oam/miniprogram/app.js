const session = require('./utils/session')
const startupSession = session.initializeSessionState()

App({
  globalData: {
    user: startupSession.user
  },

  onLaunch() {
    if (startupSession.forceLogin) {
      wx.reLaunch({ url: '/pages/login/index' })
    }
  },

  setUser(user) {
    const accepted = session.setUser(user || null)
    this.globalData.user = accepted ? user || null : null
    return accepted
  }
})
