const session = require('./utils/session')
session.initializeSessionState()

App({
  globalData: {
    user: null
  },

  onLaunch() {
    // Public knowledge is the entry page, including after an interrupted login.
    // initializeSessionState still quarantines incomplete credentials on upgrades.
  },

  setUser(user) {
    const accepted = session.setUser(user || null)
    this.globalData.user = accepted ? user || null : null
    return accepted
  }
})
