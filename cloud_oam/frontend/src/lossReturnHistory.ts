import { canonical, id, object } from './formalLossReview';

export const stages = ['not_outbound', 'outbound_not_shipped', 'shipped_unconfirmed', 'accepted_not_inbound', 'rejected', 'posted_inbound'] as const;
export type Share = { stage: typeof stages[number]; quantity: string; serial_ids: string[] };
export type HistoryLine = { operation_line_id: string; original_quantity: string; shares: Share[];
  damaged_accepted_quantity: string; shortage_observations: { receipt_line_id: string; quantity: string }[] };
export type Classification = { inbound_id: string; receipt_line_id: string; inbound_line_id: string;
  original_target_account_id: string; recorded_condition: 'new' | 'used'; required_condition: 'damaged';
  affected_quantity: string; affected_serial_ids: string[]; code: 'legacy_damaged_acceptance_classification';
  current_stock_verified: false; correction_authorized: false };
export type ReturnHistory = { schema_version: '1.0'; result_scope: 'verified_loss_return_history'; stock_effect: 'none';
  current_stock_verified: false; write_authorization_provided: false; root_disposition_id: string; operation_id: string;
  observed_ledger_cursor: number; evidence_fingerprint: string;
  coordinates: Record<'outbounds' | 'shipments' | 'receipts' | 'inbounds', string[]>;
  lines: HistoryLine[]; classification_exceptions: Classification[] };

function fail(): never { throw new Error('退回历史无法核验，请刷新后重试'); }
function list<T>(raw: unknown, parse: (value: unknown) => T): T[] {
  if (!Array.isArray(raw) || raw.length > 20000) fail();
  return raw.map(parse);
}
function unique(ids: string[]) { if (new Set(ids).size !== ids.length) fail(); }
function ids(raw: unknown): string[] { const values = list(raw, id); unique(values); return values; }
function quantity(raw: unknown, positive = false): string {
  if (typeof raw !== 'string' || !/^(0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(raw)) fail();
  const [whole, fraction = ''] = raw.split('.');
  const result = `${whole}.${fraction.padEnd(3, '0')}`;
  if (positive && amount(result) === 0n) fail();
  return result;
}
function amount(q: string): bigint { return BigInt(q.replace('.', '')); }
const sum = (values: string[]) => values.reduce((total, q) => total + amount(q), 0n);

export function returnHistory(raw: unknown, expected: { root: string; quantity: string; serials: string[] }): ReturnHistory {
  const r = object(raw, ['schema_version', 'result_scope', 'stock_effect', 'current_stock_verified', 'write_authorization_provided',
    'root_disposition_id', 'operation_id', 'observed_ledger_cursor', 'evidence_fingerprint', 'coordinates', 'lines', 'classification_exceptions']);
  if (r.schema_version !== '1.0' || r.result_scope !== 'verified_loss_return_history' || r.stock_effect !== 'none'
      || r.current_stock_verified !== false || r.write_authorization_provided !== false || r.root_disposition_id !== id(expected.root)
      || !Number.isSafeInteger(r.observed_ledger_cursor) || Number(r.observed_ledger_cursor) < 1
      || typeof r.evidence_fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(r.evidence_fingerprint)) fail();
  id(r.operation_id);
  const c = object(r.coordinates, ['outbounds', 'shipments', 'receipts', 'inbounds']);
  const coordinates = { outbounds: ids(c.outbounds), shipments: ids(c.shipments), receipts: ids(c.receipts), inbounds: ids(c.inbounds) };
  const lines = list(r.lines, rawLine => {
    const line = object(rawLine, ['operation_line_id', 'original_quantity', 'shares', 'damaged_accepted_quantity', 'shortage_observations']);
    const shares = list(line.shares, (rawShare): Share => {
      const share = object(rawShare, ['stage', 'quantity', 'serial_ids']);
      if (!stages.includes(share.stage as Share['stage'])) fail();
      return { stage: share.stage as Share['stage'], quantity: quantity(share.quantity), serial_ids: ids(share.serial_ids) };
    });
    if (canonical(shares.map(s => s.stage)) !== canonical(stages)) fail();
    const original = quantity(line.original_quantity, true), damaged = quantity(line.damaged_accepted_quantity);
    if (sum(shares.map(s => s.quantity)) !== amount(original)
        || amount(damaged) > sum(shares.filter(s => ['accepted_not_inbound', 'posted_inbound'].includes(s.stage)).map(s => s.quantity))) fail();
    const serials = shares.flatMap(s => s.serial_ids); unique(serials);
    if (serials.length && shares.some(s => amount(s.quantity) !== BigInt(s.serial_ids.length) * 1000n)) fail();
    const shortages = list(line.shortage_observations, x => {
      const v = object(x, ['receipt_line_id', 'quantity']);
      return { receipt_line_id: id(v.receipt_line_id), quantity: quantity(v.quantity, true) };
    });
    unique(shortages.map(x => x.receipt_line_id));
    return { operation_line_id: id(line.operation_line_id), original_quantity: original, shares,
      damaged_accepted_quantity: damaged, shortage_observations: shortages };
  });
  unique(lines.map(l => l.operation_line_id));
  const serials = lines.flatMap(l => l.shares.flatMap(s => s.serial_ids)); unique(serials);
  if (!lines.length || sum(lines.map(l => l.original_quantity)) !== amount(quantity(expected.quantity, true))
      || canonical([...serials].sort()) !== canonical([...ids(expected.serials)].sort())) fail();
  const exceptions = list(r.classification_exceptions, (rawIssue): Classification => {
    const issue = object(rawIssue, ['inbound_id', 'receipt_line_id', 'inbound_line_id', 'original_target_account_id',
      'recorded_condition', 'required_condition', 'affected_quantity', 'affected_serial_ids', 'code', 'current_stock_verified', 'correction_authorized']);
    for (const key of ['inbound_id', 'receipt_line_id', 'inbound_line_id', 'original_target_account_id']) id(issue[key]);
    if (!coordinates.inbounds.includes(issue.inbound_id as string) || !['new', 'used'].includes(String(issue.recorded_condition))
        || issue.required_condition !== 'damaged' || issue.code !== 'legacy_damaged_acceptance_classification'
        || issue.current_stock_verified !== false || issue.correction_authorized !== false) fail();
    const affected = quantity(issue.affected_quantity, true), sn = ids(issue.affected_serial_ids);
    const posted = lines.flatMap(l => l.shares.filter(s => s.stage === 'posted_inbound').flatMap(s => s.serial_ids));
    if (sn.some(v => !posted.includes(v)) || (serials.length && amount(affected) !== BigInt(sn.length) * 1000n) || (!serials.length && sn.length)) fail();
    return { ...issue, affected_quantity: affected, affected_serial_ids: sn } as Classification;
  });
  unique(exceptions.map(e => e.inbound_line_id)); unique(exceptions.flatMap(e => e.affected_serial_ids));
  if (sum(exceptions.map(e => e.affected_quantity)) > sum(lines.map(l => l.damaged_accepted_quantity))
      || sum(exceptions.map(e => e.affected_quantity)) > sum(lines.flatMap(l => l.shares.filter(s => s.stage === 'posted_inbound').map(s => s.quantity)))) fail();
  return { ...r, coordinates, lines, classification_exceptions: exceptions } as ReturnHistory;
}
