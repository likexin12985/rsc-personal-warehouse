const api = require('../../utils/api')

Page({
  data: {
    loading: true,
    attachment: null,
    localPath: '',
    isVideo: false
  },

  onLoad() {
    this.getOpenerEventChannel().on('media', (attachment) => {
      this.setData({ attachment, isVideo: attachment.mimeType.indexOf('video') === 0 })
      this.loadFile(attachment)
    })
  },

  async loadFile(attachment) {
    try {
      const localPath = await api.download(attachment.url)
      this.setData({ localPath })
    } catch (error) {
      wx.showToast({ title: error.message || '凭证加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  previewImage() {
    if (this.data.localPath) wx.previewImage({ urls: [this.data.localPath] })
  }
})
