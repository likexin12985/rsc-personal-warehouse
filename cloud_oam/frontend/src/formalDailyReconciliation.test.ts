import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { loadDailyDetail, loadDailyExcluded, loadDailyHistory, loadDailyItems, loadDailyList, parseDailyDetail } from './formalDailyReconciliation';
import { dailyDetail, dailyExcluded, dailyHistory, dailyId, dailyItems, otherDailyId } from './__fixtures__/dailyReconciliation';
vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), api: vi.fn() }));
beforeEach(() => vi.mocked(api).mockReset());
describe('daily query boundaries', () => {
  it('preserves precise quantities and binds every page to the selected snapshot', async () => {
    vi.mocked(api).mockResolvedValue(dailyItems());
    const result = await loadDailyItems(parseDailyDetail(dailyDetail()));
    expect(result.items[0].external_qty).toBe('999999999999999.999');
    expect(result.items[0].difference).toBe('0.001');
    expect(api).toHaveBeenCalledWith(`/v1/reconciliations/daily/${dailyId}/items?limit=100&after_ordinal=0&expected_review_version=1`);
  });
  it('keeps approved review independent from numerical differences', () => {
    const d = parseDailyDetail({ ...dailyDetail(), review_status: 'approved', approved_by_person_id: dailyId, approval_comment: '证据核验通过', allowed_actions: [] });
    expect(d.comparison_status).toBe('differences'); expect(d.review_status).toBe('approved');
  });
  it.each(['local_ledger_cursor', 'review_version', 'item_count', 'excluded_quantity_count'])('refuses unsafe integer %s', field => {
    expect(() => parseDailyDetail({ ...dailyDetail(), [field]: Number.MAX_SAFE_INTEGER + 1 })).toThrow();
  });
  it.each(['boolean', 'string', 'negative', 'fraction', 'impossible_date', 'unknown_status', 'missing_time', 'false_approval'])('refuses malformed summary %s', fault => {
    const d: Record<string, unknown> = dailyDetail();
    if (fault === 'boolean') d.review_version = true;
    if (fault === 'string') d.local_ledger_cursor = '5';
    if (fault === 'negative') d.item_count = -1;
    if (fault === 'fraction') d.item_count = 1.5;
    if (fault === 'impossible_date') d.business_date = '2026-02-30';
    if (fault === 'unknown_status') d.review_status = 'resolved';
    if (fault === 'missing_time') d.review_updated_at = null;
    if (fault === 'false_approval') d.review_status = 'approved';
    expect(() => parseDailyDetail(d)).toThrow();
  });
  it.each(['object', 'hash', 'version', 'ordinal', 'review_ordinal', 'quantity', 'difference', 'status', 'next', 'truncated', 'file'])('refuses inconsistent items %s', async fault => {
    const p = dailyItems();
    if (fault === 'object') p.cutoff_id = otherDailyId;
    if (fault === 'hash') p.comparison_sha256 = 'e'.repeat(64);
    if (fault === 'version') p.review_version = 2;
    if (fault === 'ordinal') p.items[0].ordinal = 2;
    if (fault === 'review_ordinal') p.items[0].review.ordinal = 2;
    if (fault === 'quantity') p.items[0].external_qty = '1e20';
    if (fault === 'difference') p.items[0].difference = '0.002';
    if (fault === 'status') p.items[0].status = 'matched';
    if (fault === 'next') Object.assign(p, { next_after_ordinal: 1 });
    if (fault === 'truncated') p.items = [];
    if (fault === 'file') p.items[0].review.evidence.status = 'pending';
    vi.mocked(api).mockResolvedValue(p);
    await expect(loadDailyItems(parseDailyDetail(dailyDetail()))).rejects.toThrow();
  });
  it('rejects a wrong detail object', async () => {
    vi.mocked(api).mockResolvedValue({ ...dailyDetail(), cutoff_id: otherDailyId });
    await expect(loadDailyDetail(dailyId)).rejects.toThrow();
  });
  it.each(['duplicate', 'order', 'next', 'region', 'date', 'repeat_cursor'])('validates list identity and filter %s', async fault => {
    const p = { items: [dailyDetail()], next_after_id: null as string | null };
    if (fault === 'duplicate') p.items.push(dailyDetail());
    if (fault === 'order') p.items.unshift({ ...dailyDetail(), cutoff_id: otherDailyId });
    if (fault === 'next') p.next_after_id = otherDailyId;
    vi.mocked(api).mockResolvedValue(p);
    await expect(loadDailyList(fault === 'region' ? { regionId: otherDailyId } : fault === 'date'
      ? { businessDate: '2026-09-20' } : fault === 'repeat_cursor' ? { afterId: dailyId } : {})).rejects.toThrow();
  });
  it('validates filters before making a request', async () => {
    await expect(loadDailyList({ regionId: 'guessed-region' })).rejects.toThrow();
    await expect(loadDailyList({ businessDate: '2026-02-30' })).rejects.toThrow();
    expect(api).not.toHaveBeenCalled();
  });
  it('accepts a truly empty query', async () => {
    vi.mocked(api).mockResolvedValue({ items: [], next_after_id: null });
    expect((await loadDailyList()).items).toEqual([]);
  });
  it('validates history order and refuses a changed review version', async () => {
    vi.mocked(api).mockResolvedValue(dailyHistory());
    expect((await loadDailyHistory(parseDailyDetail(dailyDetail()))).items[0].operation).toBe('open');
    vi.mocked(api).mockResolvedValue({ ...dailyHistory(), review_version: 2 });
    await expect(loadDailyHistory(parseDailyDetail(dailyDetail()))).rejects.toThrow();
    vi.mocked(api).mockResolvedValue({ ...dailyHistory(), items: [] });
    await expect(loadDailyHistory(parseDailyDetail(dailyDetail()))).rejects.toThrow();
  });
  it('requires excluded quantities to belong to the same immutable comparison', async () => {
    vi.mocked(api).mockResolvedValue(dailyExcluded());
    expect((await loadDailyExcluded(parseDailyDetail(dailyDetail()))).items).toEqual([]);
    vi.mocked(api).mockResolvedValue({ ...dailyExcluded(), comparison_sha256: 'f'.repeat(64) });
    await expect(loadDailyExcluded(parseDailyDetail(dailyDetail()))).rejects.toThrow();
  });
  it.each(['missing_actions', 'duplicate', 'unknown', 'missing_identity', 'invalid_identity', 'unsafe_version', 'opened', 'unexplained', 'approved', 'matched'])('rejects invalid action context %s', fault => {
    const d: Record<string, unknown> = dailyDetail();
    if (fault === 'missing_actions') delete d.allowed_actions;
    if (fault === 'duplicate') d.allowed_actions = ['approve', 'approve'];
    if (fault === 'unknown') d.allowed_actions = ['force_approve'];
    if (fault === 'missing_identity') delete d.action_context;
    if (fault === 'invalid_identity') d.action_context = { person_id: 'guessed-person', authorization_version: 1 };
    if (fault === 'unsafe_version') d.action_context = { person_id: dailyId, authorization_version: Number.MAX_SAFE_INTEGER + 1 };
    if (fault === 'opened') d.allowed_actions = ['open'];
    if (fault === 'unexplained') d.review_status = 'awaiting_explanations';
    if (fault === 'approved') Object.assign(d, { review_status: 'approved', approved_by_person_id: dailyId });
    if (fault === 'matched') d.comparison_status = 'matched';
    expect(() => parseDailyDetail(d)).toThrow();
  });
  it('keeps a read-only detail usable without granting any operation', () => {
    expect(parseDailyDetail({ ...dailyDetail(), allowed_actions: [] }).allowed_actions).toEqual([]);
  });
});
