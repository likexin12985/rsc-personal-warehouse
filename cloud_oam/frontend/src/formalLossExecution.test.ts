import { expect, it } from 'vitest';
import { identity } from './formalLossReview';
import { checkTarget, pending, prepare, resolution, sources, verifyPending } from './lossExecutionContracts';
import { fixtures, fixture, saved } from './lossExecutionFixtures';

it('includes real registered-HTTP contracts for all four actions in quantity and SN modes',()=>{expect(fixtures).toHaveLength(8);});
it.each(fixtures)('validates complete original/source/preview/found/seal contracts: $name',async({data:f})=>{
  const s=sources(f.source,identity(f.source),f.source.report.operation_id),p=await verifyPending(await saved(f));
  checkTarget(p,s);expect(resolution(f.missing,p).status).toBe('pending');expect(resolution(f.found,p).status).toBe('found');
  expect(p.request_hash).toBe(f.found.disposition.request_hash);
  expect(resolution(f.sealed,await saved(f,true)).status).toBe('sealed');
  const after=sources(f.after,identity(f.after),f.after.report.operation_id);expect(after.decisions[0].original_posting).not.toBeNull();expect(()=>checkTarget(p,after)).toThrow();
  const made=await prepare(s,s.decisions[0],f.preview,p.flow==='return'?s.return_routes[0]:undefined);expect(made.command.request_id).not.toBe(p.command.request_id);await verifyPending(made);
});
it.each(['operation_id','line_id','headquarters_decision_id','executor_person_id','request_id','request_hash','plan_hash','quantity','source_account_id','target_account_id','status','disposition'])('does not clear a response with changed %s',async field=>{
  const result=structuredClone(fixture.found);result.disposition[field]='forged';
  // Validate against the exact backend request, not a newly generated key.
  const p=await saved();expect(()=>resolution(result,p)).toThrow();
});
it.each(['retry_permitted','result_scope','lookup_status'])('rejects changed recovery envelope %s',async field=>{const p=await saved();expect(()=>resolution({...fixture.missing as object,[field]:'changed'},p)).toThrow();});
it('rejects unknown public fields and a mismatched retained command hash',async()=>{const p=await saved();expect(()=>pending({...p,stock:'available'})).toThrow();await expect(verifyPending({...p,command:{...p.command,request_id:'changed-request'}})).rejects.toThrow();});
it.each(['line','reference','kind','identity','serial','route'])('rejects source/approval substitution: %s',fault=>{
  const f=fixtures.find(f=>f.name==='serial-return_to_region.json')!.data,s=structuredClone(f.source);
  if(fault==='line')s.decisions[0].line_id='10000000-0000-4000-8000-000000000001';
  if(fault==='reference')s.decisions[0].preview_reference.expected_headquarters_review_hash='a'.repeat(64);
  if(fault==='kind')s.decisions[0].disposition='scrap';
  if(fault==='identity')s.person_id='10000000-0000-4000-8000-000000000001';
  if(fault==='serial')s.report.lines[0].serials.push(s.report.lines[0].serials[0]);
  if(fault==='route')s.return_routes[0].source_location_id=s.return_routes[0].target_location_id;
  expect(()=>sources(s,identity(f.source),f.source.report.operation_id)).toThrow();
});
