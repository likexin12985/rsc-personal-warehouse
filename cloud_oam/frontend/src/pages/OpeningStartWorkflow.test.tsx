// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import OpeningStartWorkflow from './OpeningStartWorkflow';
import { createStartStore, START_STORAGE_PREFIX, type StartMarker, type StartPorts } from '../openingStartCore';
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const actor = { person_id: id(1), authorization_version: 7 };
const batch = { publicationId: id(3), sourceId: id(30), sourceName: '合成已发布来源', capturedAt: '2026-09-20T00:00:00Z', publishedAt: '2026-09-20T00:01:00Z', validUntil: '2099-09-20T01:00:00Z', recordCount: 0, isLatest: true };
const choice = { owner: '区域一', location: '个人仓甲', person: '工程师甲', scope: { owner_org_id: id(2), location_id: id(4), assignee_user_id: 'user-count', freeze_mode: 'hard' as const } };
const marker: StartMarker = { v: 1, kind: 'opening_start', region_org_id: id(2), publication_id: id(3), actor_person_id: id(1), actor_authorization_version: 7, trace_request_id: 'request-00000001' };
function world() {
  const values = new Map<string,string>(); let held = false;
  const store = createStartStore({ getItem: (key) => values.get(key) ?? null, setItem: (key,value) => { values.set(key,value); }, removeItem: (key) => { values.delete(key); } },
    { async request(_, __, work) { if (held) return work(null); held = true; try { return await work({}); } finally { held = false; } } });
  const result = { schema_version: '1.0', task_id: id(5), task_no: 'OPEN-UI', status: 'counting', cutoff_ledger_cursor: 0,
    initial_round_id: id(6), scope_count: 1, snapshot_line_count: 0, control_line_count: 0, replayed: false };
  const ports: StartPorts = { identity: vi.fn(async () => {}), verifySelection: vi.fn(async () => {}),
    coordinates: () => ({ requestId: marker.trace_request_id, idempotencyKey: 'idempotency-00000001' }),
    post: vi.fn(async () => result), detail: vi.fn(async () => ({ task_id: id(5), task_no: 'OPEN-UI', region_org_id: id(2) })),
    seal: vi.fn(async () => ({})),
    lookup: vi.fn(async () => ({ schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: 7,
      region_org_id: id(2), publication_id: id(3), outcome: 'found', automatic_retry_allowed: false, seal: null, result: { ...result, replayed: true } })) };
  return { values, store, ports, result, props: { actor, region: id(2), selectedScope: choice, batch, enabled: true, store, ports, onOpen: vi.fn() } };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('collects multiple scopes and explicitly starts exactly once, then opens only the verified task', async () => {
  const w = world(); w.result.scope_count = 2; const page = render(<OpeningStartWorkflow {...w.props} />);
  fireEvent.click(screen.getByRole('button', { name: '加入盘点范围' }));
  page.rerender(<OpeningStartWorkflow {...w.props} selectedScope={{ ...choice, location: '个人仓乙', scope: { ...choice.scope, location_id: id(40) } }} />);
  fireEvent.change(screen.getByLabelText('新增范围的冻结方式'), { target: { value: 'cutoff_replay' } });
  fireEvent.click(screen.getByRole('button', { name: '加入盘点范围' }));
  fireEvent.change(screen.getByLabelText('期初任务编号'), { target: { value: 'OPEN-UI' } });
  const button = screen.getByRole('button', { name: '确认范围并启动期初盘点' });
  fireEvent.click(button); fireEvent.click(button);
  await screen.findByRole('button', { name: '打开已核验任务' });
  expect(w.ports.post).toHaveBeenCalledTimes(1);
  const input = vi.mocked(w.ports.post).mock.calls[0][0];
  expect(input.scopes).toHaveLength(2); expect(input.scopes[1].freeze_mode).toBe('cutoff_replay');
  expect(input).not.toHaveProperty('control_lines'); expect(w.values.size).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: '打开已核验任务' })); expect(w.props.onOpen).toHaveBeenCalledWith(id(5));
});
it('unmount after POST retains coordinates; reopening permits only original-result reads even when new starts are closed', async () => {
  const w = world(); let release!: (value: unknown) => void;
  vi.mocked(w.ports.post).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  const page = render(<OpeningStartWorkflow {...w.props} />);
  fireEvent.click(screen.getByRole('button', { name: '加入盘点范围' }));
  fireEvent.change(screen.getByLabelText('期初任务编号'), { target: { value: 'OPEN-UI' } });
  fireEvent.click(screen.getByRole('button', { name: '确认范围并启动期初盘点' }));
  await waitFor(() => expect(w.ports.post).toHaveBeenCalledTimes(1)); page.unmount();
  await act(async () => { release(w.result); });
  expect(w.store.read(id(2)).kind).toBe('valid');
  render(<OpeningStartWorkflow {...w.props} enabled={false} batch={undefined} selectedScope={undefined} />);
  expect(screen.queryByLabelText('期初任务编号')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '核验原启动结果' }));
  await screen.findByRole('button', { name: '打开已核验任务' });
  expect(w.ports.post).toHaveBeenCalledTimes(1); expect(w.store.read(id(2)).kind).toBe('missing');
});
it('keeps not_observed pending without displaying or resending the lost command', async () => {
  const w = world(); w.values.set(START_STORAGE_PREFIX + id(2), JSON.stringify(marker));
  vi.mocked(w.ports.lookup).mockResolvedValue({ schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: 7,
    region_org_id: id(2), publication_id: id(3), outcome: 'not_observed', automatic_retry_allowed: false, seal: null, result: null });
  render(<OpeningStartWorkflow {...w.props} />);
  fireEvent.click(screen.getByRole('button', { name: '核验原启动结果' }));
  await screen.findByText(/原启动请求仍待核验/);
  expect(w.ports.post).not.toHaveBeenCalled(); expect(screen.queryByLabelText('期初任务编号')).toBeNull();
  expect(screen.queryByRole('button', { name: /清理|丢弃|重试提交/ })).toBeNull();
});
it('hiding a pending POST preserves its marker, keeps the lease busy and recovers on explicit return', async () => {
  const w = world(); let release!: (value: unknown) => void;
  vi.mocked(w.ports.post).mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  render(<OpeningStartWorkflow {...w.props} />);
  fireEvent.click(screen.getByRole('button', { name: '加入盘点范围' }));
  fireEvent.change(screen.getByLabelText('期初任务编号'), { target: { value: 'OPEN-UI' } });
  fireEvent.click(screen.getByRole('button', { name: '确认范围并启动期初盘点' }));
  await waitFor(() => expect(w.ports.post).toHaveBeenCalledTimes(1));
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  fireEvent(document, new Event('visibilitychange'));
  visibility.mockReturnValue('visible'); fireEvent(document, new Event('visibilitychange'));
  expect((screen.getByRole('button', { name: '正在核验启动结果' }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { release(w.result); });
  expect(w.store.read(id(2)).kind).toBe('valid'); expect(w.ports.lookup).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '打开已核验任务' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '核验原启动结果' }));
  await screen.findByRole('button', { name: '打开已核验任务' });
  expect(w.ports.post).toHaveBeenCalledTimes(1); expect(w.store.read(id(2)).kind).toBe('missing');
});
it('default release closure renders no creation form and no request', () => {
  const w = world(); const page = render(<OpeningStartWorkflow {...w.props} enabled={false} />);
  expect(page.container.textContent).toBe(''); expect(w.ports.post).not.toHaveBeenCalled();
});
it('a changed identity cannot clear or inspect the previous person marker', async () => {
  const w = world(); w.values.set(START_STORAGE_PREFIX + id(2), JSON.stringify(marker));
  render(<OpeningStartWorkflow {...w.props} actor={{ ...actor, person_id: id(99) }} />);
  fireEvent.click(screen.getByRole('button', { name: '核验原启动结果' })); await screen.findByText(/原启动请求仍待核验/);
  expect(w.ports.lookup).not.toHaveBeenCalled(); expect(w.values.size).toBe(1);
});
it('requires explicit seal confirmation and then shows terminal proof without offering a created task', async () => {
  const w = world(); w.values.set(START_STORAGE_PREFIX + id(2), JSON.stringify(marker));
  const envelope = { schema_version: 'rsc.opening_start_recovery.v2', actor_person_id: actor.person_id, authorization_version: 7,
    region_org_id: id(2), publication_id: id(3), automatic_retry_allowed: false, result: null };
  vi.mocked(w.ports.lookup).mockResolvedValueOnce({ ...envelope, outcome: 'not_observed', seal: null }).mockResolvedValue({ ...envelope,
    outcome: 'sealed', seal: { seal_id: id(80), actor_person_id: id(1), authorization_version: 7, region_org_id: id(2), publication_id: id(3),
      trace_request_id: marker.trace_request_id, sealed_at: '2026-09-20T12:00:00Z', permanent_nonexecution: true } });
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<OpeningStartWorkflow {...w.props} enabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: '终结原启动请求' }));
  await screen.findByText(/原启动请求已永久终结并核验/);
  expect(confirm).toHaveBeenCalledTimes(1); expect(w.ports.seal).toHaveBeenCalledExactlyOnceWith(marker);
  expect(w.ports.post).not.toHaveBeenCalled(); expect(w.values.size).toBe(0);
  expect(screen.queryByRole('button', { name: '打开已核验任务' })).toBeNull();
});
