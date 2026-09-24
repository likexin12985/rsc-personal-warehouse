// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { apiNoReplay } from './api';
import { downloadDailyEvidence } from './dailyReviewProtocol';
import { dailyId, otherDailyId } from './__fixtures__/dailyReconciliation';
vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), apiNoReplay: vi.fn() }));
afterEach(() => vi.restoreAllMocks());
function intent() { return { schema_version: '1.0', file_id: dailyId, purpose: 'daily_reconciliation_evidence', status: 'available',
  download: { method: 'GET', url: 'https://private-objects.example.invalid/evidence?signature=synthetic', expires_at: new Date(Date.now() + 60000).toISOString() } }; }
it('opens an ephemeral private link without passing application headers or referrer to the object host', async () => {
  vi.mocked(apiNoReplay).mockResolvedValue(intent());
  let link: HTMLAnchorElement | undefined;
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) { link = this; });
  await downloadDailyEvidence(dailyId, () => true);
  expect(link!.rel).toBe('noopener noreferrer'); expect(link!.referrerPolicy).toBe('no-referrer'); expect(link!.target).toBe('_blank');
  expect(apiNoReplay).toHaveBeenLastCalledWith(`/v1/files/${dailyId}/download-intent`, { headers: { 'X-Request-ID': expect.any(String) } });
});
it.each(['wrong_file', 'wrong_purpose', 'expired', 'javascript', 'credentials', 'identity_changed'])('refuses unsafe download %s', async fault => {
  const value = intent();
  if (fault === 'wrong_file') value.file_id = otherDailyId;
  if (fault === 'wrong_purpose') value.purpose = 'request_attachment';
  if (fault === 'expired') value.download.expires_at = '2000-01-01T00:00:00Z';
  if (fault === 'javascript') value.download.url = 'javascript:alert(1)';
  if (fault === 'credentials') value.download.url = 'https://user:pass@example.invalid/x';
  vi.mocked(apiNoReplay).mockResolvedValue(value); const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  await expect(downloadDailyEvidence(dailyId, () => fault !== 'identity_changed')).rejects.toThrow(); expect(click).not.toHaveBeenCalled();
});
