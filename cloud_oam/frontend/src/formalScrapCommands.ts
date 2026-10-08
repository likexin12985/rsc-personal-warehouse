/** Complete original commands retained across transport failures; never a retry token. */
import { canonical, hash } from './formalLossReview';
import { fail, type Kind } from './formalScrapFacts';

type Coordinates = { request_id: string; idempotency_key: string };
export type OriginalSource = { kind: 'original'; headquarters_decision_id: string;
  expected_headquarters_review_hash: string; expected_submission_plan_hash: string };
export type CorrectionSource = { kind: 'correction'; root_disposition_id: string;
  expected_root_request_hash: string; expected_submission_plan_hash: string; reversal_id: string;
  expected_reversal_hash: string; correction_decision_id: string; expected_correction_decision_hash: string };
export type RecoverySource = { scrap_line_id: string; expected_scrap_request_hash: string };
export type ScrapCommand = Coordinates & { source: OriginalSource | CorrectionSource;
  execution_reason: string; evidence_file_ids: string[]; expected_plan_hash: string };
type RecoveryBinding = Coordinates & { source: RecoverySource; reason: string };
export type ApplyCommand = RecoveryBinding & { action: 'apply_scrap_recovery'; evidence_file_ids: string[] };
export type RegionalCommand = RecoveryBinding & { action: 'review_scrap_recovery_region';
  recovery_request_id: string; expected_request_hash: string; decision: 'verified' | 'needs_evidence' };
export type HeadquartersCommand = RecoveryBinding & { action: 'review_scrap_recovery_headquarters';
  recovery_request_id: string; expected_request_hash: string; regional_review_id: string;
  expected_regional_hash: string; decision: 'approve' | 'request_regional_review' };
export type ExecuteCommand = RecoveryBinding & { action: 'execute_scrap_recovery';
  recovery_request_id: string; expected_request_hash: string; headquarters_review_id: string;
  expected_headquarters_hash: string; expected_plan_hash: string };
export type Command = ScrapCommand | ApplyCommand | RegionalCommand | HeadquartersCommand | ExecuteCommand;
export type Pending = { v: 1; person_id: string; authorization_version: number; kind: Kind;
  original: Command; request_hash: string };

export function object(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail();
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== fields.length || fields.some(k => !Object.hasOwn(result, k))) fail();
  return result;
}
export function id(value: unknown): string {
  if (typeof value !== 'string' || !/^(?!00000000-0000-0000-0000-000000000000$)[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value)) fail();
  return value;
}
export function digest(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9]{64}$/.test(value)) fail();
  return value;
}
export function kind(value: unknown): Kind {
  if (!['original','correction','apply','regional','headquarters','execute'].includes(String(value))) fail();
  return value as Kind;
}
function coordinate(value: unknown, max: number): string {
  if (typeof value !== 'string' || value.length > max || !/^[A-Za-z0-9._:-]{8,}$/.test(value)) fail();
  return value;
}
function reason(value: unknown): string {
  if (typeof value !== 'string' || !value || [...value].length > 500
      || /^[\s\u001c-\u001f\u0085]|[\s\u001c-\u001f\u0085]$/u.test(value)
      || [...value].some(c => { const n=c.codePointAt(0)!;
        return (n < 32 && n !== 9 && n !== 10) || n >= 0xd800 && n <= 0xdfff;
      })) fail();
  return value;
}
function evidence(value: unknown): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 20) fail();
  const files = value.map(id);
  if (new Set(files).size !== files.length) fail();
  // Do not reorder a saved command. The backend sorts a separate hash view.
  return files;
}
function source(k: Kind, value: unknown) {
  const fields = k==='original' ? ['kind','headquarters_decision_id','expected_headquarters_review_hash','expected_submission_plan_hash']
    : k==='correction' ? ['kind','root_disposition_id','expected_root_request_hash','expected_submission_plan_hash',
      'reversal_id','expected_reversal_hash','correction_decision_id','expected_correction_decision_hash']
    : ['scrap_line_id','expected_scrap_request_hash'];
  const result = object(value, fields);
  for (const [name, entry] of Object.entries(result)) {
    if (name==='kind') { if (entry!==k) fail(); }
    else if (name.endsWith('_id')) id(entry);
    else digest(entry);
  }
  return { ...result };
}

const ACTIONS = { apply:'apply_scrap_recovery', regional:'review_scrap_recovery_region',
  headquarters:'review_scrap_recovery_headquarters', execute:'execute_scrap_recovery' };
export function command(stage: Kind, value: unknown): Command {
  const k=kind(stage), isScrap=k==='original'||k==='correction';
  const fields=['request_id','idempotency_key','source', ...(isScrap
    ? ['execution_reason','evidence_file_ids','expected_plan_hash']
    : ['action','reason', ...(k==='apply' ? ['evidence_file_ids']
      : ['recovery_request_id','expected_request_hash', ...(k==='regional' ? ['decision']
        : k==='headquarters' ? ['decision','regional_review_id','expected_regional_hash']
        : ['headquarters_review_id','expected_headquarters_hash','expected_plan_hash'])])])];
  const r=object(value,fields);
  coordinate(r.request_id,160); coordinate(r.idempotency_key,200);
  const parsed: Record<string,unknown>={...r,source:source(k,r.source)};
  parsed[isScrap?'execution_reason':'reason']=reason(r[isScrap?'execution_reason':'reason']);
  if ('evidence_file_ids' in r) parsed.evidence_file_ids=evidence(r.evidence_file_ids);
  for (const name of ['recovery_request_id','regional_review_id','headquarters_review_id']) if (name in r) id(r[name]);
  for (const name of ['expected_request_hash','expected_regional_hash','expected_headquarters_hash','expected_plan_hash']) if (name in r) digest(r[name]);
  if (!isScrap && r.action!==ACTIONS[k]) fail();
  if (k==='regional' && !['verified','needs_evidence'].includes(String(r.decision))) fail();
  if (k==='headquarters' && !['approve','request_regional_review'].includes(String(r.decision))) fail();
  return parsed as Command;
}

export async function requestHash(k: Kind, value: Command): Promise<string> {
  const c=command(k,value);
  if ('execution_reason' in c) {
    return hash({schema_version:1,action:'scrap',intent:{source:c.source,execution_reason:c.execution_reason,
      evidence_file_ids:[...c.evidence_file_ids].sort()},request_id:c.request_id,expected_plan_hash:c.expected_plan_hash});
  }
  const { idempotency_key: _privateKey, ...intent }=c;
  return hash('evidence_file_ids' in intent ? {...intent,evidence_file_ids:[...intent.evidence_file_ids].sort()} : intent);
}
export function pending(value: unknown): Pending {
  const r=object(value,['v','person_id','authorization_version','kind','original','request_hash']);
  if (r.v!==1 || !Number.isSafeInteger(r.authorization_version) || Number(r.authorization_version)<1) fail();
  return {v:1,person_id:id(r.person_id),authorization_version:r.authorization_version as number,
    kind:kind(r.kind),original:command(kind(r.kind),r.original),request_hash:digest(r.request_hash)};
}
export async function verifyPending(value: unknown): Promise<Pending> {
  const p=pending(value);
  if (await requestHash(p.kind,p.original)!==p.request_hash) fail();
  return p;
}
export async function prepare(person: string, version: number, k: Kind, value: Command): Promise<Pending> {
  const original=command(k,value);
  return verifyPending({v:1,person_id:person,authorization_version:version,kind:k,original,request_hash:await requestHash(k,original)});
}
export function target(value: Pending): string {
  const p=pending(value),c=p.original;
  if ('execution_reason' in c) return c.source.kind==='original' ? c.source.headquarters_decision_id : c.source.correction_decision_id;
  if (c.action==='apply_scrap_recovery') return c.source.scrap_line_id;
  if (c.action==='review_scrap_recovery_region') return c.recovery_request_id;
  if (c.action==='review_scrap_recovery_headquarters') return c.regional_review_id;
  return c.headquarters_review_id;
}
export function same(a: Pending,b: Pending): boolean { return canonical(pending(a))===canonical(pending(b)); }
