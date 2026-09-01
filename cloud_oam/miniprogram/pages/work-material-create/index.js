const api = require('../../utils/api')
const session = require('../../utils/session')

const MANAGER_ROLES = ['admin', 'provincial_manager']

Page({
  data: {
    loading: true,
    submitting: false,
    user: null,
    warehouseIndex: -1,
    holderIndex: 0,
    warehouses: [],
    holderOptions: [],
    workOrderNumber: '',
    note: '',
    materialQuery: '',
    materialResults: [],
    items: [],
    conditionLabels: ['好件', '旧件']
  },

  onLoad() {
    if (!session.ensureLogin()) return
    this.loadOptions()
  },

  async loadOptions() {
    try {
      const user = await api.get('/auth/me')
      const canManage = MANAGER_ROLES.includes(user.role)
      const requests = [api.get('/warehouses'), api.get('/materials', { limit: 30 })]
      if (canManage) requests.push(api.get('/auth/users'))
      const result = await Promise.all(requests)
      this.setData({
        user,
        warehouses: result[0].filter((row) => row.warehouse_level === 'network'),
        materialResults: result[1],
        holderOptions: canManage ? result[2] : [user]
      })
    } catch (error) {
      wx.showToast({ title: error.message || '基础数据加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  changePicker(event) {
    this.setData({ [event.currentTarget.dataset.field]: Number(event.detail.value) })
  },

  bindField(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value })
  },

  async searchMaterials() {
    try {
      this.setData({ materialResults: await api.get('/materials', { q: this.data.materialQuery.trim(), limit: 50 }) })
    } catch (error) {
      wx.showToast({ title: error.message || '物料查询失败', icon: 'none' })
    }
  },

  addMaterial(event) {
    const material = this.data.materialResults.find((item) => item.id === event.currentTarget.dataset.id)
    if (!material) return
    if (this.data.items.some((item) => item.material_id === material.id)) {
      wx.showToast({ title: '该物料已加入', icon: 'none' })
      return
    }
    this.setData({
      items: this.data.items.concat([{
        material_id: material.id,
        code: material.code,
        name: material.name,
        unit: material.unit,
        quantity: 1,
        condition: 'good',
        conditionIndex: 0
      }])
    })
  },

  updateQuantity(event) {
    const index = Number(event.currentTarget.dataset.index)
    const items = this.data.items.slice()
    items[index].quantity = Number(event.detail.value)
    this.setData({ items })
  },

  changeCondition(event) {
    const index = Number(event.currentTarget.dataset.index)
    const items = this.data.items.slice()
    items[index].conditionIndex = Number(event.detail.value)
    items[index].condition = items[index].conditionIndex === 1 ? 'old' : 'good'
    this.setData({ items })
  },

  removeItem(event) {
    const index = Number(event.currentTarget.dataset.index)
    const items = this.data.items.slice()
    items.splice(index, 1)
    this.setData({ items })
  },

  async submit() {
    const workOrderNumber = this.data.workOrderNumber.trim()
    const warehouse = this.data.warehouses[this.data.warehouseIndex]
    const holder = this.data.holderOptions[this.data.holderIndex]
    if (workOrderNumber.length < 3) {
      wx.showToast({ title: '请填写工单号', icon: 'none' })
      return
    }
    if (!warehouse) {
      wx.showToast({ title: '请选择所属仓库', icon: 'none' })
      return
    }
    if (!holder) {
      wx.showToast({ title: '请选择领用人', icon: 'none' })
      return
    }
    if (!this.data.items.length || this.data.items.some((item) => !Number.isInteger(item.quantity) || item.quantity <= 0)) {
      wx.showToast({ title: '请添加物料并填写正确数量', icon: 'none' })
      return
    }
    const confirmed = await new Promise((resolve) => {
      wx.showModal({
        title: '确认占用',
        content: `工单 ${workOrderNumber} 共占用 ${this.data.items.length} 种物料。`,
        confirmColor: '#e65318',
        success: (result) => resolve(result.confirm)
      })
    })
    if (!confirmed) return
    this.setData({ submitting: true })
    try {
      await api.post('/work-order-materials/batch', {
        work_order_number: workOrderNumber,
        warehouse_id: warehouse.id,
        user_id: holder.id,
        note: this.data.note.trim(),
        items: this.data.items.map((item) => ({
          material_id: item.material_id,
          condition: item.condition,
          quantity: item.quantity
        }))
      })
      wx.showToast({ title: '占用已登记', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 500)
    } catch (error) {
      wx.showToast({ title: error.message || '登记失败', icon: 'none', duration: 3000 })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
