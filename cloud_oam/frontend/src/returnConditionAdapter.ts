import { canonical, hash, id, identity, object } from './formalLossReview';
import { conditionHistory, conditionTransitionAllowed, type Action, type ConditionHistory } from './returnConditionHistory';
import { action, command, fail, prepare as prepareOriginal, type Pending, type Scan } from './returnConditionCommands';
import type { Context, Transport } from './returnConditionRecovery';
import { conditionSource, conditionUnits as quantity, type ConditionSource } from './returnConditionSource';

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export const conditionPermissions: Record<Action, string> = {
  submit: 'submit_return_condition', supplement: 'supplement_return_condition', withdraw: 'withdraw_return_condition',
  execute: 'execute_return_condition', release: 'release_return_condition', verify_region: 'review_return_condition_regional',
  return_evidence: 'review_return_condition_regional', reject_region: 'review_return_condition_regional',
  return_region: 'review_return_condition_headquarters', reject_hq: 'review_return_condition_headquarters',
  approve_hq: 'review_return_condition_headquarters', cancel_approved: 'cancel_return_condition_approval',
};
const headquarters = new Set<Action>(['return_region', 'reject_hq', 'approve_hq', 'cancel_approved']);
const requester = new Set<Action>(['supplement', 'withdraw', 'execute', 'release']);
const base = '/v1/stock-operations/loss-reports/return-condition-corrections';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache' }, noCache: RequestInit = { cache: 'no-store', headers };
export type ConditionIntent = { reason: string; evidence_file_ids: string[]; quantity: string; scans: readonly Scan[] };
export type PreparedCondition = { pending: Pending; history: ConditionHistory; source: ConditionSource | null };
export type ConditionInbox = { person_id: string; authorization_version: number; view: 'pending' | 'all'; items: ConditionHistory[]; next_after_id: string | null };
export type ConditionAdapter = Transport & {
  inbox(view: 'pending' | 'all', after?: string | null): Promise<ConditionInbox>;
  download(inbound: string, event: string, file: string): Promise<{ url: string; expires_at: string }>;
  receipt(receipt: string, shipment: string, root: string): Promise<ConditionHistory[]>;
  read(inbound: string): Promise<ConditionHistory>;
  source(inbound: string): Promise<ConditionSource>;
  prepare(inbound: string, action: Action, caseId: string | null, intent: ConditionIntent): Promise<PreparedCondition>;
};

export function createConditionAdapter(person: string, requestNoReplay: Requester): ConditionAdapter {
  const expectedPerson = id(person);
  const regionalScans = new Map<string, { original: string; scans: readonly Scan[] }>();
  async function context(kind: Action): Promise<Context> {
    if (!Object.hasOwn(conditionPermissions, kind)) fail();
    const me = object(await requestNoReplay('/auth/me', noCache), ['person_id', 'name', 'employee_no', 'organization_code', 'organization_name',
      'account_status', 'employment_status', 'authorization_version', 'access_mode', 'role_codes']);
    const access = object(await requestNoReplay('/access/context', noCache), ['person_id', 'account_status', 'employment_status',
      'authorization_version', 'access_mode', 'role_codes', 'assignments', 'permissions']);
    const who = identity(me);
    if (who.person_id !== expectedPerson || canonical(who) !== canonical(identity(access))
        || [me, access].some(v => v.account_status !== 'active' || v.employment_status !== 'active' || v.access_mode !== 'active')) fail();
    const roles = access.role_codes;
    if (!Array.isArray(roles) || roles.length > 100 || roles.some(v => !['admin', 'provincial_manager', 'technician', 'star_headquarters_approver'].includes(v))
        || new Set(roles).size !== roles.length || !Array.isArray(me.role_codes) || canonical([...roles].sort()) !== canonical([...me.role_codes].sort())
        || !Array.isArray(access.assignments) || access.assignments.length > 1000 || !Array.isArray(access.permissions) || access.permissions.length > 1000) fail();
    const assignments = access.assignments.map(v => object(v, ['assignment_id', 'role_code', 'scope_type', 'scope_id', 'valid_from', 'valid_to']));
    const permissions = access.permissions.map(v => object(v, ['resource', 'action', 'field_code']));
    for (const a of assignments) {
      id(a.assignment_id);
      if (typeof a.role_code !== 'string' || !roles.includes(a.role_code) || typeof a.scope_type !== 'string' || typeof a.scope_id !== 'string'
          || typeof a.valid_from !== 'string' || !Number.isFinite(Date.parse(a.valid_from))
          || (a.valid_to !== null && (typeof a.valid_to !== 'string' || !Number.isFinite(Date.parse(a.valid_to))))) fail();
    }
    for (const p of permissions) if (Object.values(p).some(v => typeof v !== 'string')) fail();
    const now = Date.now(), active = assignments.filter(a => Date.parse(a.valid_from as string) <= now && (a.valid_to === null || Date.parse(a.valid_to as string) > now));
    const allows = (resource: string, action: string) => permissions.some(p => p.resource === resource && p.action === action && p.field_code === '');
    const role = headquarters.has(kind) ? 'admin' : 'provincial_manager';
    const sort = (values: unknown[]) => [...values].sort((a, b) => canonical(a) < canonical(b) ? -1 : canonical(a) > canonical(b) ? 1 : 0);
    return { ...who, action: kind, authority_hash: await hash({ roles: [...roles].sort(), assignments: sort(assignments), permissions: sort(permissions) }),
      can_read: active.length > 0 && allows('stock_operation', 'read') && allows('inventory', 'read'),
      can_write: active.some(a => a.role_code === role) && allows('stock_operation', conditionPermissions[kind]) };
  }
  function post(p: Pending, suffix: string) {
    const original = command(p.original);
    return requestNoReplay(`${base}/${suffix}`, { method: 'POST', cache: 'no-store',
      headers: { ...headers, 'Content-Type': 'application/json', 'X-Request-ID': original.request_id, 'Idempotency-Key': original.idempotency_key },
      body: JSON.stringify(suffix === 'commands' ? original : { operator_person_id: p.person_id, original }) });
  }
  async function rawHistory(inbound: string) {
    const raw = await requestNoReplay(`${base}/history/${id(inbound)}`, noCache);
    if (!raw || typeof raw !== 'object') fail();
    const r = raw as Record<string, unknown>;
    return conditionHistory(raw, { inbound, root: id(r.root_disposition_id), material: id(r.material_id) });
  }
  async function stable(before: Context) {
    if (canonical(before) !== canonical(await context(before.action))) fail('读取期间权限变化，请重新核验');
  }
  function checkedScans(history: ConditionHistory, ids: string[], scans: readonly Scan[]) {
    if (canonical([...ids].sort()) !== canonical(scans.map(s => s.serial_id).sort())) fail('必须逐件核对本次全部实物');
    for (const s of scans) if (s.sku_code !== history.sku_code || !history.serials.some(row => row.serial_id === s.serial_id
        && row.serial_no === s.serial_no && row.qr_code === s.qr_code)) fail('实物物料号、SN或二维码与当前记录不一致');
  }
  const result: ConditionAdapter = {
    context,
    async inbox(view, after = null) {
      if (!['pending', 'all'].includes(view)) fail();
      const before = await context('submit'); if (!before.can_read) fail();
      const suffix = after === null ? '' : `&after_id=${id(after)}`;
      const r = object(await requestNoReplay(`${base}/inbox?view=${view}${suffix}`, noCache),
        ['schema_version', 'person_id', 'authorization_version', 'view', 'items', 'next_after_id', 'current_stock_verified', 'posting_allowed']);
      if (r.schema_version !== 'condition_inbox/1' || r.person_id !== before.person_id || r.authorization_version !== before.authorization_version
          || r.view !== view || r.current_stock_verified !== false || r.posting_allowed !== false || !Array.isArray(r.items) || r.items.length > 10) fail();
      const items = r.items.map(raw => { const item = raw as Record<string, unknown>;
        return conditionHistory(raw, { inbound: id(item.inbound_line_id), root: id(item.root_disposition_id), material: id(item.material_id) }); });
      const ids = items.map(item => item.inbound_line_id);
      if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort()) || (after !== null && ids.some(i => i <= after))
          || items.some(item => !item.cases.length || view === 'pending' && item.cases.every(c => ['executed', 'released_rejected', 'released_cancelled'].includes(c.status)))) fail();
      const next = r.next_after_id === null ? null : id(r.next_after_id);
      if (next !== null && (ids.length === 0 || next !== ids.at(-1))) fail();
      await stable(before);
      return { person_id: before.person_id, authorization_version: before.authorization_version, view, items, next_after_id: next };
    },
    async download(inbound, event, file) {
      const before = await context('submit'); if (!before.can_read) fail();
      const history = await rawHistory(inbound);
      if (!history.events.some(e => e.fact.event_id === id(event) && e.evidence_file_ids.includes(id(file)))) fail('附件不属于该原事件');
      await stable(before);
      const r = object(await requestNoReplay(`/v1/files/${id(file)}/download-intent`,
        { ...noCache, headers: { ...headers, 'X-Request-ID': crypto.randomUUID() } }),
        ['schema_version', 'file_id', 'purpose', 'status', 'download']);
      if (r.schema_version !== '1.0' || r.file_id !== file || r.purpose !== 'return_condition_evidence' || r.status !== 'available') fail();
      const d = object(r.download, ['method', 'url', 'expires_at']);
      if (d.method !== 'GET' || typeof d.url !== 'string' || typeof d.expires_at !== 'string' || d.url.length > 8192) fail();
      const url = new URL(d.url), expiry = Date.parse(d.expires_at);
      await stable(before);
      if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.hash
          || !Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.now() + 630000) fail('附件临时链接已失效，请重新核验');
      return { url: d.url, expires_at: d.expires_at };
    },
    async receipt(receipt, shipment, root) {
      const before = await context('submit'); if (!before.can_read) fail();
      const r = object(await requestNoReplay(`${base}/receipts/${id(receipt)}`, noCache),
        ['schema_version', 'receipt_id', 'shipment_id', 'root_disposition_id', 'operator_person_id', 'authorization_version',
          'status', 'inbound_id', 'items', 'current_stock_verified', 'posting_allowed']);
      if (r.schema_version !== 'condition_receipt_history/1' || id(r.receipt_id) !== id(receipt)
          || id(r.shipment_id) !== id(shipment) || id(r.root_disposition_id) !== id(root)
          || id(r.operator_person_id) !== expectedPerson || r.authorization_version !== before.authorization_version
          || r.current_stock_verified !== false || r.posting_allowed !== false || !['posted', 'not_posted'].includes(String(r.status))
          || (r.status === 'posted') !== (r.inbound_id !== null) || !Array.isArray(r.items) || r.items.length > 100
          || (r.status === 'not_posted' && r.items.length)) fail();
      if (r.inbound_id !== null) id(r.inbound_id);
      const items = r.items.map(raw => { const item = raw as Record<string, unknown>;
        return conditionHistory(raw, { inbound: id(item.inbound_line_id), root, material: id(item.material_id) }); });
      if (new Set(items.map(i => i.inbound_line_id)).size !== items.length) fail();
      await stable(before); return items;
    },
    async read(inbound) { const before = await context('submit'); if (!before.can_read) fail();
      const value = await rawHistory(inbound); await stable(before); return value; },
    async source(inbound) { const before = await context('submit'); if (!before.can_read || !before.can_write) fail();
      const value = conditionSource(await requestNoReplay(`${base}/sources/${id(inbound)}`, noCache), inbound, expectedPerson);
      await stable(before); return value; },
    async prepare(inbound, kind, caseId, intent) {
      const before = await context(kind); if (!before.can_read || !before.can_write) fail('没有本次操作权限');
      const history = await rawHistory(inbound);
      const source = kind === 'submit' ? await result.source(inbound) : null;
      if (source && (source.material_id !== history.material_id || source.root_disposition_id !== history.root_disposition_id
          || source.source_account_id !== history.source_account_id || source.recorded_condition !== history.recorded_condition)) fail('当前来源与历史入库不能对应');
      const selected = history.cases.find(c => c.case_id === caseId);
      if ((kind === 'submit') !== (caseId === null) || (kind !== 'submit' && !selected)) fail();
      const physical = kind === 'submit' || kind === 'verify_region' || kind === 'execute' || kind === 'release';
      if (physical) checkedScans(history, kind === 'submit' ? intent.scans.map(s => s.serial_id) : selected!.serial_ids, intent.scans);
      const common = { reason: intent.reason, evidence_file_ids: [...intent.evidence_file_ids], request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() };
      const original = kind === 'submit' ? { ...common, action: 'submit_return_condition', inbound_line_id: inbound,
        expected_source_hash: source!.expected_source_hash, quantity: intent.quantity, serial_verifications: [...intent.scans] }
        : { ...common, action: kind, case_id: selected!.case_id, expected_event_id: selected!.latest_event_id,
          expected_event_hash: selected!.latest_event_hash, ...(['execute', 'release'].includes(kind) ? { serial_verifications: [...intent.scans] } : {}) };
      const pending = await prepareOriginal(expectedPerson, before.authorization_version, inbound, original);
      if (kind === 'verify_region') {
        if (regionalScans.size >= 100) regionalScans.clear();
        regionalScans.set(pending.original.request_id, { original: canonical(pending.original), scans: intent.scans.map(s => ({ ...s })) });
      }
      await result.verifySource(pending, before); await stable(before);
      return { pending, history, source };
    },
    async verifySource(p, current) {
      const c = command(p.original), kind = action(c);
      if (p.person_id !== expectedPerson || current.person_id !== p.person_id || current.action !== kind || !current.can_write || !current.can_read) fail();
      if (c.action === 'submit_return_condition') {
        const s = conditionSource(await requestNoReplay(`${base}/sources/${id(p.inbound_line_id)}`, noCache), p.inbound_line_id, expectedPerson);
        if (s.claimable_quantity === null || s.expected_source_hash !== c.expected_source_hash) fail('来源已变化或仍待核对，请重新选择并核验');
        const amount = quantity(c.quantity), claimed = quantity(s.claimable_quantity);
        if (amount > claimed) fail('申请数量超过本次可纠正份额');
        const tracked = s.tracking_mode === 'serial' || s.tracking_mode === 'lot_and_serial';
        if (tracked ? amount !== BigInt(c.serial_verifications.length) * 1000n : !!c.serial_verifications.length) fail();
        for (const scan of c.serial_verifications) if (!s.serials.some(s => s.serial_id === scan.serial_id && s.serial_no === scan.serial_no
            && s.qr_code === scan.qr_code && s.claimable_for_correction)) fail('实物标识与当前可纠正份额不一致');
      } else {
        const h = await rawHistory(p.inbound_line_id);
        const selected = h.cases.find(row => row.case_id === c.case_id);
        if (!selected || selected.latest_event_id !== c.expected_event_id || selected.latest_event_hash !== c.expected_event_hash
            || !conditionTransitionAllowed(kind, selected.status)) fail('案件状态已变化，请刷新后重新准备');
        const events = h.events.filter(e => e.fact.case_id === selected.case_id), initial = events[0];
        if (!initial || (requester.has(kind) ? initial.fact.actor_person_id !== expectedPerson : initial.fact.actor_person_id === expectedPerson)) fail('当前操作人与案件的独立复核要求不符');
        const regional = events.filter(e => e.fact.action === 'verify_region').at(-1);
        if (headquarters.has(kind) && (!regional || regional.fact.actor_person_id === expectedPerson)) fail('区域核实人与总部审批人必须独立');
        if ('serial_verifications' in c) checkedScans(h, selected.serial_ids, c.serial_verifications);
        if (kind === 'verify_region' && selected.serial_ids.length) {
          const proof = regionalScans.get(c.request_id);
          if (!proof || proof.original !== canonical(c)) fail('请重新逐件核验后准备本次区域复核');
          checkedScans(h, selected.serial_ids, proof.scans);
        }
      }
    },
    submit: async p => { try { return await post(p, 'commands'); } finally { regionalScans.delete(p.original.request_id); } }, lookup: p => post(p, 'request-lookup'), seal: p => post(p, 'request-seal'),
  };
  return result;
}
