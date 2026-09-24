import { beforeEach, expect, it, vi } from 'vitest';
import { api, apiNoReplay, mutationHeaders } from './api';
import { createOpeningStartPorts, OPENING_START_ENABLED } from './openingStartClient';
import type { StartInput, StartMarker } from './openingStartCore';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), api: vi.fn(), apiNoReplay: vi.fn(),
  mutationHeaders: vi.fn(() => ({ headers: { 'X-Request-ID': 'request-00000001', 'Idempotency-Key': 'idempotency-00000001' } })) }));
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const actor = { person_id: id(1), authorization_version: 7 };
const input: StartInput = { region_org_id: id(2), publication_id: id(3), task_no: 'OPEN-WIRE', blind_count: true, deadline: null, note: '',
  scopes: [{ owner_org_id: id(2), location_id: id(4), assignee_user_id: 'counter-001', freeze_mode: 'hard' }] };
const marker: StartMarker = { v: 1, kind: 'opening_start', region_org_id: id(2), publication_id: id(3), actor_person_id: id(1), actor_authorization_version: 7, trace_request_id: 'request-00000001' };
const user = { ...actor, name: '测试管理员', employee_no: 'HQ001', organization_code: 'HQ', organization_name: '总部', account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin'] };
function access() { return { ...actor, account_status: 'active', employment_status: 'active', access_mode: 'active', role_codes: ['admin'],
  assignments: [{ assignment_id: id(9), role_code: 'admin', scope_type: 'national', scope_id: '*', valid_from: '2026-01-01T00:00:00Z', valid_to: null }],
  permissions: ['read','manage'].map((action) => ({ resource: 'stocktake', action, field_code: '' })) }; }
function wire(path: string): unknown {
  if (path === '/auth/me') return user;
  if (path === '/access/context') return access();
  const url = new URL(path, 'https://example.invalid'), stage = url.pathname.split('/').pop();
  const anchors = { actor_person_id: actor.person_id, authorization_version: 7 };
  if (stage === 'control-batches') return { ...anchors, schema_version: 'rsc.opening_control_batches.v1', region_org_id: id(2),
    start_ready: false, admission_status: 'not_evaluated', next_after_id: null, items: [{ publication_id: id(3), source_system_id: id(8), source_name: '合成控制来源',
      captured_at: '2026-09-20T00:00:00Z', published_at: '2026-09-20T00:01:00Z', valid_until: '2099-09-20T00:45:00Z', record_count: 0, is_latest: true }] };
  const common = { ...anchors, schema_version: '1.0', start_ready: false, control_evidence_status: 'control_evidence_not_evaluated' };
  if (stage === 'regions') return { ...common, next_after_id: null, items: [{ region_org_id: id(2), code: 'REGION', name: '测试区域', province_code: '330000' }] };
  if (stage === 'asset-owners') return { ...common, region_org_id: id(2), next_after_id: null, items: [{ owner_org_id: id(2), code: 'OWNER', name: '资产区域' }] };
  if (stage === 'locations') return { ...common, region_org_id: id(2), owner_org_id: id(2), next_after_id: null, items: [{ location_id: id(4), code: 'LOC', name: '个人仓', location_type: 'personal', physical_owner_org_id: id(2), physical_owner_name: '实物区域', custodian_person_id: id(5), custodian_name: '保管人' }] };
  if (stage === 'assignees') return { ...common, region_org_id: id(2), owner_org_id: id(2), location_id: id(4), next_after_person_id: null, items: [{ person_id: id(5), assignee_user_id: 'counter-001', name: '执行人' }] };
  throw new Error('Unexpected synthetic URL');
}
beforeEach(() => { vi.mocked(apiNoReplay).mockReset(); vi.mocked(api).mockReset(); vi.mocked(mutationHeaders).mockClear(); });

it('uses real identity, permission, all selection directories and batch parsers exclusively over no-replay reads', async () => {
  vi.mocked(apiNoReplay).mockImplementation(async (path) => wire(path) as never);
  const ports = createOpeningStartPorts(); await ports.identity(actor); await ports.verifySelection(input, actor);
  const calls = vi.mocked(apiNoReplay).mock.calls;
  expect(calls.map(([path]) => path)).toEqual(['/auth/me','/access/context',
    '/v1/stocktakes/opening/start-options/regions?limit=50',
    `/v1/stocktakes/opening/start-options/asset-owners?limit=50&region_org_id=${id(2)}`,
    `/v1/stocktakes/opening/start-options/locations?limit=50&region_org_id=${id(2)}&owner_org_id=${id(2)}`,
    `/v1/stocktakes/opening/start-options/assignees?limit=50&region_org_id=${id(2)}&owner_org_id=${id(2)}&location_id=${id(4)}`,
    `/v1/stocktakes/opening/start-options/control-batches?region_org_id=${id(2)}&limit=50`]);
  for (const [,init] of calls) { expect(init?.cache).toBe('no-store'); expect(init?.body).toBeUndefined(); expect(new Headers(init?.headers).has('Idempotency-Key')).toBe(false); }
  expect(api).not.toHaveBeenCalled(); expect(mutationHeaders).not.toHaveBeenCalled(); expect(OPENING_START_ENABLED).toBe(false);
});
it('refuses stale identity, removed manage permission and a person UUID substituted for the actual user reference', async () => {
  const ports = createOpeningStartPorts();
  vi.mocked(apiNoReplay).mockResolvedValue({ ...user, authorization_version: 8 });
  await expect(ports.identity(actor)).rejects.toThrow();
  vi.mocked(apiNoReplay).mockImplementation(async (path) => (path === '/auth/me' ? user : { ...access(), permissions: [] }) as never);
  await expect(ports.identity(actor)).rejects.toThrow();
  vi.mocked(apiNoReplay).mockImplementation(async (path) => wire(path) as never);
  await expect(ports.verifySelection({ ...input, scopes: [{ ...input.scopes[0], assignee_user_id: id(5) }] }, actor)).rejects.toThrow('范围或人员已变化');
  expect(api).not.toHaveBeenCalled(); expect(mutationHeaders).not.toHaveBeenCalled();
});
it('sends the selected-publication body with one coordinate pair and never retries a failed POST', async () => {
  const ports = createOpeningStartPorts(), coordinates = ports.coordinates();
  expect(mutationHeaders).toHaveBeenCalledExactlyOnceWith('opening-start');
  vi.mocked(apiNoReplay).mockRejectedValue(new Error('network'));
  await expect(ports.post(input, coordinates)).rejects.toThrow('network');
  expect(apiNoReplay).toHaveBeenCalledExactlyOnceWith('/v1/stocktakes/opening/from-publication', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': coordinates.idempotencyKey, 'X-Request-ID': coordinates.requestId }, body: JSON.stringify(input) });
  expect(api).not.toHaveBeenCalled();
});
it('recovery sends only the three public coordinates and independently rejects malformed task details', async () => {
  const ports = createOpeningStartPorts(); vi.mocked(apiNoReplay).mockResolvedValue({ task_id: id(6), task_no: 'OPEN-WIRE', region_org_id: id(2) });
  await ports.lookup(marker); await expect(ports.detail(id(6))).rejects.toThrow();
  expect(vi.mocked(apiNoReplay).mock.calls.map(([path]) => path)).toEqual([
    `/v1/stocktakes/opening/start-command-result?region_org_id=${id(2)}&publication_id=${id(3)}&trace_request_id=request-00000001`, `/v1/stocktakes/opening/${id(6)}`]);
  for (const [,init] of vi.mocked(apiNoReplay).mock.calls) expect(init).toEqual({ method: 'GET', cache: 'no-store', headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } });
  expect(api).not.toHaveBeenCalled(); expect(mutationHeaders).not.toHaveBeenCalled();
});
it('terminal intent uses only exact original coordinates with a deterministic single-send key', async () => {
  const ports = createOpeningStartPorts();
  const original = { v: 1 as const, kind: 'opening_start' as const, region_org_id: id(2), publication_id: id(3),
    actor_person_id: actor.person_id, actor_authorization_version: actor.authorization_version, trace_request_id: 'request-00000001' };
  await ports.seal(original);
  expect(apiNoReplay).toHaveBeenCalledWith('/v1/stocktakes/opening/seal-start-command', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'opening-start-seal:request-00000001', 'X-Request-ID': 'request-00000001' },
    body: JSON.stringify({ region_org_id: id(2), publication_id: id(3), trace_request_id: 'request-00000001' }) });
});
