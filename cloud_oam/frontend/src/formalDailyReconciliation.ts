import { api, ApiError } from './api';

const PATH = '/v1/reconciliations/daily';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA = /^[0-9a-f]{64}$/;
export const reviewLabels = {
  not_recorded: '尚未开启审核', awaiting_explanations: '待解释差异',
  pending_review: '待总部复核', changes_requested: '待补充证据', approved: '总部已审核',
} as const;
export const operationLabels = { open: '开启审核', explain: '解释差异', approve: '总部审核', request_changes: '退回补证' } as const;
type ObjectValue = Record<string, unknown>;
function requireValue(ok: unknown): asserts ok {
  if (!ok) throw new Error('日终对账数据不完整或版本已变化，请刷新核对');
}
function object(value: unknown): ObjectValue {
  requireValue(value && typeof value === 'object' && !Array.isArray(value));
  return value as ObjectValue;
}
function string(value: unknown, maximum = 2000): string {
  requireValue(typeof value === 'string' && value.length <= maximum);
  return value;
}
function pattern(value: unknown, regex: RegExp): string {
  const v = string(value); requireValue(regex.test(v)); return v;
}
function uuid(value: unknown): string {
  const v = pattern(value, UUID); requireValue(v !== '00000000-0000-0000-0000-000000000000'); return v;
}
function integer(value: unknown, minimum = 0): number {
  requireValue(typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum); return value;
}
function choice<T extends string>(value: unknown, choices: readonly T[]): T {
  requireValue(typeof value === 'string' && choices.includes(value as T)); return value as T;
}
function timestamp(value: unknown): string {
  const v = pattern(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);
  requireValue(Number.isFinite(Date.parse(v))); return v;
}
function day(value: unknown): string {
  const v = pattern(value, /^\d{4}-\d{2}-\d{2}$/);
  requireValue(Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v); return v;
}
function list<T>(value: unknown, parse: (v: unknown) => T, maximum: number): T[] {
  requireValue(Array.isArray(value) && value.length <= maximum); return value.map(parse);
}
function nullable<T>(value: unknown, parse: (v: unknown) => T): T | null { return value === null ? null : parse(value); }
function strings(value: unknown): string[] { return list(value, v => string(v), 100000); }
function quantity(value: unknown, signed = false): string {
  return pattern(value, signed ? /^-?(?:0|[1-9]\d{0,14})\.\d{3}$/ : /^(?:0|[1-9]\d{0,14})\.\d{3}$/);
}
function units(value: string): bigint { return BigInt(value.replace('.', '')); }

export function parseDailySummary(value: unknown) {
  const v = object(value);
  const result = {
    cutoff_id: uuid(v.cutoff_id), business_date: day(v.business_date), source_system_id: uuid(v.source_system_id),
    region_org_id: uuid(v.region_org_id), source_publication_id: uuid(v.source_publication_id), mapping_decision_id: uuid(v.mapping_decision_id),
    local_ledger_cursor: integer(v.local_ledger_cursor), source_captured_at: timestamp(v.source_captured_at),
    local_captured_at: timestamp(v.local_captured_at), created_at: timestamp(v.created_at),
    cutoff_sha256: pattern(v.cutoff_sha256, SHA), comparison_sha256: pattern(v.comparison_sha256, SHA),
    comparison_status: choice(v.comparison_status, ['matched', 'differences']), item_count: integer(v.item_count),
    excluded_quantity_count: integer(v.excluded_quantity_count), review_version: integer(v.review_version),
    review_status: choice(v.review_status, Object.keys(reviewLabels) as (keyof typeof reviewLabels)[]),
    review_updated_at: nullable(v.review_updated_at, timestamp),
  };
  requireValue((result.review_version === 0) === (result.review_status === 'not_recorded'));
  requireValue((result.review_version === 0) === (result.review_updated_at === null));
  return result;
}
export type DailySummary = ReturnType<typeof parseDailySummary>;
export function parseDailyDetail(value: unknown) {
  const v = object(value);
  const context = object(v.action_context);
  const result = { ...parseDailySummary(value), covered_warehouses: strings(v.covered_warehouses),
    included_buckets: strings(v.included_buckets), approval_comment: string(v.approval_comment),
    approved_by_person_id: nullable(v.approved_by_person_id, uuid),
    action_context: { person_id: uuid(context.person_id), authorization_version: integer(context.authorization_version, 1) },
    allowed_actions: list(v.allowed_actions, action => choice(action, Object.keys(operationLabels) as (keyof typeof operationLabels)[]), 4) };
  requireValue((result.review_status === 'approved') === (result.approved_by_person_id !== null));
  requireValue(new Set(result.allowed_actions).size === result.allowed_actions.length);
  requireValue(result.allowed_actions.every(action => {
    if (result.review_status === 'approved') return false;
    if (action === 'open') return result.review_version === 0;
    if (result.review_version === 0) return false;
    if (action === 'approve') return result.review_status === 'pending_review';
    return result.comparison_status === 'differences';
  }));
  return result;
}
export type DailyDetail = ReturnType<typeof parseDailyDetail>;
function evidence(value: unknown) {
  const v = object(value); requireValue(v.status === 'available' && v.accessible === true);
  return { file_id: uuid(v.file_id), sha256: pattern(v.sha256, SHA), size_bytes: integer(v.size_bytes, 1), mime_type: string(v.mime_type, 160) };
}
function comparisonItem(value: unknown) {
  const v = object(value), r = object(v.review);
  const result = { ordinal: integer(v.ordinal, 1), warehouse_code: string(v.warehouse_code), material_id: uuid(v.material_id),
    condition: string(v.condition), external_qty: quantity(v.external_qty), local_qty: quantity(v.local_qty),
    difference: quantity(v.difference, true), status: choice(v.status, ['matched', 'difference']),
    review: { ordinal: integer(r.ordinal, 1), version: integer(r.version), explanation: string(r.explanation),
      evidence: nullable(r.evidence, evidence), explained_by_person_id: nullable(r.explained_by_person_id, uuid),
      revision_requested: r.revision_requested, review_comment: string(r.review_comment) } };
  requireValue(typeof r.revision_requested === 'boolean' && result.ordinal === result.review.ordinal);
  requireValue(units(result.external_qty) - units(result.local_qty) === units(result.difference));
  requireValue((result.status === 'matched') === (units(result.difference) === 0n));
  requireValue((result.review.evidence === null) === (result.review.explained_by_person_id === null));
  return result;
}
function explanation(value: unknown) {
  const v = object(value);
  return { ordinal: integer(v.ordinal, 1), expected_item_version: integer(v.expected_item_version), explanation: string(v.explanation),
    evidence_file_id: uuid(v.evidence_file_id), evidence_sha256: pattern(v.evidence_sha256, SHA) };
}
function historyItem(value: unknown) {
  const v = object(value);
  return { version: integer(v.version, 1), event_id: uuid(v.event_id), event_sha256: pattern(v.event_sha256, SHA),
    actor_person_id: uuid(v.actor_person_id), occurred_at: timestamp(v.occurred_at),
    operation: choice(v.operation, Object.keys(operationLabels) as (keyof typeof operationLabels)[]),
    explanations: list(v.explanations, explanation, 100), comment: string(v.comment), returned_ordinals: list(v.returned_ordinals, v => integer(v, 1), 100) };
}
function pageBinding(value: ObjectValue, detail: DailyDetail, review: boolean) {
  requireValue(uuid(value.cutoff_id) === detail.cutoff_id);
  if (review) requireValue(integer(value.review_version) === detail.review_version);
  if ('comparison_sha256' in value) requireValue(pattern(value.comparison_sha256, SHA) === detail.comparison_sha256);
}
function sequential<T>(items: T[], after: number, total: number, next: number | null, key: (v: T) => number) {
  requireValue(items.every((v, index) => key(v) === after + index + 1 && key(v) <= total));
  const end = after + items.length;
  requireValue(end <= total && (next === null ? end === total : items.length > 0 && next === end && end < total));
}
export async function loadDailyList(filters: { businessDate?: string; regionId?: string; afterId?: string } = {}) {
  const query = new URLSearchParams({ limit: '20' });
  if (filters.businessDate) query.set('business_date', day(filters.businessDate));
  if (filters.regionId) query.set('region_org_id', uuid(filters.regionId.trim()));
  if (filters.afterId) query.set('after_id', uuid(filters.afterId));
  const v = object(await api<unknown>(`${PATH}?${query}`));
  const items = list(v.items, parseDailySummary, 20), next = nullable(v.next_after_id, uuid);
  requireValue(new Set(items.map(v => v.cutoff_id)).size === items.length);
  requireValue(items.every((v, index) => index === 0 || v.cutoff_id > items[index - 1].cutoff_id));
  requireValue(items.every(v => (!filters.businessDate || v.business_date === filters.businessDate)
    && (!filters.regionId || v.region_org_id === filters.regionId.trim()) && (!filters.afterId || v.cutoff_id > filters.afterId)));
  requireValue(next === null || (items.length > 0 && next === items[items.length - 1].cutoff_id));
  return { items, next_after_id: next };
}
export async function loadDailyDetail(id: string): Promise<DailyDetail> {
  const value = parseDailyDetail(await api<unknown>(`${PATH}/${uuid(id)}`)); requireValue(value.cutoff_id === id); return value;
}
export async function loadDailyItems(detail: DailyDetail, after = 0) {
  const v = object(await api<unknown>(`${PATH}/${uuid(detail.cutoff_id)}/items?limit=100&after_ordinal=${integer(after)}&expected_review_version=${integer(detail.review_version)}`));
  pageBinding(v, detail, true); requireValue('comparison_sha256' in v);
  const items = list(v.items, comparisonItem, 100), next = nullable(v.next_after_ordinal, v => integer(v, 1));
  requireValue(items.every(v => v.review.version <= detail.review_version));
  requireValue(detail.comparison_status !== 'matched' || items.every(v => v.status === 'matched'));
  sequential(items, after, detail.item_count, next, v => v.ordinal);
  return { items, next_after_ordinal: next };
}
export async function loadDailyExcluded(detail: DailyDetail, after = 0) {
  const v = object(await api<unknown>(`${PATH}/${uuid(detail.cutoff_id)}/excluded-quantities?limit=100&after_ordinal=${integer(after)}`));
  pageBinding(v, detail, false); requireValue('comparison_sha256' in v);
  const items = list(v.items, value => {
    const r = object(value); return { ordinal: integer(r.ordinal, 1), warehouse_code: string(r.warehouse_code),
      material_id: uuid(r.material_id), condition: string(r.condition), bucket: string(r.bucket), quantity: quantity(r.quantity) };
  }, 100), next = nullable(v.next_after_ordinal, v => integer(v, 1));
  sequential(items, after, detail.excluded_quantity_count, next, v => v.ordinal);
  return { items, next_after_ordinal: next };
}
export async function loadDailyHistory(detail: DailyDetail, after = 0) {
  const v = object(await api<unknown>(`${PATH}/${uuid(detail.cutoff_id)}/history?limit=50&after_version=${integer(after)}&expected_review_version=${integer(detail.review_version)}`));
  pageBinding(v, detail, true);
  const items = list(v.items, historyItem, 50), next = nullable(v.next_after_version, v => integer(v, 1));
  sequential(items, after, detail.review_version, next, v => v.version);
  return { items, next_after_version: next };
}
export function dailyReadError(reason: unknown): string {
  if (reason instanceof ApiError) {
    if (reason.status === 409) return '审核已变化，请刷新这份对账后重新查看';
    if ([401, 403, 404].includes(reason.status)) return '当前身份已无法查看这份对账，请重新确认权限';
  }
  return '暂时无法核验日终对账，请刷新重试';
}
