const test = require('node:test')
const assert = require('node:assert/strict')
const contract = require('../utils/stock-return-receiving-contract')
const f = require('./fixtures/stock-return-receiving-data.cjs')
for (const tracked of [false, true]) {
  for (const type of ['accepted', 'shortage', 'damaged', 'rejected', 'wrong_material', 'wrong_serial']) test(`receiver history ${tracked ? 'SN' : 'quantity'} ${type} retains independent quantities`, () => {
    const raw = f.history(tracked, [f.receipt(tracked, type)])
    assert.equal(contract.validateHistory(raw, f.expected), raw)
    assert.ok(!JSON.stringify(raw).includes('posting_transaction_id'))
  })
  test(`shortage does not spend ${tracked ? 'SN' : 'fractional'} acceptance budget`, () => {
    const raw = f.history(tracked, [f.receipt(tracked, 'shortage'), f.receipt(tracked, 'accepted', 2)])
    assert.equal(contract.validateHistory(raw, f.expected).lines[0].unconfirmed_qty, '0.000')
  })
}
test('receiver scope, original binding, overage and double counted SN cannot be displayed', () => {
  const mutations = [
    raw => { raw.person_id = f.id(300) }, raw => { raw.package.receiver_person_id = f.id(300) },
    raw => { raw.receipts[0].shipment_id = f.id(300) }, raw => { raw.receipts[0].target_custody_assignment_id = f.id(300) },
    raw => { raw.receipts[0].lines[0].material_id = f.id(300) }, raw => { raw.receipts[0].lines[0].accepted_qty = '2.000' },
    raw => { raw.receipts[0].lines[0].accepted_serials[0].serial_no = 'foreign SN' },
    raw => { raw.receipts[0].lines[0].previously_accepted_qty = '1.000' }, raw => { raw.lines[0].accepted_qty = '0.000' },
    raw => { raw.lines[0].unconfirmed_serials = f.parcel(true).lines[0].serials },
    raw => { raw.receipts.push(f.receipt(true, 'accepted', 2)) }, raw => { raw.package.lines.push(raw.package.lines[0]) },
    raw => { raw.receipts[0].lines[0].exceptions = [] }, raw => { raw.receipts[0].status = 'posted' }
  ]
  for (const mutate of mutations) { const raw = f.history(true, [f.receipt(true, 'damaged')]); mutate(raw); assert.throws(() => contract.validateHistory(raw, f.expected)) }
})
test('allowlists reject sensitive or posting fields at every displayed layer', () => {
  const positions = [raw => raw, raw => raw.package, raw => raw.package.lines[0], raw => raw.package.lines[0].serials[0],
    raw => raw.receipts[0], raw => raw.receipts[0].lines[0], raw => raw.receipts[0].lines[0].exceptions[0], raw => raw.lines[0]]
  for (const position of positions) for (const key of ['qr_code', 'stock_account_id', 'posting_transaction_id']) {
    const raw = f.history(true, [f.receipt(true, 'damaged')]); position(raw)[key] = 'unreviewed'; assert.throws(() => contract.validateHistory(raw, f.expected))
  }
})
test('formal multiline evidence descriptions preserve Unicode and the server length bound', () => {
  const raw = f.history(true, [f.receipt(true, 'damaged')]), detail = raw.receipts[0].lines[0].exceptions[0]
  detail.description = '外箱破损\n实物复核：\n' + '🔎'.repeat(980)
  assert.equal(contract.validateHistory(raw, f.expected), raw)
  detail.description = '🔎'.repeat(1001); assert.throws(() => contract.validateHistory(raw, f.expected))
})
test('directory keeps unavailable parcels isolated and validates complete page coordinates', () => {
  const raw = f.directory(); raw.items.push({ verification_status: 'unavailable', shipment_id: f.id(30),
    code: 'stock_return_receiving_verification_required', message: '原接收责任待核验' })
  assert.equal(contract.validateDirectory(raw, { ...f.expected, shipmentId: null }), raw)
  raw.items.reverse(); assert.throws(() => contract.validateDirectory(raw, f.expected))
  assert.throws(() => contract.validateDirectory(f.directory(), f.expected, f.id(2)))
  const invalid = f.directory(); invalid.next_after_id = f.id(2); assert.throws(() => contract.validateDirectory(invalid, f.expected))
})
