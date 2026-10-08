// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import FormalOriginalScrap from './FormalOriginalScrap';
import { createStore } from './formalScrapRecovery';
import { prepare, type ScrapCommand } from './formalScrapCommands';
import type { Adapter, Prepared, Preview } from './scrapOriginalAdapter';
import { fixture } from './lossExecutionFixtures';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
import outcomes from './test-fixtures/stock-scrap/committed-quantity-results.json';
vi.mock('./FormalFileUploadField',()=>({default:(p:{onAvailableChange:(f:unknown[])=>void})=><button onClick={()=>p.onAvailableChange([{file_id:'10000000-0000-4000-8000-000000000001'}])}>选择测试附件</button>}));
beforeEach(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
async function world(){
  const source=structuredClone(fixture.source);source.decisions[0].disposition='scrap';source.report.headquarters_review!.decisions[0].disposition='scrap';
  const d=source.decisions[0],line=source.report.lines[0],identity={person_id:source.person_id,authorization_version:source.authorization_version};
  const c:ScrapCommand={source:{kind:'original',...d.preview_reference},execution_reason:'本次报废已核验',evidence_file_ids:[crypto.randomUUID()],expected_plan_hash:'a'.repeat(64),request_id:crypto.randomUUID(),idempotency_key:crypto.randomUUID()};
  const pending=await prepare(identity.person_id,identity.authorization_version,'original',c),store=createStore(new MemoryStorage(),locks());
  const preview:Preview={planning_status:'preview_only',stock_effect:'none',operation_id:source.report.operation_id,line_id:d.line_id,
    decision_id:d.headquarters_decision_id,predecessor_reversal_id:null,source_account_id:crypto.randomUUID(),target_account_id:null,
    source_condition:line.condition_code,quantity:line.quantity,serial_ids:[],plan_hash:c.expected_plan_hash,checked_at:new Date().toISOString()};
  const prepared:Prepared={pending,preview,source,decision:d};
  const context={...identity,kind:'original' as const,authority_hash:'b'.repeat(64),can_read:true,can_write:true};
  const result={...outcomes.samples.find(s=>s.kind==='original')!.fact,request_id:c.request_id,request_hash:pending.request_hash,plan_hash:c.expected_plan_hash};
  const coordinates={request_id:c.request_id,request_hash:pending.request_hash,retry_allowed:false};
  const found={...coordinates,request_state:'found',result_scope:'historical_original_outcome',result};
  const missing={...coordinates,request_state:'not_found',result_scope:'unconfirmed_request',result:null};
  const adapter:Adapter={context:vi.fn(async()=>({...context})),list:vi.fn(),read:vi.fn(async()=>source),prepare:vi.fn(async()=>prepared),
    verifySource:vi.fn(async()=>{}),submit:vi.fn(async()=>result),lookup:vi.fn(async()=>found),seal:vi.fn()};
  const props={identity,adapter,store,operationId:source.report.operation_id,decisionId:d.headquarters_decision_id,onBack:vi.fn()};
  return {source,pending,store,prepared,context,adapter,props,missing};
}
it('requires evidence and confirmation, keeps lost write across remount, then recovers without write permission',async()=>{
  const w=await world();vi.mocked(w.adapter.submit).mockRejectedValueOnce(new Error('响应丢失'));
  const rendered=render(<FormalOriginalScrap {...w.props}/>);
  const preview=await screen.findByRole('button',{name:'预览报废'});expect((preview as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole('textbox',{name:'报废执行说明'}),{target:{value:'本次报废已核验'}});
  fireEvent.click(screen.getByRole('button',{name:'选择测试附件'}));fireEvent.click(preview);
  const confirm=await screen.findByRole('button',{name:'确认执行报废'});expect((confirm as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(confirm);await screen.findByText('响应丢失');
  expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.store.list(w.pending.person_id)).toEqual([w.pending]);
  rendered.unmount();w.context.can_write=false;render(<FormalOriginalScrap {...w.props}/>);
  const lookup=await screen.findByRole('button',{name:'回查报废原请求'});await waitFor(()=>expect((lookup as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByRole('button',{name:'永久封存报废原请求'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(lookup);await screen.findByText(/本次报废已记账并从可管理资产移出/);
  expect(w.store.list(w.pending.person_id)).toEqual([]);expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.adapter.prepare).toHaveBeenCalledTimes(1);
});
it('shows pending originals without a selected source and never interprets missing as retry permission',async()=>{
  const w=await world();await w.store.withLease(w.pending,async l=>l.persist(w.pending));vi.mocked(w.adapter.lookup).mockResolvedValue(w.missing);
  render(<FormalOriginalScrap {...w.props} operationId={undefined} decisionId={undefined}/>);
  const lookup=await screen.findByRole('button',{name:'回查报废原请求'});await waitFor(()=>expect((lookup as HTMLButtonElement).disabled).toBe(false));fireEvent.click(lookup);
  await screen.findByText(/尚未查到原请求结果/);expect(w.store.list(w.pending.person_id)).toEqual([w.pending]);
  fireEvent.click(screen.getByRole('button',{name:'永久封存报废原请求'}));await screen.findByRole('alertdialog');expect(w.adapter.seal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'取消封存'}));expect(w.adapter.submit).not.toHaveBeenCalled();expect(w.adapter.read).not.toHaveBeenCalled();
});
it('does not offer execution for a historical posting',async()=>{
  const w=await world();w.source.decisions[0].original_posting=fixture.after.decisions[0].original_posting;
  render(<FormalOriginalScrap {...w.props}/>);await screen.findByText(/原处置已记账，请查看历史/);
  expect(screen.queryByRole('button',{name:'预览报废'})).toBeNull();expect(w.adapter.prepare).not.toHaveBeenCalled();
});
