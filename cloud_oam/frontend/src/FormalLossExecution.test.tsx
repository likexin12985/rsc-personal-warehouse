// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import FormalLossExecution from './FormalLossExecution';
import { identity, type Queue } from './formalLossReview';
import { createStore } from './lossExecutionRecovery';
import type { Adapter } from './lossExecutionAdapter';
import { fixture, fixtures, saved } from './lossExecutionFixtures';
import { MemoryStorage, locks } from './lossExecutionTestSupport';
beforeEach(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});afterEach(()=>{cleanup();vi.unstubAllGlobals();});
async function world(f=fixture,sealed=false){
  const p=await saved(f,sealed),store=createStore(new MemoryStorage(),locks()),c={...identity(p),can_read:true,can_write:true,authority_hash:'a'.repeat(64)};
  const q:Queue={...identity(p),schema_version:'1.0',stage:'headquarters',view:'all',queried_at:f.source.queried_at,items:[f.source.report],next_after_id:null};
  const adapter:Adapter={context:vi.fn(async()=>({...c})),list:vi.fn(async()=>q),read:vi.fn(async()=>f.source),source:vi.fn(async()=>f.source),prepare:vi.fn(async()=>p),execute:vi.fn(async()=>f.found.disposition),lookup:vi.fn(async()=>f.found),seal:vi.fn(async()=>f.sealed)};
  const props={identity:identity(p),adapter,store};return {p,store,c,adapter,props};
}
it('requires explicit preview confirmation, then preserves a lost response across remount and does not resend',async()=>{
  const w=await world();vi.mocked(w.adapter.execute).mockRejectedValueOnce(new Error('网络响应丢失'));const rendered=render(<FormalLossExecution {...w.props}/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看处置明细'}));fireEvent.click(await screen.findByRole('button',{name:'预览处置'}));
  const confirm=await screen.findByRole('button',{name:'确认执行处置'});expect((confirm as HTMLButtonElement).disabled).toBe(true);expect(w.adapter.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(confirm);await screen.findByText('网络响应丢失');expect(w.store.list(w.p.person_id)).toEqual([w.p]);expect(w.adapter.execute).toHaveBeenCalledTimes(1);
  rendered.unmount();w.c.can_write=false;render(<FormalLossExecution {...w.props}/>);const recover=await screen.findByRole('button',{name:'回查原请求'});
  await waitFor(()=>expect((recover as HTMLButtonElement).disabled).toBe(false));expect(w.adapter.lookup).not.toHaveBeenCalled();expect((screen.getByRole('button',{name:'永久封存原请求'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(recover);await screen.findByText(/原请求已核验：已完成该次处置记账/);expect(w.adapter.execute).toHaveBeenCalledTimes(1);expect(w.store.list(w.p.person_id)).toEqual([]);
});
it('not_found retains a pending request; permanent closure needs a separate confirmation',async()=>{
  const w=await world(fixture,true);await w.store.withLease(w.p.person_id,w.p.command.headquarters_decision_id,async l=>l.persist(w.p));vi.mocked(w.adapter.lookup).mockResolvedValue(fixture.missing);
  render(<FormalLossExecution {...w.props}/>);const button=await screen.findByRole('button',{name:'回查原请求'});await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
  await screen.findByText(/尚未查到原请求结果/);expect(w.store.list(w.p.person_id)).toEqual([w.p]);expect(w.adapter.execute).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'永久封存原请求'}));await screen.findByRole('alertdialog');expect(w.adapter.seal).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'取消'}));expect(w.adapter.seal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'永久封存原请求'}));vi.mocked(w.adapter.lookup).mockResolvedValueOnce(fixture.missing).mockResolvedValue(fixture.sealed);fireEvent.click(screen.getByRole('button',{name:'确认永久封存'}));
  await screen.findByText(/本次原请求已永久封存，不能迟到执行/);expect(w.adapter.seal).toHaveBeenCalledTimes(1);expect(w.adapter.execute).not.toHaveBeenCalled();
});
it('offers only verified return routes and distinguishes created returns from fulfillment',async()=>{
  const f=fixtures.find(f=>f.name==='serial-return_to_region.json')!.data,w=await world(f);render(<FormalLossExecution {...w.props}/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看处置明细'}));const preview=await screen.findByRole('button',{name:'预览处置'});expect((preview as HTMLButtonElement).disabled).toBe(true);
  const route=f.source.return_routes[0];fireEvent.change(screen.getByRole('combobox'),{target:{value:route.target_location_id+':'+route.transit_location_id}});fireEvent.click(preview);
  await screen.findByRole('button',{name:'确认执行处置'});expect(w.adapter.prepare).toHaveBeenCalledWith(w.p.operation_id,w.p.command.headquarters_decision_id,route.target_location_id+':'+route.transit_location_id);
  fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'确认执行处置'}));await screen.findByText(/已生成退回单，仍需单独发货、收货和入库/);
});
it('does not expose another execution button for an already posted historical fact',async()=>{const w=await world();vi.mocked(w.adapter.read).mockResolvedValue(fixture.after);render(<FormalLossExecution {...w.props}/>);fireEvent.click(await screen.findByRole('button',{name:'查看处置明细'}));await screen.findByText(/原处置已记账（历史事实，不代表当前库存）/);expect(screen.queryByRole('button',{name:'预览处置'})).toBeNull();});

it('opens correction history with the exact original disposition identifier',async()=>{
  const w=await world();vi.mocked(w.adapter.read).mockResolvedValue(fixture.after);const open=vi.fn();
  render(<FormalLossExecution {...w.props} onOpenCorrection={open}/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看处置明细'}));
  fireEvent.click(await screen.findByRole('button',{name:'查看纠正与历史'}));
  expect(open).toHaveBeenCalledExactlyOnceWith(fixture.after.decisions[0].original_posting!.disposition_id);
  expect(w.adapter.execute).not.toHaveBeenCalled();
});

it('opens scrap from its exact approved source and exposes a separate original-request entry',async()=>{
  const w=await world(),source=structuredClone(fixture.source),open=vi.fn();
  source.decisions[0].disposition='scrap';source.report.headquarters_review!.decisions[0].disposition='scrap';
  vi.mocked(w.adapter.read).mockResolvedValue(source);render(<FormalLossExecution {...w.props} onOpenScrap={open}/>);
  fireEvent.click(await screen.findByRole('button',{name:'查看处置明细'}));
  fireEvent.click(await screen.findByRole('button',{name:'办理报废'}));
  expect(open).toHaveBeenLastCalledWith(source.report.operation_id,source.decisions[0].headquarters_decision_id);
  fireEvent.click(screen.getByRole('button',{name:'报废请求回查'}));expect(open).toHaveBeenLastCalledWith();
  expect(w.adapter.execute).not.toHaveBeenCalled();
});
