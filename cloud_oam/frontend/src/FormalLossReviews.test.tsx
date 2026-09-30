// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import FormalLossReviews from './FormalLossReviews';
import { createStore } from './lossReviewRecovery';
import { prepare, type Pending, type Report } from './formalLossReview';
import type { Adapter } from './lossReviewAdapter';
const id=(n:number)=>`10000000-0000-4000-8000-${n.toString().padStart(12,'0')}`;
const identity={person_id:id(1),authorization_version:3};
const doc:Report={availability:'available',operation_id:id(2),operation_no:'LOSS-001',owner_org_id:id(3),owner_org_name:'区域',requester_person_id:id(4),requester_name:'申请人',source_location_id:id(5),source_location_name:'个人仓',submitted_at:'2026-09-30T01:00:00Z',reason:'损坏待核实',submission_plan_hash:'a'.repeat(64),approval_stage:'awaiting_regional',approval_stock_effect:'none',lines:[{line_id:id(6),material_id:id(7),sku_code:'M1',material_name:'备件',base_unit:'件',condition_code:'new',lot_id:null,lot_no:null,quantity:'1.000',serials:[]}],evidence:[{file_id:id(8),original_filename:'照片.jpg',sha256:'b'.repeat(64),size_bytes:128,mime_type:'image/jpeg'}],regional_review:null,headquarters_review:null};
beforeEach(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});afterEach(()=>{cleanup();vi.unstubAllGlobals();localStorage.clear();});
function setup(){
 const store=createStore(localStorage,{async request(_n,_o,callback){return callback({});}});
 const adapter:Adapter={context:vi.fn(async()=>({...identity,stage:'regional' as const,authority_hash:'c'.repeat(64),can_read:true,can_write:true})),list:vi.fn(async()=>({schema_version:'1.0' as const,...identity,stage:'regional' as const,view:'pending' as const,queried_at:'2026-09-30T01:00:00Z',items:[doc],next_after_id:null})),read:vi.fn(async()=>doc),detail:vi.fn(async()=>doc),download:vi.fn(async()=>({url:'https://private.example/signed',expires_at:new Date(Date.now()+60000).toISOString()})),submit:vi.fn(async()=>{throw new Error('网络结果未知');}),lookup:vi.fn(async()=>({lookup_status:'not_found',retry_permitted:false})),seal:vi.fn(async()=>{throw new Error('封存结果未知');})};
 return {store,adapter};
}
it('shows exact evidence and preserves one unknown command across re-entry without replay',async()=>{
 const w=setup(),page=render(<FormalLossReviews identity={identity} stages={['regional']} adapter={w.adapter} store={w.store}/>);
 fireEvent.click(await screen.findByText('查看详情'));await screen.findByText('照片.jpg');
 fireEvent.change(screen.getByLabelText('审批意见'),{target:{value:'已核实照片'}});
 fireEvent.click(screen.getByLabelText('已核对原单、明细和证据，确认提交以上意见'));
 fireEvent.click(screen.getByText('确认提交区域核实'));
 await screen.findByText('网络结果未知');expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.store.list(identity.person_id)).toHaveLength(1);
 page.unmount();render(<FormalLossReviews identity={identity} stages={['regional']} adapter={w.adapter} store={w.store}/>);
 fireEvent.click(await screen.findByText('回查原请求'));await screen.findByText('尚未查到原请求结果。已保留原请求，不能据此再次提交。');
 expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.store.list(identity.person_id)).toHaveLength(1);
});
it('requires explicit confirmation before sealing and retains unknown seal outcomes',async()=>{
 const w=setup(),p=await prepare(identity,'regional',doc,'已核实');await w.store.withLease(p.person_id,'regional',doc.operation_id,async lease=>lease.persist(p));
 render(<FormalLossReviews identity={identity} stages={['regional']} adapter={w.adapter} store={w.store}/>);
 const button=await screen.findByText('永久封存原请求');await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
 expect(w.adapter.seal).not.toHaveBeenCalled();fireEvent.click(screen.getByText('确认永久封存'));await screen.findByText('封存结果未知');
 expect(w.adapter.seal).toHaveBeenCalledExactlyOnceWith(p);expect(w.store.list(identity.person_id)).toEqual([p]);
});
it('shows evidence only after a fresh link is requested and removes it on another detail load',async()=>{
 const w=setup();render(<FormalLossReviews identity={identity} stages={['regional']} adapter={w.adapter} store={w.store}/>);
 fireEvent.click(await screen.findByText('查看详情'));fireEvent.click(await screen.findByText('照片.jpg'));
 const link=await screen.findByRole('link',{name:'打开 照片.jpg'});expect(link.getAttribute('rel')).toBe('noopener noreferrer');expect(link.getAttribute('referrerpolicy')).toBe('no-referrer');
 fireEvent.click(screen.getByText('查看详情'));await waitFor(()=>expect(screen.queryByRole('link',{name:'打开 照片.jpg'})).toBeNull());
});

it('headquarters requires an explicit per-line decision and reports approval without disposal',async()=>{
 const w=setup();let value:Report={...doc,approval_stage:'awaiting_headquarters',regional_review:{review_id:id(20),reviewer_person_id:id(21),request_hash:'d'.repeat(64),comment:'已核实',reviewed_at:'2026-09-30T01:01:00Z'}};
 let result:unknown;
 w.adapter.context=vi.fn(async()=>({...identity,stage:'headquarters' as const,authority_hash:'c'.repeat(64),can_read:true,can_write:true}));
 w.adapter.list=vi.fn(async()=>({schema_version:'1.0' as const,...identity,stage:'headquarters' as const,view:'pending' as const,queried_at:'2026-09-30T01:00:00Z',items:value.approval_stage==='approved'?[]:[value],next_after_id:null}));
 w.adapter.read=vi.fn(async()=>value);w.adapter.detail=vi.fn(async()=>value);
 w.adapter.submit=vi.fn(async(p:Pending)=>{const o=p.command.original;expect(o.decisions).toEqual([{line_id:id(6),disposition:'return_to_region',reason:'退回检测'}]);
 const review={review_id:id(22),operation_id:doc.operation_id,owner_org_id:doc.owner_org_id,reviewer_person_id:identity.person_id,regional_review_id:id(20),regional_review_hash:'d'.repeat(64),approval_stage:'approved',stock_effect:'none',disposition_stage:'pending',decision:'approved',comment:o.comment,request_id:o.request_id,request_hash:p.command.request_hash,submission_plan_hash:doc.submission_plan_hash,decisions:o.decisions,reviewed_at:'2026-09-30T01:02:00Z'};
 result={lookup_status:'found',retry_permitted:false,review};value={...value,approval_stage:'approved',headquarters_review:{review_id:review.review_id,reviewer_person_id:review.reviewer_person_id,request_hash:review.request_hash,comment:review.comment,reviewed_at:review.reviewed_at,regional_review_id:review.regional_review_id,regional_review_hash:review.regional_review_hash,decisions:o.decisions!}};return review;});
 w.adapter.lookup=vi.fn(async()=>result);
 render(<FormalLossReviews identity={identity} stages={['headquarters']} adapter={w.adapter} store={w.store}/>);
 fireEvent.click(await screen.findByText('查看详情'));await screen.findByLabelText('处置决定 M1');
 expect((screen.getByLabelText('处置决定 M1') as HTMLSelectElement).value).toBe('');
 fireEvent.change(screen.getByLabelText('审批意见'),{target:{value:'同意退回'}});fireEvent.change(screen.getByLabelText('处置决定 M1'),{target:{value:'return_to_region'}});fireEvent.change(screen.getByLabelText('逐行理由 M1'),{target:{value:'退回检测'}});
 fireEvent.click(screen.getByLabelText('已核对原单、明细和证据，确认提交以上意见'));fireEvent.click(screen.getByText('确认提交总部终审'));
 await screen.findByText('总部终审已记录，仍需执行后续处置。');expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.store.list(identity.person_id)).toEqual([]);
});
