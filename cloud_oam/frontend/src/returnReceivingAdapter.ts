import { apiNoReplay, ApiError } from './api';
import { canonical, detail, directory, fail, history, id, integer, object, text, timestamp, type History } from './formalReturnReceiving';
import { hash, state as inboundState } from './formalReturnInbound';
import { requestHash } from './formalReturnReceipt';
import { pending, type Context, type Pending, type Transport } from './returnReceivingRecovery';

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
const BASE = '/v1/stock-returns/my-receiving';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache' };
const read: RequestInit = { cache: 'no-store', headers };
const ROLES = ['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'];
export function createAdapter(person: string, request: Requester = apiNoReplay) {
  const personId = id(person);
  async function context(): Promise<Context> {
    const me = object(await request('/auth/me', read), ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes']);
    const access = object(await request('/access/context', read), ['person_id', 'account_status', 'employment_status', 'authorization_version', 'access_mode', 'role_codes', 'assignments', 'permissions']);
    if ([me, access].some(r => r.person_id !== personId || r.account_status !== 'active' || r.employment_status !== 'active' || r.access_mode !== 'active') || integer(me.authorization_version, 1) !== integer(access.authorization_version, 1)) fail('当前接收身份已变化');
    const roles = access.role_codes;
    if (!Array.isArray(roles) || roles.some(r => !ROLES.includes(r)) || new Set(roles).size !== roles.length || !Array.isArray(me.role_codes) || canonical([...roles].sort()) !== canonical([...me.role_codes].sort())) fail();
    if (!Array.isArray(access.assignments) || access.assignments.length > 1000 || !Array.isArray(access.permissions) || access.permissions.length > 1000) fail();
    const assignments = access.assignments.map(v => {
      const r = object(v, ['assignment_id', 'role_code', 'scope_type', 'scope_id', 'valid_from', 'valid_to']);
      id(r.assignment_id); if (!ROLES.includes(String(r.role_code))) fail(); text(r.scope_type, 100); text(r.scope_id, 100);
      timestamp(r.valid_from); if (r.valid_to !== null) timestamp(r.valid_to); return r;
    });
    const permissions = access.permissions.map(v => {
      const r = object(v, ['resource', 'action', 'field_code']); text(r.resource, 100); text(r.action, 100); if (typeof r.field_code !== 'string') fail(); return r;
    });
    const now = Date.now();
    const assigned = assignments.some(r => roles.includes(r.role_code) && ['admin', 'provincial_manager'].includes(String(r.role_code))
      && Date.parse(String(r.valid_from)) <= now && (r.valid_to === null || Date.parse(String(r.valid_to)) > now));
    const allows = (action: string) => assigned && permissions.some(r => r.resource === 'stock_operation' && r.action === action && r.field_code === '');
    const sort = (rows: unknown[]) => [...rows].sort((a, b) => canonical(a) < canonical(b) ? -1 : 1);
    return { person_id: personId, authorization_version: integer(me.authorization_version, 1), can_read: allows('read'), can_write: allows('receive_return'), authority_hash: await hash({ assignments: sort(assignments), permissions: sort(permissions), roles: [...roles].sort() }) };
  }
  const checked = (value: Pending) => { const p = pending(value); if (p.person_id !== personId) fail('原请求属于其他人员'); return p; };
  const path = (p: Pending) => p.kind === 'receipt' ? `${BASE}/${p.package.shipment_id}/receipts` : `${BASE}/${p.receipt.receipt_id}/inbound`;
  const command = (p: Pending) => p.kind === 'receipt' ? p.command : p.original.command;
  const post = (url: string, body?: unknown, extra: Record<string, string> = {}) => request(url, { method: 'POST', cache: 'no-store', headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...extra }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  async function readable() { const c = await context(); if (!c.can_read) fail('当前没有区域仓退回接收权限'); return c; }
  async function stable(before: Context) { if (canonical(before) !== canonical(await context())) fail('查询期间权限或身份变化，请重新进入'); }
  const transport: Transport = {
    context,
    history: shipment => request(`${BASE}/${id(shipment)}/receipts`, read),
    state(value) { const p = checked(value); if (p.kind !== 'inbound') fail(); return request(path(p), read); },
    preview(value) {
      const p = checked(value);
      if (p.kind === 'inbound') return post(`${path(p)}/preview`);
      const { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...body } = p.command;
      return post(`${path(p)}/preview`, body);
    },
    async lookup(value) {
      const p = checked(value);
      try { return { observed: true, value: await request(`${path(p)}/by-request/${command(p).request_id}`, read) }; }
      catch (error) {
        const code = p.kind === 'receipt' ? 'stock_return_receipt_not_observed' : 'stock_return_inbound_not_observed';
        if (error instanceof ApiError && error.responseReceived && error.status === 404 && error.code === code) return { observed: false };
        throw error;
      }
    },
    submit(value) {
      const p = checked(value), c = command(p);
      return post(path(p), c, { 'X-Request-ID': c.request_id, 'Idempotency-Key': c.idempotency_key });
    },
    async seal(value) {
      const p = checked(value), c = command(p);
      let body: unknown;
      if (p.kind === 'receipt') { const { expected_plan_hash: _p, request_id: _r, idempotency_key: _k, ...intent } = p.command; body = { operator_person_id: p.person_id, request_hash: await requestHash(p.package.shipment_id, intent) }; }
      else body = { request_hash: p.original.request_hash };
      return post(`${path(p)}/by-request/${c.request_id}/seal`, body, { 'X-Request-ID': c.request_id });
    },
  };
  return {
    ...transport,
    async list(after: string | null = null, limit = 10) {
      integer(limit, 1, 20); const before = await readable(), params = new URLSearchParams({ limit: String(limit) }); if (after !== null) params.set('after_id', id(after));
      const result = directory(await request(`${BASE}?${params}`, read), before, limit, after); await stable(before); return result;
    },
    async read(shipment: string): Promise<History> {
      const before = await readable();
      const pkg = detail(await request(`${BASE}/${id(shipment)}`, read), before, shipment);
      const result = history(await transport.history(shipment), before, shipment);
      if (canonical(pkg.package) !== canonical(result.package)) fail('查询期间原包裹变化'); await stable(before); return result;
    },
    async inboundState(current: History, receiptId: string) {
      const before = await readable(), original = current.receipts.find(r => r.receipt_id === id(receiptId)); if (!original) fail();
      const result = inboundState(await request(`${BASE}/${id(receiptId)}/inbound`, read), before, original); await stable(before); return result;
    },
  };
}
export type Adapter = ReturnType<typeof createAdapter>;
