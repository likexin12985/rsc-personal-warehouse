import { createStopAdapter, type StopAdapter } from './lossReturnStopAdapter';
import { returnHistory, type ReturnHistory } from './lossReturnHistory';
import { conditionHistory, type ConditionHistory } from './returnConditionHistory';
import { createConditionAdapter } from './returnConditionAdapter';
import type { Transport as ConditionTransport } from './returnConditionRecovery';
import { canonical, hash, id, identity, object, type Queue, type Report, type LossLine } from './formalLossReview';
import { fail, flow, intentFor, prepare, sources, type Flow, type Pending, type Sources } from './lossCorrectionContracts';
import { createAdapter as createExecutionAdapter } from './lossExecutionAdapter';
import type { Context, Transport } from './lossCorrectionRecovery';
type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export type Detail = { source: Sources; report: Report; line: LossLine };
export type Adapter = Transport & { returnStop: StopAdapter; list(after?: string | null): Promise<Queue>;
  read(root: string): Promise<Sources>; describe(root: string): Promise<Detail>;
  history(root: string): Promise<ReturnHistory>;
  conditionHistory?(root: string, inbound: string): Promise<ConditionHistory>;
  conditionRecovery?: ConditionTransport;
  prepare(root: string, action: Flow, reason: string, choice?: string): Promise<Pending> };
const base = '/v1/stock-operations/loss-reports/corrections';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache' };
const noCache: RequestInit = { cache: 'no-store', headers };
export function createAdapter(personId: string, requestNoReplay: Requester): Adapter {
  const expectedPerson = id(personId), origins = createExecutionAdapter(personId, requestNoReplay);
  async function context():Promise<Context>{
    const me=object(await requestNoReplay('/auth/me',noCache),['person_id','name','employee_no','organization_code','organization_name','account_status','employment_status','access_mode','authorization_version','role_codes']);
    const access=object(await requestNoReplay('/access/context',noCache),['person_id','account_status','employment_status','authorization_version','access_mode','role_codes','assignments','permissions']);
    const a=identity(me),b=identity(access);
    if(a.person_id!==expectedPerson||canonical(a)!==canonical(b)||[me,access].some(x=>x.account_status!=='active'||x.employment_status!=='active'||x.access_mode!=='active'))fail('登录身份已变化，请重新进入纠正页');
    const roles=access.role_codes;
    if(!Array.isArray(roles)||!roles.length||roles.some(r=>!['admin','provincial_manager','technician','star_headquarters_approver'].includes(r))||new Set(roles).size!==roles.length||!Array.isArray(me.role_codes)||canonical([...roles].sort())!==canonical([...me.role_codes].sort()))fail();
    if(!Array.isArray(access.assignments)||access.assignments.length>1000||!Array.isArray(access.permissions)||access.permissions.length>1000)fail();
    const assignments=access.assignments.map(v=>object(v,['assignment_id','role_code','scope_type','scope_id','valid_from','valid_to']));
    const permissions=access.permissions.map(v=>object(v,['resource','action','field_code']));
    for(const g of assignments){id(g.assignment_id);if(typeof g.scope_id!=='string'||typeof g.valid_from!=='string'||!Number.isFinite(Date.parse(g.valid_from)))fail();if(g.valid_to!==null&&(typeof g.valid_to!=='string'||!Number.isFinite(Date.parse(g.valid_to))))fail();}
    const role='admin';
    const now=Date.now();
    const assigned=roles.includes(role)&&assignments.some(g=>g.role_code===role&&g.scope_type==='national'&&g.scope_id==='*'&&Date.parse(g.valid_from as string)<=now&&(g.valid_to===null||Date.parse(g.valid_to as string)>now));
    const allows=(action:string)=>assigned&&permissions.some(p=>p.resource==='stock_operation'&&p.action===action&&p.field_code==='');
    const sort=(rows:unknown[])=>[...rows].sort((a,b)=>canonical(a)<canonical(b)?-1:1);
    return {...a,authority_hash:await hash({assignments:sort(assignments),permissions:sort(permissions),roles:[...roles].sort()}),can_read:allows('read'),can_write:{inverses:allows('reverse_loss'),approvals:allows('approve_loss_correction'),executions:allows('correct_loss')}};
  }

  async function checked(action?: Flow) {
    const c = await context();
    if (!c.can_read || (action && !c.can_write[flow(action)])) fail('当前缺少此纠正操作的权限');
    return c;
  }
  async function stable(before: Context) {
    if (canonical(before) !== canonical(await context())) fail('读取期间身份或权限已变化，请重新核验');
  }
  async function source(root: string, current: Context) {
    return sources(await requestNoReplay(`${base}/sources/${id(root)}`, noCache), current, root);
  }
  function post(p: Pending, suffix: string) {
    return requestNoReplay(`${base}/${flow(p.flow)}${suffix}`, { method: 'POST', cache: 'no-store',
      headers: { ...headers, 'Content-Type': 'application/json', 'X-Request-ID': p.command.request_id, 'Idempotency-Key': p.command.idempotency_key },
      body: JSON.stringify(p.command) });
  }
  return {
    context, source, returnStop: createStopAdapter(expectedPerson, requestNoReplay, context),
    conditionRecovery: createConditionAdapter(expectedPerson, requestNoReplay),
    async list(after = null) { const c = await checked(), page = await origins.list(after); await stable(c); return page; },
    async read(root) { const c = await checked(), s = await source(root, c); await stable(c); return s; },
    async describe(root) {
      const c = await checked(), s = await source(root, c), origin = await origins.read(s.operation_id);
      const decision = origin.decisions.find(d => d.original_posting?.disposition_id === s.root_disposition_id);
      const line = origin.report.lines.find(l => l.line_id === s.line_id);
      if (!decision || decision.line_id !== s.line_id || !line || line.quantity !== s.quantity || canonical(line.serials.map(sn => sn.serial_id).sort()) !== canonical([...s.serial_ids].sort())) fail('原处置与报损明细不能对应，请刷新核验');
      await stable(c); return { source: s, report: origin.report, line };
    },
    async conditionHistory(root, inbound) {
      const c = await checked(), s = await source(root, c), origin = await origins.read(s.operation_id);
      const line = origin.report.lines.find(line => line.line_id === s.line_id);
      if (!line || line.quantity !== s.quantity || canonical(line.serials.map(sn => sn.serial_id).sort()) !== canonical([...s.serial_ids].sort())) fail('原报损物料与成色纠正来源不一致');
      const value = conditionHistory(await requestNoReplay(`/v1/stock-operations/loss-reports/return-condition-corrections/history/${id(inbound)}`, noCache),
        { inbound, root, material: line.material_id });
      await stable(c); return value;
    },
    async history(root) {
      const c = await checked(), s = await source(root, c);
      if (!s.history.some(h => h.kind === 'original_execution' && h.fact_id === root && h.disposition === 'return_to_region')) fail('原处置不是退回，不能查询退回履约');
      const value = returnHistory(await requestNoReplay(`${base}/return-history/${id(root)}`, noCache),
        { root, quantity: s.quantity, serials: s.serial_ids });
      await stable(c); return value;
    },
    async prepare(root, action, why, choice) {
      const f = flow(action), c = await checked(f), s = await source(root, c), body = intentFor(s, f, why, choice);
      const preview = f === 'approvals' ? null : await requestNoReplay(`${base}/${f}/preview`, { ...noCache,
        method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      await stable(c); return prepare(s, f, body, preview);
    },
    lookup(p) { return post(p, '/request-lookup'); },
    execute(p) { return post(p, ''); },
    seal(p) { return post(p, '/request-seal'); },
  };
}
