const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/material-request-option-contract')

const PERSON_ID = '10000000-0000-4000-8000-000000000001'
const WORK_ORDER_ID = '20000000-0000-4000-8000-000000000001'
const WORK_ORDER_2_ID = '20000000-0000-4000-8000-000000000002'
const CONTEXT = { person_id: PERSON_ID, authorization_version: 7 }

function item(overrides = {}) {
  return Object.assign({
    work_order_id: WORK_ORDER_ID,
    work_order_no: 'WO-20260901-001',
    status: 'active',
    source_system_code: 'starcharge_oam',
    source_external_id: 'OAM-WO-20260901-001',
    source_version: 'v-20260901-070000',
    source_updated_at: '2026-09-01T07:00:00+08:00',
    synced_at: '2026-09-01T07:10:00+08:00',
    freshness_status: 'fresh'
  }, overrides)
}

function page(overrides = {}) {
  return Object.assign({
    schema_version: '1.0',
    person_id: PERSON_ID,
    authorization_version: 7,
    items: [item()],
    next_after_id: WORK_ORDER_2_ID
  }, overrides)
}

test('formal work-order options accept only the requester-scoped minimal contract', () => {
  const result = contract.validatePage(page(), CONTEXT)
  assert.equal(result.person_id, PERSON_ID)
  assert.equal(result.authorization_version, 7)
  assert.equal(result.items[0].work_order_no, 'WO-20260901-001')
  assert.equal(contract.validateQuery('WO-20260901'), 'WO-20260901')
  assert.equal(contract.validateQuery(''), '')

  const detail = contract.validateDetail({
    schema_version: '1.0',
    person_id: PERSON_ID,
    authorization_version: 7,
    item: item()
  }, CONTEXT, WORK_ORDER_ID)
  assert.equal(detail.item.work_order_id, WORK_ORDER_ID)
})

test('formal work-order options fail closed on drift, ambiguity and stale authorization', () => {
  assert.throws(
    () => contract.validatePage(Object.assign(page(), { address: 'forbidden' }), CONTEXT),
    /精确包含正式字段/
  )
  assert.throws(
    () => contract.validatePage(page({ authorization_version: 8 }), CONTEXT),
    /人员或授权版本已变化/
  )
  assert.throws(
    () => contract.validatePage(page({ items: [item(), item()] }), CONTEXT),
    /重复工单/
  )
  assert.throws(
    () => contract.validatePage(page({ next_after_id: WORK_ORDER_ID }), CONTEXT),
    /游标指向当前页对象/
  )
  assert.throws(
    () => contract.validatePage(page({ items: [item({ status: 'closed' })] }), CONTEXT),
    /状态无效/
  )
  assert.throws(
    () => contract.validatePage(page({ items: [item({ freshness_status: 'stale' })] }), CONTEXT),
    /新鲜度无效/
  )
  assert.throws(
    () => contract.validatePage(page({ items: [item({ source_system_code: 'legacy' })] }), CONTEXT),
    /来源系统无效/
  )
  assert.throws(
    () => contract.validatePage(page({
      items: [item({
        source_updated_at: '2026-09-01T07:11:00+08:00',
        synced_at: '2026-09-01T07:10:00+08:00'
      })]
    }), CONTEXT),
    /不能晚于本次同步时间/
  )
  assert.throws(
    () => contract.validateQuery(' WO-1'),
    /检索词无效/
  )
  assert.throws(
    () => contract.validateDetail({
      schema_version: '1.0',
      person_id: PERSON_ID,
      authorization_version: 7,
      item: item({ work_order_id: WORK_ORDER_2_ID })
    }, CONTEXT, WORK_ORDER_ID),
    /目标工单不一致/
  )
})
