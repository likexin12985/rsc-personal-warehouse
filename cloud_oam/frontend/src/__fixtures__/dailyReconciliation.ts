export const dailyId = '10000000-0000-4000-8000-000000000001';
export const otherDailyId = '10000000-0000-4000-8000-000000000002';
export function dailyDetail() {
  return { cutoff_id: dailyId, business_date: '2026-09-21', source_system_id: dailyId, region_org_id: dailyId,
    source_publication_id: dailyId, mapping_decision_id: dailyId, local_ledger_cursor: 5,
    source_captured_at: '2026-09-21T01:00:00Z', local_captured_at: '2026-09-21T01:05:00Z', created_at: '2026-09-21T01:06:00Z',
    cutoff_sha256: 'a'.repeat(64), comparison_sha256: 'b'.repeat(64), comparison_status: 'differences' as const,
    item_count: 1, excluded_quantity_count: 0, review_version: 1, review_status: 'pending_review' as const,
    review_updated_at: '2026-09-21T02:00:00Z', covered_warehouses: ['合成省仓'], included_buckets: ['available'],
    approval_comment: '', approved_by_person_id: null as string | null,
    action_context: { person_id: otherDailyId, authorization_version: 1 },
    allowed_actions: ['explain', 'approve', 'request_changes'] as ('open' | 'explain' | 'approve' | 'request_changes')[] };
}
export function dailyItem() {
  return { ordinal: 1, warehouse_code: '合成省仓', material_id: dailyId, condition: 'new', external_qty: '999999999999999.999',
    local_qty: '999999999999999.998', difference: '0.001', status: 'difference',
    review: { ordinal: 1, version: 1, explanation: '合成数量差异说明', evidence: { file_id: dailyId, sha256: 'c'.repeat(64),
      size_bytes: 10, mime_type: 'image/png', status: 'available', accessible: true },
    explained_by_person_id: dailyId, revision_requested: false, review_comment: '' } };
}
export function dailyItems() { return { cutoff_id: dailyId, review_version: 1, comparison_sha256: 'b'.repeat(64), items: [dailyItem()], next_after_ordinal: null }; }
export function dailyExcluded() { return { cutoff_id: dailyId, comparison_sha256: 'b'.repeat(64), items: [], next_after_ordinal: null }; }
export function dailyHistory() { return { cutoff_id: dailyId, review_version: 1, items: [{ version: 1, event_id: dailyId,
  event_sha256: 'd'.repeat(64), actor_person_id: dailyId, occurred_at: '2026-09-21T02:00:00Z', operation: 'open',
  explanations: [], comment: '', returned_ordinals: [] }], next_after_version: null }; }
