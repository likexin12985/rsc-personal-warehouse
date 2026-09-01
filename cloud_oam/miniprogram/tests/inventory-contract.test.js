const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/inventory-contract')

const PERSON_ID = '10000000-0000-4000-8000-000000000001'
const LOCATION_ID = '20000000-0000-4000-8000-000000000001'

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function unopenedSummary() {
  return {
    schema_version: '1.0',
    projection_status: 'ready',
    opening_balance_status: 'not_established',
    projected_at: null,
    ledger_cursor: 0,
    scopes: [{ scope_type: 'person', scope_id: PERSON_ID }],
    quantity_status: 'opening_not_established',
    physical_in_stock_qty: null,
    available_qty: null,
    reserved_qty: null,
    committed_qty: null,
    frozen_qty: null,
    physical_in_transit_qty: null,
    expected_supply_qty: null,
    expected_supply_status: 'not_available'
  }
}

function unopenedAccount() {
  return {
    stock_account_id: '30000000-0000-4000-8000-000000000001',
    owner_org_id: '40000000-0000-4000-8000-000000000001',
    owner_org_code: 'OWNER-JS',
    owner_org_name: '资产所有组织',
    location_owner_org_id: '50000000-0000-4000-8000-000000000001',
    location_owner_org_code: 'OPS-JS',
    location_owner_org_name: '库位运营组织',
    location_id: LOCATION_ID,
    location_code: 'PERSON-E001',
    location_name: '工程师个人仓',
    location_type: 'personal',
    location_parent_id: '60000000-0000-4000-8000-000000000001',
    custodian_person_id: PERSON_ID,
    custodian_person_name: '工程师甲',
    material_id: '70000000-0000-4000-8000-000000000001',
    sku_code: 'SKU-001',
    material_name: '测试物料',
    base_unit: '个',
    tracking_mode: 'none',
    condition_code: 'new',
    availability_bucket: 'available',
    lot_id: null,
    lot_no: null,
    quantity_status: 'opening_not_established',
    quantity: null,
    balance_version: 0,
    ledger_cursor: 0
  }
}

function personalWarehouse() {
  return {
    schema_version: '1.0',
    projection_status: 'ready',
    opening_balance_status: 'not_established',
    projected_at: null,
    ledger_cursor: 0,
    person_id: PERSON_ID,
    location_id: LOCATION_ID,
    location_code: 'PERSON-E001',
    location_name: '工程师个人仓',
    location_status: 'active',
    custody_effective_from: '2026-08-30T00:00:00Z',
    items: []
  }
}

function establishedPersonalWarehouse() {
  const warehouse = personalWarehouse()
  warehouse.opening_balance_status = 'established'
  warehouse.projected_at = '2026-08-30T12:00:00+00:00'
  warehouse.ledger_cursor = 7
  const account = unopenedAccount()
  account.quantity_status = 'available'
  account.quantity = '12.345'
  account.balance_version = 2
  account.ledger_cursor = 7
  warehouse.items = [account]
  return warehouse
}

test('unopened summary remains null and preserves asset dimensions', () => {
  const summary = unopenedSummary()
  assert.equal(contract.validateInventorySummary(summary), summary)
  assert.equal(summary.available_qty, null)
})

test('reviewed empty opening scope may be established at ledger cursor zero', () => {
  const summary = unopenedSummary()
  summary.opening_balance_status = 'established'
  summary.quantity_status = 'material_filter_required'

  assert.equal(contract.validateInventorySummary(summary), summary)
  assert.equal(summary.ledger_cursor, 0)
  assert.equal(summary.projected_at, null)

  const warehouse = personalWarehouse()
  warehouse.opening_balance_status = 'established'
  assert.equal(
    contract.validatePersonalWarehouse(warehouse, PERSON_ID),
    warehouse
  )
  assert.deepEqual(warehouse.items, [])
})

test('summary rejects leaked opening quantity, cross-material total and unknown enum', () => {
  const leaked = unopenedSummary()
  leaked.available_qty = '0.000'
  assert.throws(
    () => contract.validateInventorySummary(leaked),
    (error) => error.code === 'inventory_contract_unopened_quantity'
  )

  const crossMaterial = unopenedSummary()
  crossMaterial.opening_balance_status = 'established'
  crossMaterial.quantity_status = 'material_filter_required'
  crossMaterial.projected_at = '2026-08-30T12:00:00Z'
  crossMaterial.ledger_cursor = 1
  crossMaterial.available_qty = '12.000'
  assert.throws(
    () => contract.validateInventorySummary(crossMaterial),
    (error) => error.code === 'inventory_contract_cross_material_total'
  )

  const unknown = unopenedSummary()
  unknown.quantity_status = 'estimated'
  assert.throws(
    () => contract.validateInventorySummary(unknown),
    (error) => error.code === 'inventory_contract_enum_unknown'
  )
})

test('summary rejects missing cursor and any unscoped available state', () => {
  const missing = unopenedSummary()
  delete missing.ledger_cursor
  assert.throws(
    () => contract.validateInventorySummary(missing),
    (error) => error.code === 'inventory_contract_field_missing'
  )

  const established = unopenedSummary()
  established.opening_balance_status = 'established'
  established.quantity_status = 'available'
  established.projected_at = '2026-08-30T12:00:00Z'
  established.ledger_cursor = 1
  for (const field of [
    'physical_in_stock_qty',
    'available_qty',
    'reserved_qty',
    'committed_qty',
    'frozen_qty',
    'physical_in_transit_qty'
  ]) established[field] = '0.000'
  established.available_qty = 0
  assert.throws(
    () => contract.validateInventorySummary(established),
    (error) => error.code === 'inventory_contract_enum_unknown'
  )
})

test('unopened personal warehouse returns only location and custody facts', () => {
  const warehouse = personalWarehouse()
  assert.equal(
    contract.validatePersonalWarehouse(warehouse, PERSON_ID),
    warehouse
  )
  assert.deepEqual(warehouse.items, [])
})

test('personal warehouse with no configured location is explicit and empty', () => {
  const warehouse = personalWarehouse()
  warehouse.location_id = null
  warehouse.location_code = null
  warehouse.location_name = null
  warehouse.location_status = null
  warehouse.custody_effective_from = null
  warehouse.items = []
  assert.equal(
    contract.validatePersonalWarehouse(warehouse, PERSON_ID).location_id,
    null
  )
})

test('personal warehouse rejects identity, unopened rows and location leakage', () => {
  const wrongPerson = personalWarehouse()
  assert.throws(
    () => contract.validatePersonalWarehouse(
      wrongPerson,
      '10000000-0000-4000-8000-000000000099'
    ),
    (error) => error.code === 'inventory_contract_person_mismatch'
  )

  const unopenedRows = personalWarehouse()
  unopenedRows.items = [unopenedAccount()]
  assert.throws(
    () => contract.validatePersonalWarehouse(unopenedRows, PERSON_ID),
    (error) => error.code === 'inventory_contract_unopened_items'
  )

  const wrongLocation = clone(establishedPersonalWarehouse())
  wrongLocation.items[0].location_id = '20000000-0000-4000-8000-000000000099'
  assert.throws(
    () => contract.validatePersonalWarehouse(wrongLocation, PERSON_ID),
    (error) => error.code === 'inventory_contract_location_leak'
  )
})

test('established personal balance remains an exact decimal string', () => {
  const warehouse = establishedPersonalWarehouse()
  const validated = contract.validatePersonalWarehouse(warehouse, PERSON_ID)
  assert.equal(validated.items[0].quantity, '12.345')
  assert.equal(typeof validated.items[0].quantity, 'string')
  assert.notEqual(
    validated.items[0].owner_org_id,
    validated.items[0].location_owner_org_id
  )
})

test('display labels fail closed for future unknown states', () => {
  assert.equal(contract.conditionLabel('damaged'), '坏件')
  assert.equal(contract.availabilityLabel('in_transit'), '在途')
  assert.equal(contract.conditionLabel('future'), '未知成色')
  assert.equal(contract.availabilityLabel('future'), '未知状态')
})
