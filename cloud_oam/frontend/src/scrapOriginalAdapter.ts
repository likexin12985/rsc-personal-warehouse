/** Original scrap only; recovery never reloads a preview or resends a write. */
import { canonical, type Queue } from './formalLossReview';
import { createAdapter as createLossAdapter } from './lossExecutionAdapter';
import type { ApprovedLine, Sources } from './lossExecutionContracts';
import { command, digest, id, object, prepare, verifyPending, type Pending, type ScrapCommand } from './formalScrapCommands';
import { fail, type Kind } from './formalScrapFacts';
import type { Context, Transport } from './formalScrapRecovery';

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export type Preview = { planning_status: 'preview_only'; stock_effect: 'none'; operation_id: string;
  line_id: string; decision_id: string; predecessor_reversal_id: null; source_account_id: string;
  target_account_id: null; source_condition: string; quantity: string; serial_ids: string[];
  plan_hash: string; checked_at: string };
export type Prepared = { pending: Pending; preview: Preview; source: Sources; decision: ApprovedLine };
export type Adapter = Transport & { list(after?: string | null): Promise<Queue>; read(operation: string): Promise<Sources>;
  prepare(operation: string, decision: string, reason: string, evidence: readonly string[]): Promise<Prepared> };
const base='/v1/stock-operations/loss-reports/scraps/originals';
const headers={'Cache-Control':'no-store',Pragma:'no-cache','Content-Type':'application/json'};
function original(p:Pending):ScrapCommand {
  if(p.kind!=='original'||!('execution_reason' in p.original)||p.original.source.kind!=='original')fail();
  return p.original;
}
function decision(source:Sources,identifier:string):ApprovedLine {
  const d=source.decisions.find(d=>d.headquarters_decision_id===id(identifier));
  if(!d||d.disposition!=='scrap'||d.original_posting||source.report.approval_stage!=='approved')fail();
  return d;
}
function preview(value:unknown,s:Sources,d:ApprovedLine):Preview {
  const r=object(value,['planning_status','stock_effect','operation_id','line_id','decision_id','predecessor_reversal_id',
    'source_account_id','target_account_id','source_condition','quantity','serial_ids','plan_hash','checked_at']);
  const line=s.report.lines.find(line=>line.line_id===d.line_id);
  id(r.source_account_id);digest(r.plan_hash);
  if(!line||r.planning_status!=='preview_only'||r.stock_effect!=='none'||r.operation_id!==s.report.operation_id
      ||r.line_id!==d.line_id||r.decision_id!==d.headquarters_decision_id||r.predecessor_reversal_id!==null
      ||r.target_account_id!==null||r.source_condition!==line.condition_code||r.quantity!==line.quantity
      ||!Array.isArray(r.serial_ids)||r.serial_ids.length>10000)fail();
  const serials=r.serial_ids.map(id);
  if(new Set(serials).size!==serials.length||canonical([...serials].sort())!==canonical(line.serials.map(sn=>sn.serial_id).sort()))fail();
  if(typeof r.checked_at!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(r.checked_at))fail();
  const at=Date.parse(r.checked_at);
  if(!Number.isFinite(at)||at>Date.now()+30000||at<Date.now()-300000)fail();
  return {...r,serial_ids:serials} as Preview;
}
export function createAdapter(personId:string,requestNoReplay:Requester):Adapter {
  const person=id(personId),loss=createLossAdapter(person,requestNoReplay);
  // This is only a reference to a source already selected in this page. It is
  // never persisted as authority. Every new write rereads the server source.
  const operations=new Map<string,string>();
  async function context(stage:Kind):Promise<Context>{
    if(stage!=='original')fail();return {...await loss.context(),kind:stage};
  }
  async function stable(before:Context){if(canonical(before)!==canonical(await context('original')))fail();}
  async function checked(){const c=await context('original');if(!c.can_read||!c.can_write)fail();return c;}
  async function plan(c:ScrapCommand,s:Sources,d:ApprovedLine){
    const {request_id:_request,idempotency_key:_key,expected_plan_hash:_plan,...body}=c;
    return preview(await requestNoReplay(base+'/preview',{method:'POST',cache:'no-store',headers,body:JSON.stringify(body)}),s,d);
  }
  async function post(value:Pending,suffix:string){
    const p=await verifyPending(value),c=original(p);if(p.person_id!==person)fail();
    return requestNoReplay(base+suffix,{method:'POST',cache:'no-store',headers:{...headers,
      'X-Request-ID':c.request_id,'Idempotency-Key':c.idempotency_key},
      body:JSON.stringify(suffix?{operator_person_id:p.person_id,original:c}:c)});
  }
  return {
    context,list:loss.list,read:loss.read,
    async prepare(operation,identifier,reason,evidence){
      const before=await checked(),s=await loss.read(id(operation)),d=decision(s,identifier);
      if(s.authorization_version!==before.authorization_version)fail();
      const c=command('original',{source:{kind:'original',...d.preview_reference},execution_reason:reason,
        evidence_file_ids:[...evidence],expected_plan_hash:'0'.repeat(64),request_id:crypto.randomUUID(),
        idempotency_key:crypto.randomUUID()}) as ScrapCommand;
      const p=await plan(c,s,d);await stable(before);
      const pending=await prepare(person,before.authorization_version,'original',{...c,expected_plan_hash:p.plan_hash});
      operations.set(d.headquarters_decision_id,s.report.operation_id);
      return {pending,preview:p,source:s,decision:d};
    },
    async verifySource(value,current){
      const p=await verifyPending(value),c=original(p);
      if(c.source.kind!=='original'||p.person_id!==person||current.person_id!==person||current.kind!=='original')fail();
      const operation=operations.get(c.source.headquarters_decision_id);if(!operation)fail();
      const s=await loss.read(operation),d=decision(s,c.source.headquarters_decision_id);
      if(s.authorization_version!==current.authorization_version
          ||canonical({kind:'original',...d.preview_reference})!==canonical(c.source))fail();
      const fresh=await plan(c,s,d);if(fresh.plan_hash!==c.expected_plan_hash)fail();
    },
    submit:p=>post(p,''),lookup:p=>post(p,'/request-lookup'),seal:p=>post(p,'/request-seal'),
  };
}
