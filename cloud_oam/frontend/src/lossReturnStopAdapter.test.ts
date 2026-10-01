import { expect, it, vi } from 'vitest';
import { createStopAdapter } from './lossReturnStopAdapter';
import { verifyStopPending } from './lossReturnStopContracts';
import { fixtures, saved } from './lossReturnStopFixtures';
import type { Context } from './lossCorrectionRecovery';

function harness(f: typeof fixtures[number]) {
  const context: Context = { person_id: f.data.source.person_id, authorization_version: f.data.source.authorization_version,
    authority_hash: 'a'.repeat(64), can_read: true, can_write: { inverses: true, approvals: true, executions: true } };
  const readContext = vi.fn(async () => structuredClone(context));
  const request = vi.fn(async (url: string, init?: RequestInit): Promise<unknown> => {
    expect(init?.cache).toBe('no-store'); expect(url).toContain('/corrections/return-stops');
    if (url.includes('/sources/')) return structuredClone(f.data.source);
    if (url.endsWith('/preview')) return structuredClone(f.data.preview);
    if (url.endsWith('/request-lookup')) return structuredClone(f.data.found);
    if (url.endsWith('/request-seal')) return structuredClone(f.data.sealed);
    if (url.endsWith('/return-stops')) return structuredClone(f.data.found.result);
    throw new Error('Unexpected stop endpoint');
  });
  return { context, readContext, request, adapter: createStopAdapter(context.person_id, request, readContext) };
}
it.each(fixtures)('$name prepares from dedicated references with only source and preview requests', async f => {
  const h = harness(f), p = await h.adapter.prepare(f.data.original.root_disposition_id, f.data.original.reason);
  expect(await verifyStopPending(p)).toEqual(p);
  expect(h.request.mock.calls.map(([url]) => url)).toEqual([
    '/v1/stock-operations/loss-reports/corrections/return-stops/sources/' + p.command.root_disposition_id,
    '/v1/stock-operations/loss-reports/corrections/return-stops/preview',
  ]);
  expect(p.command.request_id).not.toBe(f.data.original.request_id);
});
it.each(fixtures)('$name preserves the exact original command and headers without transport retries', async f => {
  const h = harness(f), p = await saved(f);
  for (const [method, suffix] of [['lookup', '/request-lookup'], ['execute', ''], ['seal', '/request-seal']] as const) {
    await h.adapter[method](p);
    const [url, init] = h.request.mock.lastCall!;
    expect(url).toBe('/v1/stock-operations/loss-reports/corrections/return-stops' + suffix);
    expect(JSON.parse(init!.body as string)).toEqual(p.command);
    const headers = new Headers(init!.headers);
    expect(headers.get('X-Request-ID')).toBe(p.command.request_id);
    expect(headers.get('Idempotency-Key')).toBe(p.command.idempotency_key);
  }
  h.request.mockRejectedValueOnce(new Error('Unknown reply'));
  await expect(h.adapter.execute(p)).rejects.toThrow('Unknown reply');
  expect(h.request).toHaveBeenCalledTimes(4);
});
it.each(fixtures)('$name write revocation permits source reads but denies new previews', async f => {
  const h = harness(f); h.context.can_write.inverses = false;
  await expect(h.adapter.read(f.data.original.root_disposition_id)).resolves.toMatchObject({ state: 'preview_required' });
  await expect(h.adapter.prepare(f.data.original.root_disposition_id, f.data.original.reason)).rejects.toThrow();
  expect(h.request.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
it('discards data if authority changed while reading', async () => {
  const f = fixtures[0], h = harness(f), original = h.request.getMockImplementation()!;
  h.request.mockImplementation(async (url, init) => {
    const answer = await original(url, init); h.context.authority_hash = 'b'.repeat(64); return answer;
  });
  await expect(h.adapter.read(f.data.original.root_disposition_id)).rejects.toThrow('权限变化');
});
