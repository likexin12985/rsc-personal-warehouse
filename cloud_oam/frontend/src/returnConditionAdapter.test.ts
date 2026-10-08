import { expect, it, vi } from 'vitest';
import fixtures from './test-fixtures/return-condition/command-hashes.json';
import { createConditionAdapter, conditionPermissions } from './returnConditionAdapter';
import { prepare, action } from './returnConditionCommands';
import { conditionFixture, conditionId } from './returnConditionHistoryFixtures';

async function harness(sample = fixtures.samples[0]) {
  const p = await prepare(sample.person_id, sample.authorization_version, sample.inbound_line_id, sample.original);
  const who = { person_id: p.person_id, authorization_version: 1, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin', 'provincial_manager'] };
  const me = { ...who, name: '合成人员', employee_no: 'TEST', organization_code: 'TEST', organization_name: '合成组织' };
  const access = { ...who, assignments: who.role_codes.map((role_code, i) => ({ assignment_id: conditionId(i + 101), role_code,
    scope_type: 'national', scope_id: '*', valid_from: '2020-01-01T00:00:00Z', valid_to: null })), permissions: [
      { resource: 'inventory', action: 'read', field_code: '' }, ...['read', ...new Set(Object.values(conditionPermissions))].map(action => ({ resource: 'stock_operation', action, field_code: '' }))] };
  const request = vi.fn(async (path: string, init?: RequestInit): Promise<unknown> => {
    expect(init?.cache).toBe('no-store'); if (path === '/auth/me') return structuredClone(me);
    if (path === '/access/context') return structuredClone(access); return { synthetic: true };
  });
  return { p, me, access, request, t: createConditionAdapter(p.person_id, request) };
}
it.each(fixtures.samples)('$kind transports complete originals with exact headers and no automatic retry', async sample => {
  const h = await harness(sample);
  for (const [method, path] of [['submit', 'commands'], ['lookup', 'request-lookup'], ['seal', 'request-seal']] as const) {
    await h.t[method](h.p); const [url, init] = h.request.mock.lastCall!;
    expect(url).toBe('/v1/stock-operations/loss-reports/return-condition-corrections/' + path);
    expect(init?.method).toBe('POST'); const headers = new Headers(init?.headers);
    expect(headers.get('Idempotency-Key')).toBe(h.p.original.idempotency_key);
    expect(headers.get('X-Request-ID')).toBe(h.p.original.request_id);
    expect(JSON.parse(init!.body as string)).toEqual(method === 'submit' ? h.p.original : { operator_person_id: h.p.person_id, original: h.p.original });
  }
  h.request.mockRejectedValue(new Error('timeout'));
  await expect(h.t.submit(h.p)).rejects.toThrow('timeout'); expect(h.request).toHaveBeenCalledTimes(4);
});
it('keeps read recovery available after write revocation and rejects changed identity', async () => {
  const h = await harness(); h.access.permissions = h.access.permissions.filter(p => p.action === 'read');
  expect(await h.t.context('submit')).toMatchObject({ can_read: true, can_write: false });
  h.me.authorization_version++;
  await expect(h.t.context('submit')).rejects.toThrow();
});
it('matches the complete claimed serial subset before a new submission', async () => {
  const h = await harness(), c = h.p.original; if (c.action !== 'submit_return_condition') throw new Error();
  const context = await h.t.context('submit');
  const source = { stage: 'source_evidence_only', inbound_line_id: h.p.inbound_line_id, inbound_id: conditionId(80), root_disposition_id: conditionId(81),
    source_account_id: conditionId(82), material_id: conditionId(83), lot_id: null, location_id: conditionId(84), custodian_person_id: h.p.person_id,
    recorded_condition: 'new', required_condition: 'damaged', source_status: 'recorded_stock_retained', historical_damaged_quantity: '2.000',
    account_balance_quantity: '2.000', claimable_quantity: '2.000', tracking_mode: 'serial',
    serials: c.serial_verifications.map(s => ({ serial_id: s.serial_id, serial_no: s.serial_no, qr_code: s.qr_code, claimable_for_correction: true })),
    observed_ledger_cursor: 15, expected_source_hash: c.expected_source_hash, checked_at: '2026-10-06T01:00:00Z', physical_verification_required: true, posting_allowed: false };
  h.request.mockResolvedValue(source); await h.t.verifySource(h.p, context);
  for (const changed of [{ claimable_quantity: null }, { expected_source_hash: '0'.repeat(64) }, { claimable_quantity: '0.000' },
    { posting_allowed: true }, { serials: [{ ...source.serials[0], claimable_for_correction: false }, source.serials[1]] }]) {
    h.request.mockResolvedValue({ ...source, ...changed }); await expect(h.t.verifySource(h.p, context)).rejects.toThrow();
  }
  expect(h.request.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
it('requires the selected latest case event and requester before settlement', async () => {
  const h = await harness(fixtures.samples.find(s => s.kind === 'execute')!), history = conditionFixture();
  history.events[0].fact.actor_person_id = h.p.person_id;
  const p = await prepare(h.p.person_id, 1, history.inbound_line_id, { ...h.p.original,
    case_id: history.cases[0].case_id, expected_event_id: history.cases[0].latest_event_id,
    expected_event_hash: history.cases[0].latest_event_hash, serial_verifications: [] });
  const context = await h.t.context(action(p.original)); h.request.mockResolvedValue(history);
  await h.t.verifySource(p, context);
  const wrong = await prepare(p.person_id, 1, p.inbound_line_id, { ...p.original, expected_event_id: conditionId(999) });
  await expect(h.t.verifySource(wrong, context)).rejects.toThrow();
  history.events[0].fact.actor_person_id = conditionId(999);
  await expect(h.t.verifySource(p, context)).rejects.toThrow();
});

/** These preparation tests exercise the real adapter/parser, with synthetic HTTP data. */
function preparationHistory(kind: keyof typeof conditionPermissions, person: string) {
  const history = conditionFixture();
  history.events[0].fact.actor_person_id = ['supplement', 'withdraw', 'execute', 'release'].includes(kind) ? person : conditionId(700);
  history.events[1].fact.actor_person_id = conditionId(701); history.events[2].fact.actor_person_id = conditionId(702);
  if (kind === 'submit') { history.events = []; history.cases = []; history.held_quantity = '0.000'; history.unclaimed_quantity = '0.375'; }
  else {
    if (['verify_region', 'return_evidence', 'reject_region', 'withdraw', 'supplement'].includes(kind)) history.events = history.events.slice(0, 1);
    else if (['return_region', 'reject_hq', 'approve_hq'].includes(kind)) history.events = history.events.slice(0, 2);
    if (kind === 'supplement' || kind === 'release') {
      const previous = history.events.at(-1)!;
      history.events.push({ ...previous, sequence: history.events.length + 1, previous_event_id: previous.fact.event_id,
        fact: { ...previous.fact, event_id: conditionId(14), actor_person_id: conditionId(702), action: kind === 'supplement' ? 'return_evidence' : 'cancel_approved',
          status: kind === 'supplement' ? 'needs_evidence' : 'cancelled_pending_release', stock_effect: 'none', posting_transaction_id: null, posting_movement_id: null } });
    }
    const end = history.events.at(-1)!.fact;
    Object.assign(history.cases[0], { status: end.status, latest_event_id: end.event_id, latest_event_hash: end.request_hash });
  }
  return history;
}
function sourceFor(history: ReturnType<typeof conditionFixture>, person: string) {
  return { stage: 'source_evidence_only', inbound_line_id: history.inbound_line_id, inbound_id: conditionId(80),
    root_disposition_id: history.root_disposition_id, source_account_id: history.source_account_id, material_id: history.material_id,
    lot_id: null, location_id: conditionId(84), custodian_person_id: person, recorded_condition: history.recorded_condition,
    required_condition: 'damaged', source_status: 'recorded_stock_retained', historical_damaged_quantity: history.historical_damaged_quantity,
    account_balance_quantity: history.historical_damaged_quantity, claimable_quantity: history.unclaimed_quantity, tracking_mode: 'none', serials: [],
    observed_ledger_cursor: 20, expected_source_hash: 'd'.repeat(64), checked_at: '2026-10-06T01:00:00Z', physical_verification_required: true, posting_allowed: false };
}
function reads(h: Awaited<ReturnType<typeof harness>>, history: ReturnType<typeof conditionFixture>, source = sourceFor(history, h.p.person_id)) {
  h.request.mockImplementation(async (path, init) => {
    expect(init?.method).not.toBe('POST');
    if (path === '/auth/me') return structuredClone(h.me);
    if (path === '/access/context') return structuredClone(h.access);
    if (path.endsWith('/history/' + history.inbound_line_id)) return structuredClone(history);
    if (path.endsWith('/sources/' + history.inbound_line_id)) return structuredClone(source);
    throw new Error('Unexpected read path');
  });
}
it.each(Object.keys(conditionPermissions) as (keyof typeof conditionPermissions)[])('%s prepares from the exact fresh case/source without a write', async kind => {
  const h = await harness(), history = preparationHistory(kind, h.p.person_id); reads(h, history);
  const result = await h.t.prepare(history.inbound_line_id, kind, kind === 'submit' ? null : history.cases[0].case_id,
    { reason: '实际核验意见', evidence_file_ids: [conditionId(40)], quantity: '0.125', scans: [] });
  expect(result.pending.inbound_line_id).toBe(history.inbound_line_id);
  expect(action(result.pending.original)).toBe(kind);
  expect(result.pending.original.reason).toBe('实际核验意见');
  if (result.pending.original.action === 'submit_return_condition') expect(result.pending.original.expected_source_hash).toBe('d'.repeat(64));
  else expect(result.pending.original.expected_event_id).toBe(history.cases[0].latest_event_id);
  expect(h.request.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
it('requires all physical serials for regional verification and rechecks their current identifiers before send', async () => {
  const h = await harness(), history = preparationHistory('verify_region', h.p.person_id);
  history.serials = [1, 2].map(n => ({ serial_id: conditionId(800 + n), serial_no: `SN-${n}`, qr_code: `QR-${n}` }));
  history.historical_damaged_quantity = history.held_quantity = history.cases[0].quantity = '2.000'; history.unclaimed_quantity = '0.000';
  history.cases[0].serial_ids = history.serials.map(s => s.serial_id); history.events[0].fact.quantity = '2.000';
  reads(h, history);
  const intent = { reason: '逐件实物复核', evidence_file_ids: [conditionId(40)], quantity: '', scans: history.serials.map(s => ({ ...s, sku_code: history.sku_code })) };
  await expect(h.t.prepare(history.inbound_line_id, 'verify_region', history.cases[0].case_id, { ...intent, scans: intent.scans.slice(0, 1) })).rejects.toThrow();
  const result = await h.t.prepare(history.inbound_line_id, 'verify_region', history.cases[0].case_id, intent);
  const current = await h.t.context('verify_region');
  await h.t.verifySource(result.pending, current);
  await expect(createConditionAdapter(h.p.person_id, h.request).verifySource(result.pending, current)).rejects.toThrow('请重新逐件核验');
  history.serials[0].qr_code = 'CHANGED';
  await expect(h.t.verifySource(result.pending, current)).rejects.toThrow();
});
it('rejects self approval, a stale predecessor, and a source from another material', async () => {
  const h = await harness(), history = preparationHistory('approve_hq', h.p.person_id); reads(h, history);
  history.events[0].fact.actor_person_id = h.p.person_id;
  await expect(h.t.prepare(history.inbound_line_id, 'approve_hq', history.cases[0].case_id,
    { reason: '禁止自批', evidence_file_ids: [], quantity: '', scans: [] })).rejects.toThrow();
  history.events[0].fact.actor_person_id = conditionId(700);
  const p = await h.t.prepare(history.inbound_line_id, 'approve_hq', history.cases[0].case_id,
    { reason: '独立复核', evidence_file_ids: [], quantity: '', scans: [] });
  history.events.at(-1)!.fact.request_hash = history.cases[0].latest_event_hash = 'f'.repeat(64);
  await expect(h.t.verifySource(p.pending, await h.t.context('approve_hq'))).rejects.toThrow();
  const empty = preparationHistory('submit', h.p.person_id), source = sourceFor(empty, h.p.person_id); source.material_id = conditionId(999); reads(h, empty, source);
  await expect(h.t.prepare(empty.inbound_line_id, 'submit', null,
    { reason: '来源不可混用', evidence_file_ids: [conditionId(40)], quantity: '0.125', scans: [] })).rejects.toThrow();
});
it('discovers receipt-bound histories and rejects mixed receipts, roots, identity versions and duplicates', async () => {
  const h = await harness(), history = conditionFixture(), receipt = conditionId(501), shipment = conditionId(502);
  const response = { schema_version: 'condition_receipt_history/1', receipt_id: receipt, shipment_id: shipment,
    root_disposition_id: history.root_disposition_id, operator_person_id: h.p.person_id, authorization_version: 1,
    status: 'posted', inbound_id: conditionId(503), items: [history], current_stock_verified: false, posting_allowed: false };
  let data: unknown = response;
  h.request.mockImplementation(async path => {
    if (path === '/auth/me') return structuredClone(h.me);
    if (path === '/access/context') return structuredClone(h.access);
    expect(path).toBe('/v1/stock-operations/loss-reports/return-condition-corrections/receipts/' + receipt); return data;
  });
  expect(await h.t.receipt(receipt, shipment, history.root_disposition_id)).toEqual([history]);
  for (const wrong of [{ receipt_id: conditionId(999) }, { shipment_id: conditionId(999) }, { root_disposition_id: conditionId(999) },
    { operator_person_id: conditionId(999) }, { authorization_version: 2 }, { items: [history, history] },
    { status: 'not_posted', inbound_id: null }, { posting_allowed: true }]) {
    data = { ...response, ...wrong }; await expect(h.t.receipt(receipt, shipment, history.root_disposition_id)).rejects.toThrow();
  }
  data = { ...response, items: [] };
  expect(await h.t.receipt(receipt, shipment, history.root_disposition_id)).toEqual([]);
});

async function downloadHarness() {
  const h = await harness(), history = conditionFixture();
  h.access.permissions = h.access.permissions.filter(p => p.action === 'read');
  const file = history.events[0].evidence_file_ids[0], event = history.events[0].fact.event_id;
  const response = { schema_version: '1.0', file_id: file, purpose: 'return_condition_evidence', status: 'available',
    download: { method: 'GET', url: 'https://synthetic.invalid/private?signature=test', expires_at: new Date(Date.now() + 60000).toISOString() } };
  const initial = h.request.getMockImplementation()!;
  h.request.mockImplementation(async (path, init) => {
    if (path.includes('/history/')) return structuredClone(history);
    if (path.includes('/download-intent')) return structuredClone(response);
    return initial(path, init);
  });
  return { ...h, history, file, event, response };
}
it('downloads only original event evidence with read permission and the full public file contract', async () => {
  const h = await downloadHarness();
  expect(await h.t.download(h.history.inbound_line_id, h.event, h.file)).toEqual({ url: h.response.download.url, expires_at: h.response.download.expires_at });
  const calls = h.request.mock.calls.filter(([path]) => path.includes('/download-intent'));
  expect(calls).toHaveLength(1); expect(calls[0][1]?.cache).toBe('no-store');
  expect(new Headers(calls[0][1]?.headers).get('X-Request-ID')).toBeTruthy();
  expect(h.request.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
it.each(['file', 'event'])('rejects a mismatched %s before requesting any signed URL', async field => {
  const h = await downloadHarness();
  await expect(h.t.download(h.history.inbound_line_id, field === 'event' ? conditionId(999) : h.event,
    field === 'file' ? conditionId(999) : h.file)).rejects.toThrow();
  expect(h.request.mock.calls.some(([path]) => path.includes('/download-intent'))).toBe(false);
});
it.each(['purpose', 'file', 'expired', 'too_long', 'http', 'credentials', 'fragment', 'method', 'identity'])('discards invalid download response: %s', async change => {
  const h = await downloadHarness();
  if (change === 'purpose') h.response.purpose = 'stock_loss_evidence';
  if (change === 'file') h.response.file_id = conditionId(999);
  if (change === 'expired') h.response.download.expires_at = new Date(Date.now() - 1).toISOString();
  if (change === 'too_long') h.response.download.expires_at = new Date(Date.now() + 700000).toISOString();
  if (change === 'http') h.response.download.url = 'http://synthetic.invalid/file';
  if (change === 'credentials') h.response.download.url = 'https://user:password@synthetic.invalid/file';
  if (change === 'fragment') h.response.download.url += '#private';
  if (change === 'method') h.response.download.method = 'PUT';
  if (change === 'identity') { const initial = h.request.getMockImplementation()!;
    h.request.mockImplementation(async (path, init) => { const value = await initial(path, init);
      if (path.includes('/download-intent')) { h.me.authorization_version++; h.access.authorization_version++; }
      return value;
    });
  }
  await expect(h.t.download(h.history.inbound_line_id, h.event, h.file)).rejects.toThrow();
});
it('binds inbox pages to current identity, exact histories and a validated cursor', async () => {
  const h = await harness(), history = conditionFixture();
  const response = { schema_version: 'condition_inbox/1', person_id: h.p.person_id, authorization_version: 1,
    view: 'pending', items: [history], next_after_id: history.inbound_line_id, current_stock_verified: false, posting_allowed: false };
  const original = h.request.getMockImplementation()!;
  h.request.mockImplementation(async (path, init) => path.includes('/inbox?') ? structuredClone(response) : original(path, init));
  expect(await h.t.inbox('pending')).toMatchObject({ items: [history], next_after_id: history.inbound_line_id });
  await expect(h.t.inbox('pending', history.inbound_line_id)).rejects.toThrow();
  for (const change of [{ person_id: conditionId(999) }, { authorization_version: 2 }, { next_after_id: conditionId(999) },
    { view: 'all' }, { items: [history, history] }, { posting_allowed: true }]) {
    h.request.mockImplementation(async (path, init) => path.includes('/inbox?') ? structuredClone({ ...response, ...change }) : original(path, init));
    await expect(h.t.inbox('pending')).rejects.toThrow();
  }
  expect(h.request.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
