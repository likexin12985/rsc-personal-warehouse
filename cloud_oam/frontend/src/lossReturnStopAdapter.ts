import { canonical, id } from './formalLossReview';
import { fail } from './lossCorrectionContracts';
import type { Context } from './lossCorrectionRecovery';
import { prepareStop, stopIntent, stopSource, type StopPending, type StopSource } from './lossReturnStopContracts';
import type { Transport } from './lossReturnStopRecovery';

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export type StopAdapter = Transport & { read(root: string): Promise<StopSource>; prepare(root: string, reason: string): Promise<StopPending> };
const base = '/v1/stock-operations/loss-reports/corrections/return-stops';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache' };
const noCache: RequestInit = { cache: 'no-store', headers };

export function createStopAdapter(personId: string, requestNoReplay: Requester, context: () => Promise<Context>): StopAdapter {
  const expectedPerson = id(personId);
  async function checked(write = false) {
    const current = await context();
    if (current.person_id !== expectedPerson || !current.can_read || (write && !current.can_write.inverses)) fail('当前缺少退回停止权限');
    return current;
  }
  async function stable(before: Context) {
    if (canonical(before) !== canonical(await context())) fail('读取期间身份或权限变化，请重新核验');
  }
  async function source(root: string, current: Context) {
    return stopSource(await requestNoReplay(`${base}/sources/${id(root)}`, noCache), current, root);
  }
  function post(pending: StopPending, suffix: string) {
    return requestNoReplay(base + suffix, { method: 'POST', cache: 'no-store',
      headers: { ...headers, 'Content-Type': 'application/json', 'X-Request-ID': pending.command.request_id, 'Idempotency-Key': pending.command.idempotency_key },
      body: JSON.stringify(pending.command) });
  }
  return {
    context, source,
    async read(root) { const current = await checked(), value = await source(root, current); await stable(current); return value; },
    async prepare(root, why) {
      const current = await checked(true), value = await source(root, current), selection = stopIntent(value, why);
      const preview = await requestNoReplay(base + '/preview', { ...noCache, method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(selection) });
      await stable(current); return prepareStop(value, why, preview);
    },
    execute(pending) { return post(pending, ''); },
    lookup(pending) { return post(pending, '/request-lookup'); },
    seal(pending) { return post(pending, '/request-seal'); },
  };
}
