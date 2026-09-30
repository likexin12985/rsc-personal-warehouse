import { apiNoReplay } from './api';
import { canonical, fail, id, integer, object, text, timestamp } from './formalReturnReceiving';
import { hash } from './formalReturnInbound';
import { input, lookupInput, pending, preview, selection, selectionInput, serialOptions, sources, type Input, type Pending, type Sources } from './formalLossSubmission';
import type { Context, Transport } from './lossSubmissionRecovery';
type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
const BASE = '/v1/stock-operations/loss-reports';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache' };
const read: RequestInit = { cache: 'no-store', headers };
const ROLES = ['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'];
export function createAdapter(person: string, request: Requester = apiNoReplay) {
  const personId = id(person);
  async function context(): Promise<Context> {
    const me = object(await request('/auth/me', read), ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name', 'account_status', 'employment_status', 'access_mode', 'authorization_version', 'role_codes']);
    const access = object(await request('/access/context', read), ['person_id', 'account_status', 'employment_status', 'authorization_version', 'access_mode', 'role_codes', 'assignments', 'permissions']);
    if ([me, access].some(r => r.person_id !== personId || r.account_status !== 'active' || r.employment_status !== 'active' || r.access_mode !== 'active') || integer(me.authorization_version, 1) !== integer(access.authorization_version, 1)) fail('当前报损身份已变化');
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
    const assigned = assignments.some(r => roles.includes(r.role_code) && ['admin', 'provincial_manager', 'technician'].includes(String(r.role_code))
      && Date.parse(String(r.valid_from)) <= now && (r.valid_to === null || Date.parse(String(r.valid_to)) > now));
    const allows = (action: string) => assigned && permissions.some(r => r.resource === 'stock_operation' && r.action === action && r.field_code === '');
    const sorted = (rows: unknown[]) => [...rows].sort((a, b) => canonical(a) < canonical(b) ? -1 : canonical(a) > canonical(b) ? 1 : 0);
    return { person_id: personId, authorization_version: integer(me.authorization_version, 1), can_read: allows('read'), can_write: allows('submit_loss'), authority_hash: await hash({ assignments: sorted(assignments), permissions: sorted(permissions), roles: [...roles].sort() }) };
  }
  const checked = (value: Pending) => { const p = pending(value); if (p.person_id !== personId) fail('原报损请求属于其他人员'); return p; };
  const post = (suffix: string, body: unknown, extra: Record<string, string> = {}) => request(BASE + suffix, { method: 'POST', cache: 'no-store', headers: { ...headers, 'Content-Type': 'application/json', ...extra }, body: JSON.stringify(body) });
  const coordinates = (p: Pending) => ({ 'X-Request-ID': p.command.request_id, 'Idempotency-Key': p.command.idempotency_key });
  async function writable() { const c = await context(); if (!c.can_read || !c.can_write) fail('当前没有本人报损发起权限，已有请求仍可按读权限回查'); return c; }
  async function stable(before: Context) { if (canonical(before) !== canonical(await context())) fail('查询期间身份或权限变化，请重新进入'); }
  const transport: Transport = {
    context,
    sources: () => request(`${BASE}/sources`, read),
    preview(value) {
      const p = checked(value), { expected_plan_hash: _plan, request_id: _request, idempotency_key: _key, ...body } = p.command;
      return post('/preview', body);
    },
    lookup(value) { const p = checked(value); return post('/request-lookup', lookupInput(p.command, p.preview.request_hash)); },
    submit(value) { const p = checked(value); return post('', p.command, coordinates(p)); },
    seal(value) { const p = checked(value); return post('/request-seal', { ...lookupInput(p.command, p.preview.request_hash), source_location_id: p.sources.location_id }, coordinates(p)); },
  };
  return {
    ...transport,
    async readSources(): Promise<Sources> {
      const before = await writable(), result = sources(await transport.sources(), before);
      await stable(before); return result;
    },
    async serials(current: Sources, accountId: string, after: string | null = null, exact: string | null = null) {
      const before = await writable();
      if (current.person_id !== before.person_id || current.authorization_version !== before.authorization_version) fail('SN 查询身份已变化');
      const params = new URLSearchParams({ limit: '50' });
      if (after !== null) params.set('after_id', id(after));
      if (exact !== null) {
        if (after !== null || text(exact, 200) !== exact.trim() || /[\u0000-\u001f\u007f]/.test(exact)) fail('请使用完整 SN 精确查询');
        params.set('serial_no', exact);
      }
      const result = serialOptions(await request(`/v1/inventory/personal/me/accounts/${id(accountId)}/serials?${params}`, read), current, accountId, after, exact);
      await stable(before); return result;
    },
    async select(value: ReturnType<typeof selectionInput>) {
      const before = await writable(), current = sources(await transport.sources(), before), body = selectionInput(value, personId);
      const result = await selection(await post('/source-preview', body), body, current); await stable(before);
      return { sources: current, selection: result };
    },
    async prepare(value: Input) {
      const before = await writable(), current = sources(await transport.sources(), before), body = input(value, personId);
      const result = await preview(await post('/preview', body), body, current); await stable(before);
      return { sources: current, preview: result };
    },
  };
}
export type Adapter = ReturnType<typeof createAdapter>;
