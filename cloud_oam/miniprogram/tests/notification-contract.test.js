const test = require('node:test')
const assert = require('node:assert/strict')
const contract = require('../utils/notification-contract')

const item = {
  delivery_id: 'delivery-1', event_id: 'event-1', event_type: 'shipment',
  business_type: 'shipment', business_id: 'shipment-1', channel: 'wechat',
  status: 'sent', payload: { title: '已发运', body: '包裹已发出' },
  occurred_at: '2026-09-15T02:00:00Z', created_at: '2026-09-15T02:00:00Z',
  sent_at: '2026-09-15T02:00:00Z', delivered_at: null, read_at: null
}

test('notification page contract exposes safe display fields', () => {
  const page = contract.validatePage({ schema_version: '1.0', items: [item], next_after_id: null, unread_count: 1 })
  assert.equal(page.items[0].title, '已发运')
  assert.equal(page.items[0].body, '包裹已发出')
})

test('notification contract rejects unknown delivery status', () => {
  assert.throws(() => contract.validatePage({ schema_version: '1.0', items: [{ ...item, status: 'unknown' }], next_after_id: null, unread_count: 0 }), /状态契约无效/)
})
