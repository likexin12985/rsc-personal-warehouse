const test = require('node:test')
const assert = require('node:assert/strict')
const command = require('../utils/my-receipt-command')
const recovery = require('../utils/my-receipt-recovery-store')
const f = require('./fixtures/my-receipt')

test('receipt request fingerprint agrees with the backend normalized command', () => {
  assert.equal(command.requestHash(f.REQUEST, f.PERSON, f.body()), 'dd48dbad05900424f1857242a79712b671a22aeb1993b8ecdccefdf379e78060')
  const reordered = f.body(); reordered.lines[0].accepted_qty = '1'
  assert.equal(command.requestHash(f.REQUEST, f.PERSON, reordered), f.marker().request_hash)
})

test('quantity form preserves full numeric precision and enforces per-material scale', () => {
  const raw = f.response(), line = raw.lines[0]
  Object.assign(line, { shipped_qty: '999999999999999.999', accepted_qty: '0.000', unconfirmed_qty: '999999999999999.999', tracking_mode: 'none', quantity_scale: 3, allow_fraction: true, remaining_serials: [] })
  const draft = { [f.LINE]: { condition: 'normal', accepted: '999999999999999.999', rejected: '0' } }
  assert.equal(command.buildCommand(f.candidate(raw), draft, f.NOW).lines[0].accepted_qty, '999999999999999.999')
  for (const value of ['1e3', 'NaN', '-1', '0.0001', 1, ' 1', '1000000000000000']) assert.throws(() => command.quantity(value))
  line.quantity_scale = 2; line.shipped_qty = line.unconfirmed_qty = '999999999999999.990'
  assert.throws(() => command.buildCommand(f.candidate(raw), draft, f.NOW), /超过|精度/)
})

test('abnormal SN acceptance requires exact serial outcomes and completed evidence selection', () => {
  const draft = { [f.LINE]: { condition: 'rejected', serials: { [f.SERIAL]: 'rejected' } } }
  assert.throws(() => command.buildCommand(f.candidate(), draft, f.NOW), /凭证/)
  draft[f.LINE].evidenceFileId = f.FILE
  const payload = command.buildCommand(f.candidate(), draft, f.NOW)
  assert.equal(payload.lines[0].accepted_qty, '0.000')
  assert.equal(payload.lines[0].rejected_qty, '1.000')
  draft[f.LINE].serials[f.SERIAL] = 'accepted'
  assert.throws(() => command.buildCommand(f.candidate(), draft, f.NOW), /合格/)
  draft[f.LINE].serials = { [f.PERSON]: 'rejected' }
  assert.throws(() => command.buildCommand(f.candidate(), draft, f.NOW), /最新/)
})

test('confirmed reply must reproduce original quantities, serials, timestamps and command hash', () => {
  const marker = f.marker()
  assert.equal(command.validateResult(f.result(), marker).receipt_no, 'RCT-TEST')
  const submillisecondMismatch = f.result(); submillisecondMismatch.received_at = '2026-09-12T08:00:00.000001Z'
  assert.throws(() => command.validateResult(submillisecondMismatch, marker))
  for (const change of [r => { r.person_id = f.REQUEST }, r => { r.received_at = '2026-09-12T08:01:00Z' }, r => { r.lines[0].accepted_qty = '2.000' }, r => { r.lines[0].accepted_serial_ids = [] }, r => { r.status = 'posted' }, r => { r.request_hash = '0'.repeat(64) }]) {
    const r = f.result(); change(r); assert.throws(() => command.validateResult(r, marker))
  }
  assert.equal(command.validateLookup({ schema_version: '1.0', lookup_status: 'not_observed', command: null }, marker), null)
  assert.throws(() => command.validateLookup({ schema_version: '1.0', lookup_status: 'not_observed', command: f.result() }, marker))
})

test('durable marker excludes write keys, quantities, SN and request payload', async () => {
  const storage = f.storage(), store = recovery.createStore({ storage, state: { active: new Set(), faults: new Set() } }), marker = f.marker()
  await store.withLease(marker, lease => lease.persist(marker))
  const raw = [...storage.values.values()][0]
  for (const forbidden of ['idempotency', 'token', 'accepted_qty', f.SERIAL, 'lines', 'exception_evidence']) assert.equal(raw.includes(forbidden), false)
  const restarted = recovery.createStore({ storage, state: { active: new Set(), faults: new Set() } })
  assert.deepEqual(restarted.read(marker).value, marker)
  await assert.rejects(restarted.withLease(marker, lease => lease.persist(marker)), /禁止覆盖/)
  await restarted.withLease(marker, lease => lease.clearExact(marker))
  assert.equal(restarted.read(marker).kind, 'missing')
})

test('corrupt storage, failed readback, concurrent pages and changed cleanup anchors fail closed', async () => {
  const marker = f.marker(), storage = f.storage(), store = recovery.createStore({ storage, state: { active: new Set(), faults: new Set() } })
  let release
  const held = store.withLease(marker, async () => new Promise(resolve => { release = resolve }))
  await assert.rejects(store.withLease(marker, () => {}), /正在处理/)
  release(); await held
  await store.withLease(marker, lease => lease.persist(marker))
  await assert.rejects(store.withLease(marker, lease => lease.clearExact({ ...marker, request_hash: '0'.repeat(64) })), /禁止清除/)
  storage.values.set([...storage.values.keys()][0], '{bad json')
  assert.equal(store.read(marker).kind, 'unavailable')
  await assert.rejects(store.withLease(marker, () => {}))
  const lost = f.storage(); lost.setStorageSync = () => {}
  const broken = recovery.createStore({ storage: lost, state: { active: new Set(), faults: new Set() } })
  await assert.rejects(broken.withLease(marker, lease => lease.persist(marker)), /写后核验/)
})
