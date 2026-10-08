// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import FormalScrapRecoveryPage from './FormalScrapRecoveryPage';
import { world } from './scrapRecoveryTestSupport';
import { createStore } from './formalScrapRecovery';
import { requestHash } from './formalScrapCommands';
import type { Stage } from './scrapRecoverySources';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
vi.mock('./FormalFileUploadField',()=>({default:(p:{onAvailableChange:(f:unknown[])=>void})=><button onClick={()=>p.onAvailableChange([{file_id:'10000000-0000-4000-8000-000000000001'}])}>选择测试附件</button>}));
beforeEach(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const stages:Stage[]=['apply','regional','headquarters','execute'];
async function fill(k:Stage){
  const choose=await screen.findByRole('button',{name:'查看 TEST-LOSS-001 · TEST-SKU'});await waitFor(()=>expect((choose as HTMLButtonElement).disabled).toBe(false));fireEvent.click(choose);
  const reason=await screen.findByRole('textbox',{name:'办理说明'});await waitFor(()=>expect((reason as HTMLTextAreaElement).disabled).toBe(false));fireEvent.change(reason,{target:{value:'找回实物已经核对'}});
  if(k==='apply')fireEvent.click(screen.getByRole('button',{name:'选择测试附件'}));
  if(k==='regional'||k==='headquarters')fireEvent.change(screen.getByRole('combobox',{name:'处理意见'}),{target:{value:k==='regional'?'verified':'approve'}});
  fireEvent.click(screen.getByRole('button',{name:k==='execute'?'预览找回入库':'核对本次办理'}));
  await screen.findByRole('region',{name:'确认找回办理'});const confirm=screen.getByRole('button',{name:/^确认(找回申请|区域核实|总部审批|找回入库)$/});
  expect((confirm as HTMLButtonElement).disabled).toBe(true);fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(confirm);
}
it.each(stages)('%s unknown write is retained across remount and read-only recovery never resends',async k=>{
  const w=world(k),store=createStore(new MemoryStorage(),locks()),props={identity:w.identity,adapter:w.adapter,store,stages:[k]};
  const mounted=render(<FormalScrapRecoveryPage {...props}/>);await fill(k);await screen.findByText('响应丢失');
  const saved=store.list(w.identity.person_id);expect(saved).toHaveLength(1);mounted.unmount();w.access.permissions.pop();
  const original=w.request.getMockImplementation()!;w.request.mockImplementation(async(path,init)=>path.endsWith('/request-lookup')?{request_id:saved[0].original.request_id,request_hash:saved[0].request_hash,retry_allowed:false,request_state:'not_found',result_scope:'unconfirmed_request',result:null}:original(path,init));w.request.mockClear();
  render(<FormalScrapRecoveryPage {...props}/>);const lookup=await screen.findByRole('button',{name:'回查原请求'});await waitFor(()=>expect((lookup as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByRole('button',{name:'永久封存原请求'}) as HTMLButtonElement).disabled).toBe(true);fireEvent.click(lookup);
  await screen.findByText(/尚未查到原请求结果/);expect(store.list(w.identity.person_id)).toEqual(saved);
  expect(w.request.mock.calls.filter(([,i])=>i?.method==='POST').map(([p])=>p)).toHaveLength(1);
  expect(w.request.mock.calls.find(([,i])=>i?.method==='POST')![0]).toMatch(/request-lookup$/);
});
it.each(stages)('%s confirms exact historical result, distinguishes approval from stock',async k=>{
  const w=world(k),store=createStore(new MemoryStorage(),locks()),original=w.request.getMockImplementation()!;
  let result:Record<string,unknown>|null=null;
  w.request.mockImplementation(async(path,init)=>{
    if(init?.method==='POST'&&!path.endsWith('/preview')){
      if(path.endsWith('/request-lookup'))return {request_id:result!.request_id,request_hash:result!.request_hash,retry_allowed:false,request_state:'found',result_scope:'historical_original_outcome',result};
      const c=JSON.parse(init.body as string),sample=k==='apply'?w.application:k==='regional'?w.regional:k==='headquarters'?w.hq:w.posted;
      result={...sample,reason:c.reason,actor_person_id:w.identity.person_id,request_id:c.request_id,request_hash:await requestHash(k,c),...(k==='execute'?{plan_hash:c.expected_plan_hash}:{})};return result;
    }return original(path,init);
  });render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={store} stages={[k]}/>);await fill(k);
  await screen.findByText(k==='execute'?/已恢复原冻结份额，尚未恢复可用/:/原审批记录已核验，未改变库存/);expect(store.list(w.identity.person_id)).toEqual([]);
  expect(w.request.mock.calls.filter(([p,i])=>i?.method==='POST'&&!p.endsWith('/preview')&&!p.endsWith('/request-lookup'))).toHaveLength(1);
});
it('broken local persistence prevents preparation and submission',async()=>{
  const w=world(),store=createStore(null,locks());render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={store} stages={['apply']}/>);
  await screen.findByText(/原请求存储不可用/);const choose=await screen.findByRole('button',{name:'查看 TEST-LOSS-001 · TEST-SKU'});await waitFor(()=>expect((choose as HTMLButtonElement).disabled).toBe(false));fireEvent.click(choose);
  const why=await screen.findByRole('textbox',{name:'办理说明'});expect((why.closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);expect(w.request.mock.calls.some(([,i])=>i?.method==='POST')).toBe(false);
});
it('no automatic review choice and no action for consumed reference',async()=>{
  const w=world('regional');w.source.next_reference=null;render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={createStore(new MemoryStorage(),locks())} stages={['regional']}/>);
  const choose=await screen.findByRole('button',{name:'查看 TEST-LOSS-001 · TEST-SKU'});await waitFor(()=>expect((choose as HTMLButtonElement).disabled).toBe(false));fireEvent.click(choose);await screen.findByText(/本阶段没有可办理操作/);expect(screen.queryByRole('combobox')).toBeNull();expect(w.request.mock.calls.some(([,i])=>i?.method==='POST')).toBe(false);
});
it('sealing is explicit, rereads first, and does not replay the original write',async()=>{
  const w=world('regional'),store=createStore(new MemoryStorage(),locks()),p=(await w.prepare()).pending;
  await store.withLease(p,async l=>l.persist(p));const original=w.request.getMockImplementation()!;let closed=false;
  const result=()=>({request_id:p.original.request_id,request_hash:p.request_hash,retry_allowed:false,...(closed?{request_state:'sealed',result_scope:'closed_original_request',result:null,seal:{seal_id:w.file,kind:'regional',loss_operation_id:w.source.operation_id,loss_line_id:w.source.line_id,root_disposition_id:w.source.root_disposition_id,sealed_at:new Date().toISOString(),stock_effect:'none'}}:{request_state:'not_found',result_scope:'unconfirmed_request',result:null})});
  w.request.mockImplementation(async(path,init)=>{if(path.endsWith('/request-seal')){closed=true;return result();}if(path.endsWith('/request-lookup'))return result();return original(path,init);});w.request.mockClear();
  render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={store} stages={['regional']}/>);
  const seal=await screen.findByRole('button',{name:'永久封存原请求'});await waitFor(()=>expect((seal as HTMLButtonElement).disabled).toBe(false));fireEvent.click(seal);
  await screen.findByRole('alertdialog');expect(w.request.mock.calls.some(([,i])=>i?.method==='POST')).toBe(false);fireEvent.click(screen.getByRole('button',{name:'取消封存'}));expect(closed).toBe(false);
  fireEvent.click(seal);fireEvent.click(screen.getByRole('button',{name:'确认永久封存'}));await screen.findByText(/原请求已永久封存/);expect(store.list(p.person_id)).toEqual([]);
  expect(w.request.mock.calls.filter(([,i])=>i?.method==='POST').map(([path])=>path.split('/').pop())).toEqual(['request-lookup','request-seal','request-lookup']);
});
it('requires an explicit review decision before preparing',async()=>{
  const w=world('headquarters');render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={createStore(new MemoryStorage(),locks())} stages={['headquarters']}/>);
  const choose=await screen.findByRole('button',{name:'查看 TEST-LOSS-001 · TEST-SKU'});await waitFor(()=>expect((choose as HTMLButtonElement).disabled).toBe(false));fireEvent.click(choose);
  const decision=await screen.findByRole('combobox',{name:'处理意见'});expect((decision as HTMLSelectElement).value).toBe('');fireEvent.change(screen.getByRole('textbox',{name:'办理说明'}),{target:{value:'检查证据'}});
  expect((screen.getByRole('button',{name:'核对本次办理'}) as HTMLButtonElement).disabled).toBe(true);
});
it('requires every physical serial and clears checks when a review decision changes',async()=>{
  const {serialWorld}=await import('./scrapRecoveryTestSupport');const w=serialWorld('regional');
  render(<FormalScrapRecoveryPage identity={w.identity} adapter={w.adapter} store={createStore(new MemoryStorage(),locks())} stages={['regional']}/>);
  const choose=await screen.findByRole('button',{name:'查看 TEST-LOSS-001 · TEST-SKU'});await waitFor(()=>expect((choose as HTMLButtonElement).disabled).toBe(false));fireEvent.click(choose);
  const decision=await screen.findByRole('combobox',{name:'处理意见'});await waitFor(()=>expect((decision as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(decision,{target:{value:'verified'}});fireEvent.change(screen.getByRole('textbox',{name:'办理说明'}),{target:{value:'核对实物'}});
  const prepare=screen.getByRole('button',{name:'核对本次办理'});expect((prepare as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox',{name:'实物物料号'}),{target:{value:'TEST-SKU'}});
  for(const serial of w.source.serials){fireEvent.change(screen.getByRole('textbox',{name:'实物标识'}),{target:{value:serial.serial_no}});fireEvent.click(screen.getByRole('button',{name:'核对这件物资'}));}
  expect((prepare as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(decision,{target:{value:'needs_evidence'}});expect((prepare as HTMLButtonElement).disabled).toBe(false);expect(screen.queryByRole('textbox',{name:'实物标识'})).toBeNull();
  fireEvent.change(decision,{target:{value:'verified'}});expect((prepare as HTMLButtonElement).disabled).toBe(true);expect(screen.getByText('已核对 0 / 2 件')).toBeTruthy();
});
