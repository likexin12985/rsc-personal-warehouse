import {useEffect,useMemo,useRef,useState} from 'react';
import {canonical,fail,type Identity} from './formalReturnReceiving';
import {input,type Kind} from './formalLossSenderCommands';
import {browserStore,pending,recover,seal,submit,type Pending,type Store,type Resolution,type Context} from './lossSenderRecovery';
import type {Adapter} from './lossSenderAdapter';
import LossSendingForm from './LossSendingForm';
type Directory=Awaited<ReturnType<Adapter['list']>>;
type Choices=Awaited<ReturnType<Adapter['choices']>>;
const label=(kind:Kind)=>kind==='outbound_return'?'出库':'交运';
const errorText=(e:unknown)=>e instanceof Error?e.message:'发件结果暂未确认，请保留原请求';
export default function FormalLossSendingPage({identity,adapters,store:provided}:{identity:Identity;adapters:Record<Kind,Adapter>;store?:Store}){
 const store=useMemo(()=>provided??browserStore(),[provided]);
 const [items,setItems]=useState<Directory['items']>([]),[page,setPage]=useState<Directory|null>(null),[loaded,setLoaded]=useState(false);
 const [requests,setRequests]=useState<Pending[]>([]),[storageReady,setStorageReady]=useState(false),[writes,setWrites]=useState({outbound_return:false,ship_return:false});
 const [selection,setSelection]=useState<{kind:Kind;data:Choices}|null>(null),[prepared,setPrepared]=useState<Pending|null>(null),[confirmed,setConfirmed]=useState(false),[sealTarget,setSealTarget]=useState<Pending|null>(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0),working=useRef(false);
 function access(value:Context,write=false){if(value.person_id!==identity.person_id||value.authorization_version!==identity.authorization_version||!value.can_read||(write&&!value.can_write))fail('当前发件身份或权限已变化，请重新进入');return value;}
 function local(epoch:number){try{const rows=store.list(identity.person_id);if(epoch===generation.current){setRequests(rows);setStorageReady(true);}}catch(e){if(epoch===generation.current){setStorageReady(false);setError(errorText(e));}}}
 async function action(work:(epoch:number,valid:()=>boolean)=>Promise<void>){
  if(working.current)return;const epoch=generation.current,valid=()=>epoch===generation.current;working.current=true;setBusy(true);setError('');
  try{await work(epoch,valid);}catch(e){if(valid())setError(errorText(e));}finally{if(valid()){working.current=false;setBusy(false);local(epoch);}}
 }
 async function load(epoch:number,previous:Directory|null=null){
  const out=access(await adapters.outbound_return.context()),ship=access(await adapters.ship_return.context());
  if(out.authority_hash!==ship.authority_hash)fail('读取期间发件权限变化，请刷新');
  const result=await adapters.outbound_return.list(previous?.next_after_id??null,previous?.snapshot_hash);
  if(result.person_id!==identity.person_id||result.authorization_version!==identity.authorization_version)fail('目录身份变化');
  if(previous&&items.some(row=>result.items.some(next=>next.origin.operation_id===row.origin.operation_id)))fail('目录分页重复，请刷新');
  if(epoch===generation.current){setWrites({outbound_return:out.can_write,ship_return:ship.can_write});setItems(old=>previous?[...old,...result.items]:result.items);setPage(result);setLoaded(true);}
 }
 useEffect(()=>{
  const epoch=++generation.current;working.current=false;setItems([]);setPage(null);setLoaded(false);setRequests([]);setStorageReady(false);setWrites({outbound_return:false,ship_return:false});setSelection(null);setPrepared(null);setConfirmed(false);setSealTarget(null);setNotice('');local(epoch);void action(n=>load(n));
  return()=>{generation.current++;};
 },[identity.person_id,identity.authorization_version,adapters,store]);
 function refresh(){void action(async epoch=>{setSelection(null);setPrepared(null);setConfirmed(false);setSealTarget(null);setItems([]);setPage(null);setLoaded(false);setWrites({outbound_return:false,ship_return:false});await load(epoch);});}
 function open(operation:string,kind:Kind){void action(async(_epoch,valid)=>{
  setSelection(null);setPrepared(null);setConfirmed(false);setSealTarget(null);
  const rows=store.list(identity.person_id);if(rows.some(p=>p.operation_id===operation))fail('此退回单有待核验原请求，请先回查');
  access(await adapters[kind].context(),true);const data=await adapters[kind].choices(operation);
  if(data.detail.person_id!==identity.person_id||data.detail.authorization_version!==identity.authorization_version||data.detail.origin.operation_id!==operation)fail('所选退回单不一致');
  if(valid())setSelection({kind,data});
 });}
 async function prepare(body:ReturnType<typeof input>){if(!selection||!storageReady)return;const selected=selection;
  await action(async(_epoch,valid)=>{
   const adapter=adapters[selected.kind],before=access(await adapter.context(),true),operation=selected.data.detail.origin.operation_id;
   if(store.list(identity.person_id).some(p=>p.operation_id===operation))fail('此退回单有待核验原请求，请先回查');
   const snapshot=await adapter.prepare(operation,body);
   if(canonical(snapshot.detail.origin)!==canonical(selected.data.detail.origin)||canonical(snapshot.detail.line)!==canonical(selected.data.detail.line))fail('原退回单变化，请重新选择');
   const p=pending({v:1,kind:selected.kind,person_id:before.person_id,authorization_version:before.authorization_version,operation_id:operation,...snapshot,command:{...body,expected_plan_hash:snapshot.preview.plan_hash,request_id:crypto.randomUUID(),idempotency_key:crypto.randomUUID()}});
   const after=access(await adapter.context(),true);if(canonical(before)!==canonical(after))fail('预检期间权限变化');if(valid()){setPrepared(p);setConfirmed(false);}
  });
 }
 function message(r:Resolution,p:Pending){
  if(r.status==='found'){
   if(p.kind==='outbound_return'&&'outbound_no'in r.result)return `出库单 ${r.result.outbound_no} 已核验，物料已转入物理在途，请按实际交运情况登记包裹。`;
   if(p.kind==='ship_return'&&'shipment_no'in r.result)return `运单 ${r.result.shipment_no} 已核验交运，收货与入库仍需分别确认。`;
   fail('回查结果与原发件类型不一致');
  }
  return r.status==='sealed'?'原发件请求已永久封存；已发生的出库、交运或收货不被撤销。':'尚未观察到原发件结果，请保留原请求继续回查，勿重新提交。';
 }
 async function send(){if(!prepared||!confirmed)return;const p=prepared;
  await action(async(epoch,valid)=>{try{const r=await submit(adapters[p.kind],store,p,valid);if(!valid())return;setNotice(message(r,p));if(r.status!=='unknown'){setSelection(null);await load(epoch);}}finally{if(valid()){setPrepared(null);setConfirmed(false);}}});
 }
 function resolve(p:Pending,permanent=false){void action(async(epoch,valid)=>{
  const r=permanent?await seal(adapters[p.kind],store,p,true,valid):await recover(adapters[p.kind],store,p,valid);
  if(!valid())return;setNotice(message(r,p));setSealTarget(null);setPrepared(null);setSelection(null);setConfirmed(false);if(r.status!=='unknown')await load(epoch);
 });}
 const blocked=selection?requests.some(p=>p.operation_id===selection.data.detail.origin.operation_id):false;
 return <section className="page-stack return-receiving-page loss-sending-page">
  <div className="page-heading"><div><h1>报损退回发件</h1><p>按实际出库、交运分别登记，收货、入库和通知送达独立记录。</p></div><button disabled={busy} onClick={refresh}>刷新发件目录</button></div>
  {error&&<div className="alert alert-error" role="alert">{error}</div>}{notice&&<div className="alert" role="status">{notice}</div>}{busy&&<p role="status">正在核验，请勿重复操作…</p>}
  {!storageReady&&<p role="alert">本机原发件请求不可读，已停止新提交。请保留浏览器数据。</p>}
  {!!requests.length&&<section className="panel" aria-label="发件原请求待核验"><h2>原发件请求待核验（{requests.length}）</h2><p>网络中断不代表未执行。先回查本次原请求。</p>{requests.map(p=><article key={`${p.operation_id}:${p.command.request_id}`}><strong>{p.detail.operation_no} · {label(p.kind)}</strong><p>{p.command.reason}</p><button disabled={busy} onClick={()=>resolve(p)}>回查原{label(p.kind)}请求</button><button disabled={busy||!writes[p.kind]} onClick={()=>setSealTarget(p)}>永久封存原{label(p.kind)}请求</button></article>)}</section>}
  {sealTarget&&<section className="panel" role="alertdialog" aria-label="确认封存原发件请求"><h2>永久封存原{label(sealTarget.kind)}请求？</h2><p>{sealTarget.detail.operation_no} · {sealTarget.command.reason}</p><p>先回查；尚未执行时阻止本次请求迟到执行，不撤销已发生的库存或物流事实。</p><button disabled={busy} onClick={()=>resolve(sealTarget,true)}>确认永久封存</button><button disabled={busy} onClick={()=>setSealTarget(null)}>取消封存</button></section>}
  <section className="panel" aria-label="本人报损退回目录"><h2>本人报损退回单</h2>{!loaded&&!busy&&<p>目录待核验，不能据此判断没有退回单。</p>}{loaded&&!items.length&&<p>当前已核验，没有本人报损派生退回单。</p>}
   {items.map(row=>{const key=row.origin.operation_id,unresolved=requests.some(p=>p.operation_id===key);return <article key={key}><h3>{row.operation_no}</h3><p>{row.reason} · 目的地：{row.destination.target_location_name}</p>{unresolved&&<p>此单有待核验原请求，暂停新发件。</p>}<div className="toolbar">{(['outbound_return','ship_return']as const).map(kind=><button key={kind} disabled={busy||!storageReady||unresolved||!writes[kind]} onClick={()=>open(key,kind)}>登记{label(kind)}</button>)}</div></article>;})}
   {page?.next_after_id&&<button disabled={busy} onClick={()=>void action(epoch=>load(epoch,page))}>下一页退回单</button>}
  </section>
  {!busy&&!writes.outbound_return&&!writes.ship_return&&<p>当前不能新增发件，可按读权限回查已有原请求。</p>}
  {selection&&<section className="panel" aria-label={`本次${label(selection.kind)}选择`}><h2>{selection.data.detail.operation_no} · 登记{label(selection.kind)}</h2><p>来源报损单：{selection.data.detail.loss_operation_no} · 原退回数量 {selection.data.detail.line.return_quantity} {selection.data.detail.line.base_unit}</p><p>目的地：{selection.data.options.destination.target_location_name}</p>
   <LossSendingForm key={`${selection.kind}:${selection.data.options.operation_id}:${selection.data.options.queried_at}`} kind={selection.kind} choices={selection.data.options} disabled={busy||!!prepared||blocked||!storageReady||!writes[selection.kind]} onPrepare={body=>void prepare(body)} onError={setError}/>
  </section>}
  {prepared&&<section className="panel" aria-label="确认本次发件"><h2>确认本次{label(prepared.kind)}</h2><p>{prepared.detail.operation_no} · {prepared.command.reason}</p><p>目的地：{prepared.preview.destination.target_location_name}</p><p>实际{label(prepared.kind)}时间：{new Date('outbound_at'in prepared.preview?prepared.preview.outbound_at:prepared.preview.shipped_at).toLocaleString('zh-CN')}</p>
   {'carrier'in prepared.preview&&<p>承运商：{prepared.preview.carrier} · 运单号：{prepared.preview.tracking_no}</p>}
   <ul>{prepared.preview.lines.map((line,i)=><li key={i}>{line.sku_code} · {line.material_name} · 本次 {line.selected_quantity} {line.base_unit}{line.selected_serials.length>0&&<p>本次 SN：{line.selected_serials.map(sn=>sn.serial_no).join('、')}</p>}</li>)}</ul>
   <p>{prepared.kind==='outbound_return'?'确认后记录实物出库并转入物理在途。':'确认后记录本包裹交运。'}收货和入库仍需后续独立操作。</p>
   <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>已核对物料、数量、SN、实际时间和目的地</label><div className="toolbar"><button disabled={busy||!confirmed||!storageReady||!writes[prepared.kind]} onClick={()=>void send()}>确认登记{label(prepared.kind)}</button><button disabled={busy} onClick={()=>{setPrepared(null);setConfirmed(false);}}>返回修改</button></div>
  </section>}
 </section>;
}
