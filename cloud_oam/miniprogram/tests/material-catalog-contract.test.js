const assert = require('node:assert/strict')
const test = require('node:test')

const contract = require('../utils/material-catalog-contract')

const MATERIAL_ID = '50000000-0000-4000-8000-000000000001'

function page() {
  return {
    schema_version: '1.0',
    items: [{
      material_id: MATERIAL_ID,
      sku_code: 'SKU-A',
      name: '交流接触器',
      specification: '32A',
      base_unit: '件',
      tracking_mode: 'serial',
      quantity_scale: 0,
      allow_fraction: false,
      source_updated_at: '2026-09-01T07:00:00+08:00'
    }],
    next_after_id: '50000000-0000-4000-8000-000000000002'
  }
}

test('formal material catalog accepts only the minimal picker projection', () => {
  const result = contract.validatePage(page())
  assert.equal(result.items[0].sku_code, 'SKU-A')
  assert.equal(result.items[0].tracking_mode, 'serial')
  assert.equal(contract.validateQuery('SKU A'), 'SKU A')
})

test('formal material catalog fails closed on drift, duplicates and ambiguous query text', () => {
  assert.throws(() => contract.validatePage(Object.assign(page(), { legacy: true })))
  const duplicate = page()
  duplicate.items.push(Object.assign({}, duplicate.items[0]))
  assert.throws(() => contract.validatePage(duplicate))
  assert.throws(() => contract.validateQuery(' SKU-A'))
  assert.throws(() => contract.validateQuery('SKU-A\n'))
})

test('v2 preserves explicit unknown source time and legacy v1 stays strict', () => {
  const current = Object.assign(page(), { schema_version: '2.0' })
  current.items[0].source_updated_at = null
  assert.equal(contract.validatePage(current).items[0].source_updated_at, null)
  assert.equal(contract.validatePage(current).schema_version, '2.0')
  assert.equal(contract.validatePage(Object.assign(page(), { schema_version: '2.0' })).items[0].source_updated_at, page().items[0].source_updated_at)
  assert.throws(() => contract.validatePage(Object.assign({}, current, { schema_version: '1.0' })))
  assert.throws(() => contract.validatePage(Object.assign({}, current, { schema_version: '3.0' })))
  for (const value of [undefined, '', '未知', false, 0, '2026-09-01T07:00:00']) {
    current.items[0].source_updated_at = value
    assert.throws(() => contract.validatePage(current))
  }
  delete current.items[0].source_updated_at
  assert.throws(() => contract.validatePage(current))
})
