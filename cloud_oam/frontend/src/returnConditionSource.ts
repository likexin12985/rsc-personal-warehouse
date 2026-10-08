import { id, object } from './formalLossReview';
import { digest, fail } from './returnConditionCommands';

export type SourceSerial = { serial_id: string; serial_no: string; qr_code: string; claimable_for_correction: boolean };
export type ConditionSource = { stage: 'source_evidence_only'; inbound_line_id: string; inbound_id: string;
  root_disposition_id: string; source_account_id: string; material_id: string; lot_id: string | null;
  location_id: string; custodian_person_id: string; recorded_condition: 'new' | 'used'; required_condition: 'damaged';
  source_status: 'recorded_stock_retained' | 'verified_condition_history' | 'later_activity_requires_reconciliation';
  historical_damaged_quantity: string; account_balance_quantity: string; claimable_quantity: string | null;
  tracking_mode: 'none' | 'lot' | 'serial' | 'lot_and_serial'; serials: SourceSerial[];
  observed_ledger_cursor: number; expected_source_hash: string; checked_at: string;
  physical_verification_required: true; posting_allowed: false };
export function conditionUnits(v: unknown): bigint {
  if (typeof v !== 'string' || !/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(v)) fail('数量必须为最多三位小数的准确数字');
  const [a, b = ''] = v.split('.'); return BigInt(a) * 1000n + BigInt(b.padEnd(3, '0'));
}
export function conditionSource(value: unknown, inbound: string, person: string): ConditionSource {
  const s = object(value, ['stage', 'inbound_line_id', 'inbound_id', 'root_disposition_id', 'source_account_id', 'material_id', 'lot_id',
    'location_id', 'custodian_person_id', 'recorded_condition', 'required_condition', 'source_status', 'historical_damaged_quantity',
    'account_balance_quantity', 'claimable_quantity', 'tracking_mode', 'serials', 'observed_ledger_cursor', 'expected_source_hash',
    'checked_at', 'physical_verification_required', 'posting_allowed']);
  if (s.stage !== 'source_evidence_only' || id(s.inbound_line_id) !== id(inbound) || id(s.custodian_person_id) !== id(person)
      || s.required_condition !== 'damaged' || !['new', 'used'].includes(String(s.recorded_condition))
      || !['recorded_stock_retained', 'verified_condition_history', 'later_activity_requires_reconciliation'].includes(String(s.source_status))
      || s.physical_verification_required !== true || s.posting_allowed !== false
      || !Number.isSafeInteger(s.observed_ledger_cursor) || Number(s.observed_ledger_cursor) < 1
      || typeof s.checked_at !== 'string' || !Number.isFinite(Date.parse(s.checked_at))) fail();
  for (const k of ['inbound_id', 'root_disposition_id', 'source_account_id', 'material_id', 'location_id']) id(s[k]);
  if (s.lot_id !== null) id(s.lot_id); digest(s.expected_source_hash);
  const damaged = conditionUnits(s.historical_damaged_quantity), balance = conditionUnits(s.account_balance_quantity);
  if (damaged <= 0n || (s.source_status === 'later_activity_requires_reconciliation') !== (s.claimable_quantity === null)) fail();
  const claim = s.claimable_quantity === null ? null : conditionUnits(s.claimable_quantity);
  if (claim !== null && (claim > damaged || claim > balance)) fail();
  if (!['none', 'lot', 'serial', 'lot_and_serial'].includes(String(s.tracking_mode)) || !Array.isArray(s.serials) || s.serials.length > 20000) fail();
  const serials = s.serials.map(v => { const r = object(v, ['serial_id', 'serial_no', 'qr_code', 'claimable_for_correction']);
    if (typeof r.serial_no !== 'string' || !r.serial_no || r.serial_no.length > 200 || typeof r.qr_code !== 'string' || !r.qr_code || r.qr_code.length > 250 || typeof r.claimable_for_correction !== 'boolean') fail();
    return { serial_id: id(r.serial_id), serial_no: r.serial_no, qr_code: r.qr_code, claimable_for_correction: r.claimable_for_correction }; });
  if (new Set(serials.map(s => s.serial_id)).size !== serials.length) fail();
  const tracked = s.tracking_mode === 'serial' || s.tracking_mode === 'lot_and_serial';
  if (tracked ? BigInt(serials.length) * 1000n !== damaged : serials.length !== 0) fail();
  if (tracked && claim !== null && BigInt(serials.filter(s => s.claimable_for_correction).length) * 1000n !== claim) fail();
  return { ...s, serials } as ConditionSource;
}
