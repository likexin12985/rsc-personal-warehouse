Page({
  data: { query: '' },
  input(event) { this.setData({ query: event.detail.value.slice(0, 160) }) },
  search() { wx.navigateTo({ url: `/pages/knowledge/index?query=${encodeURIComponent(this.data.query)}` }) },
  enterWarehouse() { wx.switchTab({ url: '/pages/home/index' }) },
  openPc() { wx.setClipboardData({ data: 'https://rscwz.cn/xx' }) }
})
