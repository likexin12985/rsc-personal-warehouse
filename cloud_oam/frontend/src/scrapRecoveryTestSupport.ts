/** Synthetic current-stage sources based on native public-fact sample shapes. */
import { vi } from 'vitest';
import { createAdapter } from './scrapRecoveryAdapter';
import { fact, type ReviewFact, type ScrapFact, type RecoveryFact } from './formalScrapFacts';
import type { Stage, Source, Reference, State } from './scrapRecoverySources';
import outcomes from './test-fixtures/stock-scrap/committed-quantity-results.json';
export function world(k:Stage='apply'){
  const scrap=fact('correction',outcomes.samples.find(s=>s.kind==='correction')!.fact) as ScrapFact;
  const f=(kind:'apply'|'regional'|'headquarters')=>fact(kind,outcomes.samples.find(s=>s.kind===kind&&'scrap_line_id' in s.fact&&s.fact.scrap_line_id===scrap.scrap_line_id)!.fact) as ReviewFact;
  const application=f('apply'),regional=f('regional'),hq=f('headquarters');
  const posted=fact('execute',outcomes.samples.find(s=>s.kind==='execute'&&'reversed_correction_id' in s.fact&&s.fact.reversed_correction_id===scrap.correction_execution_id)!.fact) as RecoveryFact;
  const person=k==='apply'?application.actor_person_id:k==='regional'?regional.actor_person_id:hq.actor_person_id;
  const identity={person_id:person,authorization_version:1},role=k==='apply'?'technician':k==='regional'?'provincial_manager':'admin';
  const scope=k==='apply'?'person':k==='regional'?'organization':'national';
  const common={...identity,account_status:'active',employment_status:'active',access_mode:'active',role_codes:[role]};
  const me={...common,name:'合成测试人员',employee_no:'TEST',organization_code:'TEST',organization_name:'合成测试组织'};
  const action={apply:'apply_scrap_recovery',regional:'review_scrap_recovery_regional',headquarters:'review_scrap_recovery_headquarters',execute:'execute_scrap_recovery'}[k];
  const owner=crypto.randomUUID(),file=crypto.randomUUID();
  const access={...common,assignments:[{assignment_id:crypto.randomUUID(),role_code:role,scope_type:scope,scope_id:k==='apply'?person:k==='regional'?owner:'*',valid_from:'2020-01-01T00:00:00Z',valid_to:null as string|null}],permissions:['read',action].map(action=>({resource:'stock_operation',action,field_code:''}))};
  const binding={scrap_line_id:scrap.scrap_line_id,expected_scrap_request_hash:scrap.request_hash};
  const ref={stage:k,source:binding,...(k==='apply'?{}:{recovery_request_id:application.fact_id,expected_request_hash:application.request_hash}),...(k==='headquarters'?{regional_review_id:regional.fact_id,expected_regional_hash:regional.request_hash}:k==='execute'?{headquarters_review_id:hq.fact_id,expected_headquarters_hash:hq.request_hash}:{})} as Reference;
  const state:State={apply:'awaiting_application' as const,regional:'awaiting_regional' as const,headquarters:'awaiting_headquarters' as const,execute:'approved_pending_execution' as const}[k];
  const source:Source={...identity,schema_version:'1.0',availability:'verified',result_scope:'verified_scrap_recovery_references',write_authorization_provided:false,stock_effect:'none',queried_at:new Date().toISOString(),observed_ledger_cursor:10,requested_stage:k,scrap_reference:binding,root_disposition_id:scrap.root_disposition_id,operation_id:posted.operation_id,operation_no:'TEST-LOSS-001',line_id:posted.line_id,owner_org_id:owner,requester_person_id:application.actor_person_id,requester_name:'合成申请人',material_id:crypto.randomUUID(),sku_code:'TEST-SKU',material_name:'测试备件',base_unit:'个',condition_code:'damaged',quantity:scrap.quantity,serial_ids:[],serials:[],is_current_scrap:true,state,next_reference:ref,applications:k==='apply'?[]:[{application,evidence_file_ids:[file],regional_reviews:k==='regional'?[]:[regional],headquarters_reviews:k==='execute'?[hq]:[],status:state}],recovery_posting:null};
  const preview={planning_status:'preview_only',stock_effect:'none',root_disposition_id:scrap.root_disposition_id,original_execution_id:scrap.correction_execution_id,original_transaction_id:scrap.posting_transaction_id,original_movement_id:scrap.posting_movement_id,source_account_id:null,target_account_id:scrap.source_account_id,target_condition:source.condition_code,quantity:source.quantity,serial_ids:[] as string[],plan_hash:'a'.repeat(64),checked_at:new Date().toISOString()};
  const request=vi.fn(async(path:string,_init?:RequestInit):Promise<unknown>=>{
    if(path==='/auth/me')return me;if(path==='/access/context')return access;
    if(path.includes('/sources?'))return {...identity,schema_version:'1.0',requested_stage:k,queried_at:new Date().toISOString(),items:[source],next_after_id:null};
    if(path.includes('/sources/'))return source;if(path.endsWith('/preview'))return preview;throw new Error('响应丢失');
  });
  const adapter=createAdapter(person,request),input={reason:'找回实物已经核对',evidence:[file],decision:k==='regional'?'verified':'approve'};
  const prepare=()=>adapter.prepare(k,source.scrap_reference.scrap_line_id,input);
  return {k,identity,me,access,source,preview,request,adapter,input,prepare,application,regional,hq,posted,file};
}

export function serialWorld(k:Stage='apply'){
  const w=world(k);w.source.serials=[1,2].map(n=>({serial_id:crypto.randomUUID(),serial_no:`REC-SN-${n}`,qr_code:`REC-QR-${n}`}));
  w.source.serial_ids=w.source.serials.map(s=>s.serial_id);w.source.quantity='2.000';w.preview.quantity='2.000';w.preview.serial_ids=[...w.source.serial_ids];return w;
}
