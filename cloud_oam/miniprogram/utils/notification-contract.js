function fail(message) { throw new Error(message) }

function text(value, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function validateItem(item) {
  if (!item || typeof item !== 'object' || typeof item.delivery_id !== 'string') fail('通知响应契约无效')
  const statuses = ['queued', 'sending', 'sent', 'delivered', 'read', 'failed', 'cancelled']
  if (!statuses.includes(item.status) || !['wechat', 'sms', 'feishu'].includes(item.channel)) fail('通知状态契约无效')
  const payload = item.payload && typeof item.payload === 'object' && !Array.isArray(item.payload) ? item.payload : {}
  return Object.assign({}, item, {
    title: text(payload.title, text(payload.subject, 'RSC个人仓通知')),
    body: text(payload.body, text(payload.summary, '请进入业务页面查看详情。')),
    payload
  })
}

function validatePage(value) {
  if (!value || value.schema_version !== '1.0' || !Array.isArray(value.items) || !Number.isInteger(value.unread_count) || value.unread_count < 0) fail('通知列表响应契约无效')
  return Object.assign({}, value, { items: value.items.map(validateItem) })
}

function validateRead(value) {
  if (!value || value.schema_version !== '1.0') fail('通知已读响应契约无效')
  return Object.assign({}, value, { item: validateItem(value.item) })
}

module.exports = { validatePage, validateRead }
