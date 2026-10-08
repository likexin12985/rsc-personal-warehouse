import { expect, it } from 'vitest';
import { world } from './scrapRecoveryTestSupport';
import { createAdapter } from './scrapRecoveryAdapter';
import { source, queue, type Stage } from './scrapRecoverySources';
import { createStore, submit, recover } from './formalScrapRecovery';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
const stages:Stage[]=['apply','regional','headquarters','execute'];
it.each(stages)('discovers and prepares %s without business write',async k=>{
  const w=world(k);expect((await w.adapter.list(k)).items).toHaveLength(1);const p=await w.prepare();expect(p.pending.kind).toBe(k);expect(p.pending.original.source).toEqual(w.source.scrap_reference);
  expect(w.request.mock.calls.filter(([,i])=>i?.method==='POST').map(([p])=>p)).toEqual(k==='execute'?['/v1/stock-operations/loss-reports/scraps/recovery/executions/preview']:[]);expect(p.preview===null).toBe(k!=='execute');
});
it.each(stages)('saves before %s lost reply; remount with revoked write only reads saved original',async k=>{
  const w=world(k),p=(await w.prepare()).pending,store=createStore(new MemoryStorage(),locks());await expect(submit(w.adapter,store,p)).rejects.toThrow('响应丢失');expect(store.list(p.person_id)).toEqual([p]);
  w.access.permissions.pop();const original=w.request.getMockImplementation()!;
  w.request.mockImplementation(async(path,init)=>{
    if(path.endsWith('/request-lookup'))return {request_id:p.original.request_id,request_hash:p.request_hash,retry_allowed:false,request_state:'not_found',result_scope:'unconfirmed_request',result:null};
    if(path.includes('/sources')||path.endsWith('/preview'))throw new Error('must not reload source');return original(path,init);
  });w.request.mockClear();
  expect((await recover(createAdapter(p.person_id,w.request),store,p)).status).toBe('pending');expect(store.list(p.person_id)).toEqual([p]);
  const calls=w.request.mock.calls.filter(([,i])=>i?.method==='POST');expect(calls).toHaveLength(1);expect(calls[0][0]).toMatch(/request-lookup$/);expect(JSON.parse(calls[0][1]!.body as string)).toEqual({operator_person_id:p.person_id,original:p.original});
});
it.each(['wrong-person','no-read','no-write','expired','future','wrong-scope','wrong-role','changed-version'])('blocks new command on %s',async fault=>{
  const w=world('apply');if(fault==='wrong-person')w.me.person_id=crypto.randomUUID();if(fault==='no-read')w.access.permissions.shift();if(fault==='no-write')w.access.permissions.pop();
  if(fault==='expired')w.access.assignments[0].valid_to='2021-01-01T00:00:00Z';if(fault==='future')w.access.assignments[0].valid_from='2099-01-01T00:00:00Z';if(fault==='wrong-scope')w.access.assignments[0].scope_id=crypto.randomUUID();if(fault==='wrong-role')w.access.assignments[0].role_code='star_headquarters_approver';
  if(fault==='changed-version'){const original=w.request.getMockImplementation()!;w.request.mockImplementation(async(path,init)=>{const result=await original(path,init);if(path.includes('/sources/')){w.me.authorization_version++;w.access.authorization_version++;}return result;});}
  await expect(w.prepare()).rejects.toThrow();
});
it.each(['quantity','root_disposition_id','target_condition','serial_ids','stock_effect','source_account_id','checked_at'])('rejects mismatched execution preview %s',async key=>{const w=world('execute');Object.assign(w.preview,{[key]:key==='serial_ids'?[crypto.randomUUID()]:'invalid'});await expect(w.prepare()).rejects.toThrow();});
it('checks live reference before persisting',async()=>{const w=world('regional'),p=(await w.prepare()).pending,store=createStore(new MemoryStorage(),locks());w.source.next_reference=null;await expect(submit(w.adapter,store,p)).rejects.toThrow();expect(store.list(p.person_id)).toEqual([]);expect(w.request.mock.calls.some(([,i])=>i?.method==='POST')).toBe(false);});
it.each(['foreign-scrap','foreign-request','foreign-review','self-review','false-stock','recovered-reference','duplicate-sn','private-field','stale','foreign-person'])('rejects malformed source %s',fault=>{
  const w=world('execute'),s=w.source;if(fault==='foreign-scrap')s.applications[0].application.scrap_line_id=crypto.randomUUID();if(fault==='foreign-request')s.applications[0].regional_reviews[0].recovery_request_id=crypto.randomUUID();if(fault==='foreign-review')s.applications[0].headquarters_reviews[0].regional_review_id=crypto.randomUUID();if(fault==='self-review')s.applications[0].regional_reviews[0].actor_person_id=s.requester_person_id;
  if(fault==='false-stock')Object.assign(s,{stock_effect:'posted'});if(fault==='recovered-reference'){s.recovery_posting=w.posted;s.state='recovered';s.is_current_scrap=false;}if(fault==='duplicate-sn')s.serial_ids=[w.file,w.file];if(fault==='private-field')Object.assign(s,{command_jsonb:{}});if(fault==='stale')s.queried_at='2000-01-01T00:00:00Z';if(fault==='foreign-person')s.person_id=crypto.randomUUID();expect(()=>source(s,w.identity,'execute')).toThrow();
});
it('keeps blocked rows visible and rejects non-progressing cursor',()=>{const w=world(),q={...w.identity,schema_version:'1.0',requested_stage:'apply',queried_at:new Date().toISOString(),items:[{availability:'blocked',scrap_line_id:w.file}],next_after_id:null};expect(queue(q,w.identity,'apply').items[0].availability).toBe('blocked');expect(()=>queue(q,w.identity,'apply',w.file)).toThrow();});
it('downloads only bound evidence and validates HTTPS',async()=>{
  const w=world('regional');await expect(w.adapter.download('regional',w.source.scrap_reference.scrap_line_id,crypto.randomUUID())).rejects.toThrow();const original=w.request.getMockImplementation()!;w.request.mockImplementation(async(path,init)=>path.endsWith('/download-intent')?{file_id:w.file,purpose:'stock_loss_evidence',download:{url:'http://unsafe.test',expires_at:new Date(Date.now()+60000).toISOString()}}:original(path,init));await expect(w.adapter.download('regional',w.source.scrap_reference.scrap_line_id,w.file)).rejects.toThrow();
});
it.each(['apply','regional','execute'] as Stage[])('requires complete physical serial checks for %s before preparation',async k=>{
  const {serialWorld}=await import('./scrapRecoveryTestSupport');const w=serialWorld(k),scrap=w.source.scrap_reference.scrap_line_id;
  await expect(w.adapter.prepare(k,scrap,w.input)).rejects.toThrow();
  await expect(w.adapter.prepare(k,scrap,{...w.input,verifiedSerials:w.source.serials.slice(0,1)})).rejects.toThrow();
  const p=await w.adapter.prepare(k,scrap,{...w.input,verifiedSerials:structuredClone(w.source.serials)});
  w.source.serials[0].qr_code='CHANGED-QR';const store=createStore(new MemoryStorage(),locks());
  await expect(submit(w.adapter,store,p.pending)).rejects.toThrow();expect(store.list(w.identity.person_id)).toEqual([]);
});
it('allows requesting evidence without pretending the missing serials were checked',async()=>{
  const {serialWorld}=await import('./scrapRecoveryTestSupport');const w=serialWorld('regional');
  const result=await w.adapter.prepare('regional',w.source.scrap_reference.scrap_line_id,{reason:'实物标识不齐，请补证据',decision:'needs_evidence'});
  expect(result.pending.original).toMatchObject({decision:'needs_evidence'});expect(result.preview).toBeNull();
});
it.each(['missing','foreign-id','duplicate-number','duplicate-qr'])('rejects incomplete or ambiguous serial labels: %s',async fault=>{
  const {serialWorld}=await import('./scrapRecoveryTestSupport');const w=serialWorld();
  if(fault==='missing')w.source.serials.pop();if(fault==='foreign-id')w.source.serials[0].serial_id=crypto.randomUUID();
  if(fault==='duplicate-number')w.source.serials[1].serial_no=w.source.serials[0].serial_no;
  if(fault==='duplicate-qr')w.source.serials[1].qr_code=w.source.serials[0].qr_code;
  expect(()=>source(w.source,w.identity,'apply')).toThrow();
});
