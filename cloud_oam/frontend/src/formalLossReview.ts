/** Loss approvals are independent facts; an approval never claims stock posting. */
export type Stage = 'regional' | 'headquarters';
export type Disposition = 'restore_available' | 'convert_used' | 'convert_damaged' | 'return_to_region' | 'scrap';
export type Decision = { line_id: string; disposition: Disposition; reason: string };
export type ReviewFact = { review_id: string; reviewer_person_id: string; request_hash: string; comment: string; reviewed_at: string };
export type HeadquartersFact = ReviewFact & { regional_review_id: string; regional_review_hash: string; decisions: Decision[] };
export type LossLine = { line_id: string; material_id: string; sku_code: string; material_name: string; base_unit: string;
  condition_code: 'new' | 'used' | 'damaged'; lot_id: string | null; lot_no: string | null; quantity: string;
  serials: { serial_id: string; serial_no: string }[] };
export type Evidence = { file_id: string; original_filename: string; sha256: string; size_bytes: number; mime_type: string };
export type Report = { availability: 'available'; operation_id: string; operation_no: string; owner_org_id: string; owner_org_name: string;
  requester_person_id: string; requester_name: string; source_location_id: string; source_location_name: string;
  submitted_at: string; reason: string; submission_plan_hash: string; approval_stage: 'awaiting_regional' | 'awaiting_headquarters' | 'approved';
  approval_stock_effect: 'none'; lines: LossLine[]; evidence: Evidence[]; regional_review: ReviewFact | null; headquarters_review: HeadquartersFact | null };
export type Blocked = { availability: 'blocked'; operation_id: string; reason_code: 'stock_loss_review_evidence_unavailable' };
export type Identity = { person_id: string; authorization_version: number };
export type Queue = Identity & { schema_version: '1.0'; stage: Stage; view: 'pending' | 'all'; queried_at: string;
  items: (Report | Blocked)[]; next_after_id: string | null };
export type Command = { stage: Stage; operator_person_id: string; request_hash: string; original: {
  operation_id: string; expected_submission_plan_hash: string; comment: string; request_id: string; idempotency_key: string;
  regional_review_id?: string; expected_regional_review_hash?: string; decisions?: Decision[] } };
export type Pending = { v: 1; person_id: string; authorization_version: number; owner_org_id: string; command: Command };
export type Resolution = { status: 'pending' | 'found' | 'sealed' };
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[a-f0-9]{64}$/;
const PY_SPACE = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/u;
export function fail(message = '报损审批记录无效，已停止操作'): never { throw new Error(message); }
export function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const r = value as Record<string, unknown>;
  if (Object.keys(r).length !== keys.length || keys.some(k => !Object.hasOwn(r,k))) fail();
  return r;
}
export function id(value: unknown): string { if (typeof value !== 'string' || !UUID.test(value)) fail(); return value; }
function digest(value: unknown): string { if (typeof value !== 'string' || !HASH.test(value)) fail(); return value; }
function positive(value: unknown): number { if (!Number.isSafeInteger(value) || Number(value) < 1) fail(); return value as number; }
function string(value: unknown, max=1000): string {
  if (typeof value !== 'string' || [...value].length < 1 || [...value].length > max || [...value].some(c => {
    const n=c.codePointAt(0)!; return n===0 || (n>=0xd800 && n<=0xdfff);
  })) fail(); return value;
}
function timestamp(value: unknown): string {
  const s=string(value,64);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(s) || !Number.isFinite(Date.parse(s))) fail();
  return s;
}
function list<T>(value: unknown, parse: (item: unknown)=>T, max=100, min=0): T[] {
  if (!Array.isArray(value) || value.length>max || value.length<min) fail(); return value.map(parse);
}
function unique(values: readonly string[]) { if (new Set(values).size!==values.length) fail(); }
function text(value: unknown, max: number, reason=false): string {
  const s=string(value,max); if (PY_SPACE.test(s) || (reason && /[\x01-\x08\x0b-\x1f]/.test(s))) fail('请填写明确意见，且不带首尾空白'); return s;
}
export function decision(value: unknown): Decision {
  const r=object(value,['line_id','disposition','reason']);
  if (!['restore_available','convert_used','convert_damaged','return_to_region','scrap'].includes(String(r.disposition))) fail();
  return { line_id:id(r.line_id),disposition:r.disposition as Disposition,reason:text(r.reason,500,true) };
}
const FACT=['review_id','reviewer_person_id','request_hash','comment','reviewed_at'];
function fact(value: unknown, hq=false): ReviewFact | HeadquartersFact {
  const r=object(value,hq?[...FACT,'regional_review_id','regional_review_hash','decisions']:FACT);
  const result: ReviewFact={review_id:id(r.review_id),reviewer_person_id:id(r.reviewer_person_id),request_hash:digest(r.request_hash),comment:text(r.comment,1000),reviewed_at:timestamp(r.reviewed_at)};
  if (!hq) return result;
  const decisions=list(r.decisions,decision,100,1);unique(decisions.map(d=>d.line_id));
  return {...result,regional_review_id:id(r.regional_review_id),regional_review_hash:digest(r.regional_review_hash),decisions};
}
export function report(value: unknown): Report {
  const r=object(value,['availability','operation_id','operation_no','owner_org_id','owner_org_name','requester_person_id','requester_name','source_location_id','source_location_name','submitted_at','reason','submission_plan_hash','approval_stage','approval_stock_effect','lines','evidence','regional_review','headquarters_review']);
  if (r.availability!=='available' || r.approval_stock_effect!=='none') fail();
  const lines=list(r.lines,value=>{
    const x=object(value,['line_id','material_id','sku_code','material_name','base_unit','condition_code','lot_id','lot_no','quantity','serials']);
    if (!['new','used','damaged'].includes(String(x.condition_code)) || typeof x.quantity!=='string' || !/^\d{1,15}\.\d{3}$/.test(x.quantity) || !/[1-9]/.test(x.quantity)) fail();
    const serials=list(x.serials,v=>{const s=object(v,['serial_id','serial_no']);return {serial_id:id(s.serial_id),serial_no:string(s.serial_no)};},10000);
    unique(serials.map(s=>s.serial_id));
    if (serials.length && (x.quantity!==`${serials.length}.000`)) fail();
    return {line_id:id(x.line_id),material_id:id(x.material_id),sku_code:string(x.sku_code),material_name:string(x.material_name),base_unit:string(x.base_unit),condition_code:x.condition_code as LossLine['condition_code'],lot_id:x.lot_id===null?null:id(x.lot_id),lot_no:x.lot_no===null?null:string(x.lot_no),quantity:x.quantity,serials};
  },100,1); unique(lines.map(l=>l.line_id));unique(lines.flatMap(l=>l.serials.map(s=>s.serial_id)));
  const evidence=list(r.evidence,value=>{const e=object(value,['file_id','original_filename','sha256','size_bytes','mime_type']);return {file_id:id(e.file_id),original_filename:string(e.original_filename),sha256:digest(e.sha256),size_bytes:positive(e.size_bytes),mime_type:string(e.mime_type)};},20,1);unique(evidence.map(e=>e.file_id));
  const regional=r.regional_review===null?null:fact(r.regional_review) as ReviewFact;
  const hq=r.headquarters_review===null?null:fact(r.headquarters_review,true) as HeadquartersFact;
  if (r.approval_stage!==(hq?'approved':regional?'awaiting_headquarters':'awaiting_regional')) fail();
  if (hq && (!regional || hq.regional_review_id!==regional.review_id || hq.regional_review_hash!==regional.request_hash || canonical(hq.decisions.map(d=>d.line_id).sort())!==canonical(lines.map(l=>l.line_id).sort()))) fail();
  const submitted=timestamp(r.submitted_at);
  if(regional && Date.parse(regional.reviewed_at)<Date.parse(submitted) || hq && regional && Date.parse(hq.reviewed_at)<Date.parse(regional.reviewed_at))fail();
  const requester=id(r.requester_person_id);
  if ([regional,hq].some(f=>f?.reviewer_person_id===requester)) fail();
  return {...r,operation_id:id(r.operation_id),operation_no:string(r.operation_no),owner_org_id:id(r.owner_org_id),owner_org_name:string(r.owner_org_name),requester_person_id:requester,requester_name:string(r.requester_name),source_location_id:id(r.source_location_id),source_location_name:string(r.source_location_name),submitted_at:timestamp(r.submitted_at),reason:string(r.reason),submission_plan_hash:digest(r.submission_plan_hash),lines,evidence,regional_review:regional,headquarters_review:hq} as Report;
}
export function identity(value: unknown): Identity {
  const r=value as Partial<Identity>;if (!r || typeof r!=='object') fail();return {person_id:id(r.person_id),authorization_version:positive(r.authorization_version)};
}
export function stage(value: unknown): Stage { if (value!=='regional' && value!=='headquarters') fail();return value; }
function basis(r: Record<string,unknown>, expected: Identity, expectedStage: Stage) {
  const got=identity(r); if (r.schema_version!=='1.0' || canonical(got)!==canonical(identity(expected)) || stage(r.stage)!==expectedStage) fail('当前身份或审批范围已变化，请重新进入');timestamp(r.queried_at);
}
export function queue(value: unknown, expected: Identity, expectedStage: Stage, view: 'pending'|'all'): Queue {
  const r=object(value,['schema_version','person_id','authorization_version','stage','view','queried_at','items','next_after_id']);basis(r,expected,expectedStage);if(r.view!==view)fail();
  const items=list(r.items,v=>{if((v as Blocked)?.availability!=='blocked')return report(v);const b=object(v,['availability','operation_id','reason_code']);if(b.reason_code!=='stock_loss_review_evidence_unavailable')fail();return {availability:'blocked',operation_id:id(b.operation_id),reason_code:b.reason_code} as Blocked;},20);
  if(items.some(r=>r.availability==='available' && (r.requester_person_id===expected.person_id || view==='pending' && r.approval_stage!==(expectedStage==='regional'?'awaiting_regional':'awaiting_headquarters'))))fail();
  unique(items.map(r=>r.operation_id));const next=r.next_after_id===null?null:id(r.next_after_id);
  return {...r,items,next_after_id:next} as Queue;
}
export function detail(value: unknown, expected: Identity, expectedStage: Stage, operation: string): Report {
  const r=object(value,['schema_version','person_id','authorization_version','stage','queried_at','report']);basis(r,expected,expectedStage);const got=report(r.report);if(got.operation_id!==id(operation))fail();return got;
}
export function canonical(value: unknown): string {
  function ordered(v: unknown): unknown { if(Array.isArray(v))return v.map(ordered);if(v && typeof v==='object')return Object.fromEntries(Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([k,x])=>[k,ordered(x)]));return v; }
  return JSON.stringify(ordered(value));
}
export async function hash(value: unknown): Promise<string> {
  const bytes=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)));
  return [...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
}
export function command(value: unknown): Command {
  const r=object(value,['stage','operator_person_id','request_hash','original']);const s=stage(r.stage);
  const keys=['operation_id','expected_submission_plan_hash','comment','request_id','idempotency_key'];
  const o=object(r.original,s==='headquarters'?[...keys,'regional_review_id','expected_regional_review_hash','decisions']:keys);
  const request=string(o.request_id,160),key=string(o.idempotency_key,200);
  if(!/^[A-Za-z0-9._:-]{8,160}$/.test(request)||!/^[A-Za-z0-9._:-]{8,200}$/.test(key))fail();
  const original: Command['original']={operation_id:id(o.operation_id),expected_submission_plan_hash:digest(o.expected_submission_plan_hash),comment:text(o.comment,1000),request_id:request,idempotency_key:key};
  if(s==='headquarters'){const decisions=list(o.decisions,decision,100,1);unique(decisions.map(d=>d.line_id));if(canonical(decisions)!==canonical([...decisions].sort((a,b)=>a.line_id<b.line_id?-1:1)))fail();Object.assign(original,{regional_review_id:id(o.regional_review_id),expected_regional_review_hash:digest(o.expected_regional_review_hash),decisions});}
  return {stage:s,operator_person_id:id(r.operator_person_id),request_hash:digest(r.request_hash),original};
}
export async function verifyCommand(value: unknown): Promise<Command> {
  const c=command(value);const {request_id: _request,idempotency_key: _key,...intent}=c.original;
  if(await hash(intent)!==c.request_hash)fail('原审批内容摘要不一致，保留记录并停止写入');return c;
}
export function pending(value: unknown): Pending {
  const r=object(value,['v','person_id','authorization_version','owner_org_id','command']);if(r.v!==1)fail();
  const i=identity(r),c=command(r.command);if(c.operator_person_id!==i.person_id)fail();return {v:1,...i,owner_org_id:id(r.owner_org_id),command:c};
}
export async function prepare(expected: Identity, s: Stage, current: Report, comment: string, decisions?: Decision[]): Promise<Pending> {
  const checked=report(current),i=identity(expected);if(checked.requester_person_id===i.person_id)fail();
  const original: Command['original']={operation_id:checked.operation_id,expected_submission_plan_hash:checked.submission_plan_hash,comment,
    request_id:crypto.randomUUID(),idempotency_key:crypto.randomUUID()};
  if(s==='headquarters'){if(!checked.regional_review)fail();Object.assign(original,{regional_review_id:checked.regional_review.review_id,expected_regional_review_hash:checked.regional_review.request_hash,decisions:[...(decisions??[])].sort((a,b)=>a.line_id<b.line_id?-1:1)});}
  const {request_id:_r,idempotency_key:_k,...intent}=original;
  const c=await verifyCommand({stage:s,operator_person_id:i.person_id,original,request_hash:await hash(intent)});
  const result=pending({v:1,...i,owner_org_id:checked.owner_org_id,command:c});checkTarget(result,checked);return result;
}
export function checkTarget(p: Pending, r: Report) {
  const c=p.command,o=c.original;
  if(r.operation_id!==o.operation_id||r.owner_org_id!==p.owner_org_id||r.submission_plan_hash!==o.expected_submission_plan_hash||r.requester_person_id===p.person_id||r.approval_stage!==(c.stage==='regional'?'awaiting_regional':'awaiting_headquarters'))fail('原单或审批阶段已变化，请刷新后核对');
  if(c.stage==='headquarters'&&(!r.regional_review||r.regional_review.review_id!==o.regional_review_id||r.regional_review.request_hash!==o.expected_regional_review_hash||canonical(r.lines.map(x=>x.line_id).sort())!==canonical(o.decisions!.map(x=>x.line_id).sort())))fail();
}
export function resolution(value: unknown, p: Pending): Resolution {
  const x=value as Record<string,unknown>;if(!x || x.retry_permitted!==false)fail();
  if(x.lookup_status==='not_found'){object(x,['lookup_status','retry_permitted']);return {status:'pending'};}
  const c=p.command,o=c.original;
  if(x.lookup_status==='sealed'){
    object(x,['lookup_status','retry_permitted','seal']);const s=object(x.seal,['seal_id','stage','operation_id','owner_org_id','reviewer_person_id','request_id','request_hash','submission_plan_hash','stock_effect','sealed_at']);id(s.seal_id);timestamp(s.sealed_at);
    if(s.stage!==c.stage||s.operation_id!==o.operation_id||s.owner_org_id!==p.owner_org_id||s.reviewer_person_id!==p.person_id||s.request_id!==o.request_id||s.request_hash!==c.request_hash||s.submission_plan_hash!==o.expected_submission_plan_hash||s.stock_effect!=='none')fail();return {status:'sealed'};
  }
  if(x.lookup_status!=='found')fail();object(x,['lookup_status','retry_permitted','review']);
  const keys=[...FACT,'operation_id','owner_org_id','approval_stage','stock_effect','decision','request_id','submission_plan_hash'];
  const r=object(x.review,c.stage==='regional'?keys:[...keys,'regional_review_id','regional_review_hash','decisions','disposition_stage']);id(r.review_id);timestamp(r.reviewed_at);
  if(r.operation_id!==o.operation_id||r.owner_org_id!==p.owner_org_id||r.reviewer_person_id!==p.person_id||r.request_id!==o.request_id||r.request_hash!==c.request_hash||r.submission_plan_hash!==o.expected_submission_plan_hash||r.comment!==o.comment||r.stock_effect!=='none'||r.approval_stage!==(c.stage==='regional'?'awaiting_headquarters':'approved')||r.decision!==(c.stage==='regional'?'verified':'approved'))fail();
  if(c.stage==='headquarters'&&(r.disposition_stage!=='pending'||r.regional_review_id!==o.regional_review_id||r.regional_review_hash!==o.expected_regional_review_hash||canonical(list(r.decisions,decision,100,1))!==canonical(o.decisions)))fail();return {status:'found'};
}
