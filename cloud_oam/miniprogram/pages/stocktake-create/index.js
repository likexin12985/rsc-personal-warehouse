const api = require('../../utils/api')
const session = require('../../utils/session')

Page({
  data: {
    loading: true,
    submitting: false,
    warehouses: [],
    allUsers: [],
    assignees: [],
    warehouseIndex: -1,
    assigneeIndex: -1,
    deadline: '',
    note: ''
  },

  onLoad() {
    if (!session.ensureLogin()) return
    this.loadOptions()
  },

  async loadOptions() {
    try {
      const [warehouses, users] = await Promise.all([
        api.get('/warehouses'),
        api.get('/auth/users')
      ])
      this.setData({ warehouses, allUsers: users, assignees: users })
    } catch (error) {
      wx.showToast({ title: error.message || '基础数据加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  changeWarehouse(event) {
    const warehouseIndex = Number(event.detail.value)
    const warehouse = this.data.warehouses[warehouseIndex]
    const assignees = this.data.allUsers.filter((user) => !user.province || user.province === warehouse.province)
    this.setData({ warehouseIndex, assignees, assigneeIndex: -1 })
  },

  changeAssignee(event) {
    this.setData({ assigneeIndex: Number(event.detail.value) })
  },

  changeDeadline(event) {
    this.setData({ deadline: event.detail.value })
  },

  bindNote(event) {
    this.setData({ note: event.detail.value })
  },

  async submit() {
    const warehouse = this.data.warehouses[this.data.warehouseIndex]
    const assignee = this.data.assignees[this.data.assigneeIndex]
    if (!warehouse || !assignee) {
      wx.showToast({ title: '请选择仓库和盘点人', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const created = await api.post('/stocktakes', {
        warehouse_id: warehouse.id,
        assignee_id: assignee.id,
        deadline: this.data.deadline ? `${this.data.deadline}T23:59:59+08:00` : null,
        note: this.data.note.trim()
      })
      wx.redirectTo({ url: `/pages/stocktake-detail/index?id=${created.id}` })
    } catch (error) {
      wx.showToast({ title: error.message || '创建失败', icon: 'none', duration: 2800 })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
