const catalog = require('../../data/knowledge-catalog.json')
const PAGE_SIZE = 20

Page({
  data: {
    query: '', categories: ['全部分类'], categoryIndex: 0, results: [], total: 0,
    page: 1, pageCount: 1, ready: false, verifiedDate: ''
  },
  onLoad() {
    const ready = catalog.status === 'ready' && catalog.items.length > 0
    this.setData({
      ready,
      categories: ['全部分类', ...new Set(ready ? catalog.items.map((item) => item.category).sort() : [])],
      verifiedDate: ready && catalog.verifiedAt ? catalog.verifiedAt.slice(0, 10) : ''
    })
    this.updateResults()
  },
  onQuery(event) {
    this.setData({ query: event.detail.value.slice(0, 160), page: 1 })
    this.updateResults()
  },
  onCategory(event) {
    this.setData({ categoryIndex: Number(event.detail.value), page: 1 })
    this.updateResults()
  },
  onClear() {
    this.setData({ query: '', categoryIndex: 0, page: 1 })
    this.updateResults()
  },
  onPrevious() {
    this.setData({ page: Math.max(1, this.data.page - 1) })
    this.updateResults()
  },
  onNext() {
    this.setData({ page: Math.min(this.data.pageCount, this.data.page + 1) })
    this.updateResults()
  },
  updateResults() {
    const words = this.data.query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    const category = this.data.categoryIndex ? this.data.categories[this.data.categoryIndex] : ''
    const items = this.data.ready ? catalog.items : []
    const matches = items.filter((item) => {
      const text = [item.code, item.name, item.category, item.model, item.note, item.sourceSheet].join(' ').toLowerCase()
      return (!category || item.category === category) && words.every((word) => text.includes(word))
    })
    this.setData({
      total: matches.length,
      pageCount: Math.max(1, Math.ceil(matches.length / PAGE_SIZE)),
      results: matches.slice((this.data.page - 1) * PAGE_SIZE, this.data.page * PAGE_SIZE)
        .map((item) => ({ ...item, key: `${item.sourceSheet}:${item.sourceRow}:${item.code}` }))
    })
  },
  onCopySource() {
    wx.setClipboardData({ data: catalog.sourceUrl })
  }
})
