// @vitest-environment jsdom
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import qo from './test-fixtures/loss-sender/h5-write-contracts/quantity-outbound_return.json';
import qs from './test-fixtures/loss-sender/h5-write-contracts/quantity-ship_return.json';
import so from './test-fixtures/loss-sender/h5-write-contracts/serial-outbound_return.json';
import ss from './test-fixtures/loss-sender/h5-write-contracts/serial-ship_return.json';
import FormalLossSendingPage from './FormalLossSendingPage';
import {pending,createStore,type Pending} from './lossSenderRecovery';
import {input,preview,requestHash,type Kind} from './formalLossSenderCommands';
import type {Adapter} from './lossSenderAdapter';
function fixture(tracking:string,kind:Kind){return JSON.parse(JSON.stringify(tracking==='quantity'?(kind==='outbound_return'?qo:qs):(kind==='outbound_return'?so:ss)));}
beforeEach(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});
afterEach(()=>{cleanup();localStorage.clear();vi.unstubAllGlobals();});
const now=()=>new Date().toISOString();
function setup(tracking='quantity',kind:Kind='outbound_return'){
 const f=fixture(tracking,kind),store=createStore(localStorage,{request:async(_n,_o,fn)=>fn({})});
 const directory={schema_version:'1.0' as const,...f.identity,ledger_cursor:f.detail.ledger_cursor,queried_at:f.detail.queried_at,snapshot_hash:'a'.repeat(64),items:[{operation_no:f.detail.operation_no,reason:f.detail.reason,destination:f.detail.destination,origin:f.detail.origin}],next_after_id:null};
 let observed:unknown={lookup_status:'not_observed',retry_allowed:false};
 async function wire(body:ReturnType<typeof input>){
  return {...f.preview,checked_at:now(),reason:body.reason,request_hash:await requestHash(kind,f.detail.origin.operation_id,body,f.identity.person_id),
   ...('outbound_at'in body?{outbound_at:body.outbound_at}:{shipped_at:body.shipped_at,carrier:body.carrier,tracking_no:body.tracking_no}),
   lines:body.lines.map(l=>({...f.preview.lines[0],selected_quantity:l.quantity,selected_serials:'serial_verifications'in l?l.serial_verifications.map(s=>({serial_id:s.serial_id,serial_no:s.serial_no})):f.preview.lines[0].selected_serials.filter((s:{serial_id:string})=>l.serial_ids.includes(s.serial_id))}))};
 }
 const make=(action:Kind):Adapter=>({
  context:vi.fn(async()=>({...f.identity,authority_hash:'c'.repeat(64),can_read:true,can_write:action===kind})),
  list:vi.fn(async()=>directory),readDetail:vi.fn(async()=>f.detail),detail:vi.fn(async()=>f.detail),options:vi.fn(async()=>f.options),choices:vi.fn(async()=>({detail:f.detail,options:f.options})),
  prepare:vi.fn(async(_id,raw)=>{const body=input(kind,raw,f.identity.person_id);return {detail:f.detail,options:f.options,preview:await preview(kind,await wire(body),body,f.options,f.detail)};}),
  preview:vi.fn(async p=>{const {expected_plan_hash:_plan,request_id:_request,idempotency_key:_key,...body}=p.command;return wire(body);}),
  submit:vi.fn(async p=>{const result={...f.result,reason:p.command.reason,request_id:p.command.request_id,request_hash:p.preview.request_hash,plan_hash:p.preview.plan_hash,recorded_at:now(),lines:p.preview.lines,
   ...(kind==='outbound_return'?{outbound_at:p.command.outbound_at}:{shipped_at:p.command.shipped_at,carrier:p.command.carrier,tracking_no:p.command.tracking_no})};observed={lookup_status:'found',retry_allowed:false,operation_type:kind,result};return result;}),
  lookup:vi.fn(async()=>observed),seal:vi.fn(async()=>{throw Error('unknown seal');}),
 });
 const adapters={outbound_return:make('outbound_return'),ship_return:make('ship_return')};
 return {f,kind,identity:f.identity,store,adapters,adapter:adapters[kind],directory,setObserved:(value:unknown)=>{observed=value}};
}
function dateInput(value:string){const d=new Date(value),pad=(x:number)=>String(x).padStart(2,'0');return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;}
async function fill(w:ReturnType<typeof setup>){
 const label=w.kind==='outbound_return'?'出库':'交运';const button=await screen.findByText(`登记${label}`);await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
 await screen.findByRole('form',{name:`填写本次${label}`});
 if(w.f.tracking==='serial'){
  const proof=w.f.detail.line.selected_serials[0];fireEvent.click(screen.getByLabelText(`选择 SN ${proof.serial_no}`));
  if(w.kind==='outbound_return')for(const [label,field]of [['实物物料码','sku_code'],['实物 SN','serial_no'],['实物二维码','qr_code']]){
   const element=screen.getByLabelText(`${label} ${proof.serial_no}`) as HTMLInputElement;expect(element.value).toBe('');fireEvent.change(element,{target:{value:w.f.command.lines[0].serial_verifications[0][field]}});
  }
 }else{const row=w.f.options.lines[0],key=row.outbound_line_id??row.operation_line_id;fireEvent.change(screen.getByLabelText(`${label}数量 ${key}`),{target:{value:'1'}});}
 fireEvent.change(screen.getByLabelText(`实际${label}时间`),{target:{value:dateInput(w.f.command.outbound_at??w.f.command.shipped_at)}});
 fireEvent.change(screen.getByLabelText(`${label}说明`),{target:{value:'按实物完成本次操作'}});
 if(w.kind==='ship_return'){fireEvent.change(screen.getByLabelText('承运商'),{target:{value:'合成快递'}});fireEvent.change(screen.getByLabelText('运单号'),{target:{value:'SYNTHETIC-PARCEL-1'}});}
 fireEvent.submit(screen.getByRole('form',{name:`填写本次${label}`}));await screen.findByRole('region',{name:'确认本次发件'});
}
function confirm(kind:Kind){fireEvent.click(screen.getByLabelText('已核对物料、数量、SN、实际时间和目的地'));fireEvent.click(screen.getByText(`确认登记${kind==='outbound_return'?'出库':'交运'}`));}
for(const tracking of ['quantity','serial'])for(const kind of ['outbound_return','ship_return']as const){
 it(`${tracking} ${kind}: independent confirmation and exact readback retain stage meaning`,async()=>{
  const w=setup(tracking,kind);render(<FormalLossSendingPage {...w}/>);await fill(w);
  expect(w.adapter.submit).not.toHaveBeenCalled();expect((screen.getByText(`确认登记${kind==='outbound_return'?'出库':'交运'}`)as HTMLButtonElement).disabled).toBe(true);
  if(kind==='ship_return')expect(screen.getByText(/承运商：合成快递 · 运单号：SYNTHETIC-PARCEL-1/)).toBeTruthy();
  confirm(kind);await screen.findByText(kind==='outbound_return'?/已核验，物料已转入物理在途/:/已核验交运，收货与入库仍需分别确认/);
  expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.adapter.lookup).toHaveBeenCalledTimes(1);expect(w.store.list(w.identity.person_id)).toEqual([]);
 });
 it(`${tracking} ${kind}: lost response survives remount and blocks both new actions on the same return`,async()=>{
  const w=setup(tracking,kind);w.adapter.submit=vi.fn(async()=>{throw Error('回执丢失')});const view=render(<FormalLossSendingPage {...w}/>);await fill(w);confirm(kind);await screen.findByText('回执丢失');
  const original=w.store.list(w.identity.person_id)[0];expect(original.kind).toBe(kind);view.unmount();render(<FormalLossSendingPage {...w}/>);
  const recovery=await screen.findByText(`回查原${kind==='outbound_return'?'出库':'交运'}请求`);await waitFor(()=>expect((recovery as HTMLButtonElement).disabled).toBe(false));fireEvent.click(recovery);
  await screen.findByText(/尚未观察到原发件结果/);expect(w.adapter.submit).toHaveBeenCalledTimes(1);expect(w.store.list(w.identity.person_id)).toEqual([original]);
  for(const text of ['登记出库','登记交运'])expect((screen.getByText(text)as HTMLButtonElement).disabled).toBe(true);
 });
}
it('directory fetch errors are unknown, never an empty business directory',async()=>{
 const w=setup();w.adapters.outbound_return.list=vi.fn(async()=>{throw Error('目录读取超时')});render(<FormalLossSendingPage {...w}/>);
 await screen.findByText('目录读取超时');expect(screen.getByText(/目录待核验/)).toBeTruthy();expect(screen.queryByText(/没有本人报损派生退回单/)).toBeNull();
});
it('read-only request recovery remains usable without loading writable options',async()=>{
 const w=setup(),f=w.f,p=pending({v:1,kind:w.kind,...f.identity,operation_id:f.detail.origin.operation_id,detail:f.detail,options:f.options,command:f.command,preview:f.preview});
 await w.store.withLease(p.person_id,p.operation_id,async lease=>lease.persist(p));
 for(const a of Object.values(w.adapters))a.context=vi.fn(async()=>({...w.identity,authority_hash:'c'.repeat(64),can_read:true,can_write:false}));
 render(<FormalLossSendingPage {...w}/>);const button=await screen.findByText('回查原出库请求');await waitFor(()=>expect((button as HTMLButtonElement).disabled).toBe(false));fireEvent.click(button);
 await screen.findByText(/尚未观察到原发件结果/);expect((screen.getByText('永久封存原出库请求')as HTMLButtonElement).disabled).toBe(true);expect(w.adapter.choices).not.toHaveBeenCalled();expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('corrupt local originals block new physical commands while preserving the record',async()=>{
 const w=setup();localStorage.setItem(`cloud-oam-loss-sender-v1:${w.identity.person_id}:${w.f.detail.origin.operation_id}`,'{bad');render(<FormalLossSendingPage {...w}/>);
 await screen.findByText(/本机原发件请求不可读/);await screen.findByText(w.f.detail.operation_no);expect((screen.getByText('登记出库')as HTMLButtonElement).disabled).toBe(true);expect(localStorage.length).toBe(1);
});
it('seal requires its own confirmation and unknown response preserves original',async()=>{
 const w=setup(),f=w.f,p=pending({v:1,kind:w.kind,...f.identity,operation_id:f.detail.origin.operation_id,detail:f.detail,options:f.options,command:f.missingCommand,preview:f.preview});
 await w.store.withLease(p.person_id,p.operation_id,async lease=>lease.persist(p));render(<FormalLossSendingPage {...w}/>);
 const b=await screen.findByText('永久封存原出库请求');await waitFor(()=>expect((b as HTMLButtonElement).disabled).toBe(false));fireEvent.click(b);
 await screen.findByRole('alertdialog',{name:'确认封存原发件请求'});expect(w.adapter.seal).not.toHaveBeenCalled();fireEvent.click(screen.getByText('确认永久封存'));
 await screen.findByText('unknown seal');expect(w.store.list(w.identity.person_id)).toEqual([p]);expect(w.adapter.seal).toHaveBeenCalledTimes(1);expect(w.adapter.submit).not.toHaveBeenCalled();
});
it('unmount during POST retains original and does not render or clear a stale result',async()=>{
 const w=setup();let release!:(value:unknown)=>void;w.adapter.submit=vi.fn(()=>new Promise(resolve=>{release=resolve}));const view=render(<FormalLossSendingPage {...w}/>);await fill(w);confirm(w.kind);
 await waitFor(()=>expect(w.adapter.submit).toHaveBeenCalledTimes(1));const p=w.store.list(w.identity.person_id)[0];view.unmount();
 release({...w.f.result,reason:p.command.reason,request_id:p.command.request_id,request_hash:p.preview.request_hash,recorded_at:now(),outbound_at:'outbound_at'in p.command?p.command.outbound_at:undefined});
 await waitFor(()=>expect(w.store.list(w.identity.person_id)).toEqual([p]));expect(w.adapter.lookup).not.toHaveBeenCalled();
});
it('physical time starts empty and current time is selected only by explicit action',async()=>{
 const w=setup();render(<FormalLossSendingPage {...w}/>);const open=await screen.findByText('登记出库');await waitFor(()=>expect((open as HTMLButtonElement).disabled).toBe(false));fireEvent.click(open);
 const field=await screen.findByLabelText('实际出库时间') as HTMLInputElement;expect(field.value).toBe('');fireEvent.click(screen.getByText('使用当前时间'));expect(field.value).toMatch(/^\d{4}-\d\d-\d\dT/);
 expect(w.adapter.prepare).not.toHaveBeenCalled();expect(w.adapter.submit).not.toHaveBeenCalled();
});
