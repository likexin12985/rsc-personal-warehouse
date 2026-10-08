import { apiNoReplay } from './api';
import { createAdapter as receivingAdapter } from './returnReceivingAdapter';
import { canonical, digest, fail, id, integer, micros, object, requestId } from './formalReturnReceiving';
import { checkSelection, command, detail, fingerprint, inbox, inbound, preview, receipt, type Command, type Detail } from './rejectionWarehouse';

export type Pending = { v: 1; person_id: string; authorization_version: number; return_id: string;
  trace: string; key: string; fingerprint: string; command: Command };
export function pending(value: unknown): Pending {
  const p = object(value, ['v', 'person_id', 'authorization_version', 'return_id', 'trace', 'key', 'fingerprint', 'command']);
  if (p.v !== 1 || typeof p.key !== 'string' || !/^[A-Za-z0-9._:-]{16,128}$/.test(p.key)) fail('原仓库请求无效，请保留记录');
  return { v: 1, person_id: id(p.person_id), authorization_version: integer(p.authorization_version, 1), return_id: id(p.return_id),
    trace: requestId(p.trace), key: p.key, fingerprint: digest(p.fingerprint), command: command(p.command) };
}
type Stored = { kind: 'missing' | 'corrupt' | 'unavailable' } | { kind: 'valid'; value: Pending };
export function createStore(storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
  const name = 'cloud-oam-rejection-warehouse-v1', target = () => storage ?? window.localStorage;
  let unavailable = false;
  function read(): Stored {
    if (unavailable) return { kind: 'unavailable' };
    let raw: string | null;
    try { raw = target().getItem(name); } catch { unavailable = true; return { kind: 'unavailable' }; }
    if (raw === null) return { kind: 'missing' };
    try { return { kind: 'valid', value: pending(JSON.parse(raw)) }; } catch { return { kind: 'corrupt' }; }
  }
  return { read, persist(value: Pending) {
    const p = pending(value); if (read().kind !== 'missing') fail('已有仓库请求待核验，不能覆盖');
    try { target().setItem(name, JSON.stringify(p)); } catch { unavailable = true; fail('无法可靠保存原请求，已停止提交'); }
    const saved = read(); if (saved.kind !== 'valid' || canonical(saved.value) !== canonical(p)) { unavailable = true; fail('原请求保存未通过核验'); }
  }, clear(value: Pending) {
    const saved = read(); if (saved.kind !== 'valid' || canonical(saved.value) !== canonical(value)) fail('原请求变化，禁止清理');
    try { target().removeItem(name); } catch { unavailable = true; fail('无法清理已核验请求'); }
    if (read().kind !== 'missing') fail('原请求仍未清理');
  } };
}
export type Store = ReturnType<typeof createStore>;
type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
const READ: RequestInit = { cache: 'no-store', headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache' } };
const base = '/v1/rejection-returns';
const path = (p: Pending) => `${base}/${p.return_id}/warehouse-receipts${p.command.kind === 'inbound' ? `/${p.command.receipt_id}/inbounds` : ''}`;
export function createAdapter(person: string, request: Requester = apiNoReplay) {
  const personId = id(person), context = receivingAdapter(personId, request).context;
  async function readable() { const c = await context(); if (!c.can_read || c.person_id !== personId) fail('当前无来源仓读取权限'); return c; }
  async function stable(c: Awaited<ReturnType<typeof context>>) {
    if (canonical(c) !== canonical(await context())) fail('当前身份或权限已变化，请保留原请求');
  }
  async function read(returnId: string): Promise<Detail> {
    const c = await readable(), result = detail(await request(`${base}/${id(returnId)}/warehouse`, READ), c, returnId);
    await stable(c); return result;
  }
  const adapter = {
    context,
    async list(after: string | null = null) {
      const c = await readable(), result = inbox(await request(`${base}/my-warehouse?limit=5${after ? `&after_id=${id(after)}` : ''}`, READ), c, after);
      await stable(c); return result;
    },
    read,
    async preview(current: Detail, receiptId: string) {
      const c = await readable(), result = preview(await request(`${base}/${current.source.return_id}/warehouse-receipts/${id(receiptId)}/inbounds/preview`, READ), c, current, receiptId);
      await stable(c); return result;
    },
    async submit(original: Pending, store: Store, canCommit = () => true) {
      const p = pending(original); if (p.person_id !== personId || await fingerprint(p.command) !== p.fingerprint) fail();
      const c = await readable();
      if (!c.can_write || c.authorization_version !== p.authorization_version) fail('写入权限或版本已变化，保留原请求');
      const current = detail(await request(`${base}/${p.return_id}/warehouse`, READ), c, p.return_id);
      if (p.command.kind === 'receipt') checkSelection(p.command.input, current);
      else {
        const planned = preview(await request(path(p) + '/preview', READ), c, current, p.command.receipt_id);
        if (planned.plan_hash !== p.command.input.expected_plan_hash || planned.receipt_request_hash !== p.command.input.receipt_request_hash
            || planned.request_version !== p.command.input.expected_request_version) fail('独立入账方案已变化，保留原请求');
      }
      await stable(c);
      const saved = store.read();
      if (!canCommit() || saved.kind !== 'valid' || canonical(saved.value) !== canonical(p)) fail('页面或原请求变化，已停止提交');
      // One transport invocation. Unknown outcomes are resolved only by GET.
      await request(path(p), { method: 'POST', cache: 'no-store', headers: { ...READ.headers,
        'Content-Type': 'application/json', 'Idempotency-Key': p.key, 'X-Request-ID': p.trace }, body: JSON.stringify(p.command.input) });
    },
    async recover(original: Pending, store: Store, canCommit = () => true) {
      const p = pending(original); if (p.person_id !== personId || await fingerprint(p.command) !== p.fingerprint) fail('原请求身份或指纹不一致');
      const c = await readable(); // Current read access is sufficient after write revocation.
      const status = object(await request(path(p) + '/command-status', { ...READ, headers: { ...READ.headers,
        'Idempotency-Key': p.key, 'X-Request-Fingerprint': p.fingerprint } }), ['lookup_status', 'command']);
      if (status.lookup_status !== 'confirmed' || status.command === null) fail('尚未核验到原请求结果，请保留记录，不要重复提交');
      const current = detail(await request(`${base}/${p.return_id}/warehouse`, READ), c, p.return_id);
      if (p.command.kind === 'receipt') {
        const fact = receipt(status.command, current.source), input = p.command.input;
        const history = current.receipts.find(row => row.receipt.receipt_id === fact.receipt_id);
        if (!fact.replayed || !history || canonical({ ...fact, replayed: true }) !== canonical({ ...history.receipt, replayed: true })
            || fact.receiver_person_id !== p.person_id || fact.request_version !== input.expected_request_version || fact.reason !== input.reason
            || fact.custody_assignment_id !== input.custody_assignment_id || fact.handover_id !== input.handover_id
            || current.source.registration_request_hash !== input.registration_request_hash || current.source.handover_request_hash !== input.handover_request_hash
            || fact.observed_sku_code !== input.observed_sku_code || canonical(fact.amounts) !== canonical(input.amounts)
            || micros(fact.received_at) !== micros(input.received_at)) fail('原验收内容与独立历史不一致');
      } else {
        const receiptId = p.command.receipt_id;
        const row = current.receipts.find(h => h.receipt.receipt_id === receiptId);
        if (!row || !row.inbound || row.receipt.request_hash !== p.command.input.receipt_request_hash) fail('原验收尚未核验到独立入账');
        const fact = inbound(status.command, row.receipt);
        if (!fact.replayed || fact.plan_hash !== p.command.input.expected_plan_hash
            || canonical({ ...fact, replayed: true }) !== canonical({ ...row.inbound, replayed: true })) fail('原入账结果与库存历史不一致');
      }
      await stable(c); if (!canCommit()) fail('页面已变化，保留原请求');
      store.clear(p); return current;
    },
  };
  return adapter;
}
export type Adapter = ReturnType<typeof createAdapter>;
