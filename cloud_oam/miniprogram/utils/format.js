const { ROLE_LABELS } = require('./production-guard')

const STATUS = {
  pending_approval: ['待审批', 'warning'],
  draft: ['草稿', 'neutral'],
  dispatched: ['待收货', 'warning'],
  received: ['已入库', 'success'],
  cancelled: ['已取消', 'danger'],
  rejected: ['已驳回', 'danger'],
  occupied: ['占用中', 'info'],
  consumed: ['已消耗', 'warning'],
  recovered: ['已回收', 'success'],
  released: ['已释放', 'neutral'],
  pending: ['待盘点', 'warning'],
  in_progress: ['盘点中', 'info'],
  submitted: ['待复核', 'info'],
  closed: ['已完成', 'success']
}

const TRANSFER_TYPE = {
  internal: '内部调拨',
  standard_transfer: '标准调拨',
  external_inbound: '外部到货',
  oam_inbound: 'OAM到货',
  personal_request: '个人需求申请',
  regional_request: '区域申请',
  provider_request: '服务商申请',
  bad_return: '坏件退回',
  stagnant_return: '呆滞件退回'
}

const CONDITION = {
  good: '好件',
  old: '旧件',
  bad: '坏件'
}

const ROLE = ROLE_LABELS

function statusMeta(status) {
  const value = STATUS[status] || [status || '未知', 'neutral']
  return { label: value[0], tone: value[1] }
}

function dateTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function maskMobile(value) {
  if (!value || value.length < 7) return value || '-'
  return `${value.slice(0, 3)}****${value.slice(-4)}`
}

module.exports = { statusMeta, dateTime, maskMobile, CONDITION, ROLE, TRANSFER_TYPE }
