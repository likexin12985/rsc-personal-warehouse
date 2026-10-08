import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity, Report } from './formalLossReview';
import type { Pending, Sources } from './lossExecutionContracts';
import type { Adapter } from './lossExecutionAdapter';
import { browserStore, execute, recover, seal, type Store } from './lossExecutionRecovery';
import './lossExecution.css';

const names={restore_available:'恢复可用',convert_used:'转旧件',convert_damaged:'转坏件',return_to_region:'退回区域仓',scrap:'报废'};
const message=(e:unknown)=>e instanceof Error?e.message:'结果未确认，请保留原请求并回查';
type Props={identity:Identity;adapter:Adapter;store?:Store;onOpenCorrection?:(root:string)=>void;onOpenScrap?:(operation?:string,decision?:string)=>void};
export default function FormalLossExecution({identity,adapter,store:provided,onOpenCorrection,onOpenScrap}:Props){
  const store=useMemo(()=>provided??browserStore(),[provided]);
  const [rows,setRows]=useState<Report[]>([]),[next,setNext]=useState<string|null>(null),[source,setSource]=useState<Sources|null>(null);
  const [pending,setPending]=useState<Pending[]>([]),[prepared,setPrepared]=useState<Pending|null>(null),[sealTarget,setSealTarget]=useState<Pending|null>(null);
  const [routes,setRoutes]=useState<Record<string,string>>({}),[canWrite,setCanWrite]=useState(false),[storageReady,setStorageReady]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirmed,setConfirmed]=useState(false);
  const epoch=useRef(0),active=useRef(false);
  const valid=(captured:number)=>active.current&&epoch.current===captured;
  function local(captured:number){try{const list=store.list(identity.person_id);if(valid(captured)){setPending(list);setStorageReady(true);}}catch(e){if(valid(captured)){setStorageReady(false);setError(message(e));}}}
  async function load(after:string|null=null){
    const captured=epoch.current;setBusy(true);setError('');setPrepared(null);setConfirmed(false);local(captured);
    try{const c=await adapter.context(),page=await adapter.list(after);if(!valid(captured))return;
      setCanWrite(c.can_write);const approved=page.items.filter((r):r is Report=>r.availability==='available'&&r.approval_stage==='approved');
      setRows(old=>after?[...old.filter(r=>!approved.some(x=>x.operation_id===r.operation_id)),...approved]:approved);setNext(page.next_after_id);
      if(page.items.some(r=>r.availability==='blocked'))setNotice('部分原单证据待核验，未提供执行入口。');
    }catch(e){if(valid(captured)){setError(message(e));setCanWrite(false);if(!after){setRows([]);setNext(null);}}}
    finally{if(valid(captured))setBusy(false);}
  }
  useEffect(()=>{active.current=true;epoch.current++;setSource(null);setRoutes({});setSealTarget(null);setNotice('');void load();return()=>{active.current=false;epoch.current++;};},[identity.person_id,identity.authorization_version,adapter,store]);
  useEffect(()=>{const changed=()=>local(epoch.current);window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[identity.person_id,store]);
  async function open(operation:string){
    const captured=epoch.current;setBusy(true);setError('');setSource(null);setPrepared(null);setRoutes({});setConfirmed(false);
    try{const s=await adapter.read(operation);if(valid(captured))setSource(s);}catch(e){if(valid(captured))setError(message(e));}finally{if(valid(captured))setBusy(false);}
  }
  async function preview(decision:string){
    if(!source||busy||!storageReady)return;const captured=epoch.current;setBusy(true);setError('');setPrepared(null);setConfirmed(false);
    try{const p=await adapter.prepare(source.report.operation_id,decision,routes[decision]);if(valid(captured))setPrepared(p);}catch(e){if(valid(captured))setError(message(e));}finally{if(valid(captured))setBusy(false);}
  }
  async function action(p:Pending,mode:'execute'|'recover'|'seal'){
    if(busy||mode==='execute'&&!confirmed)return;const captured=epoch.current;setBusy(true);setError('');setNotice('');setPrepared(null);setConfirmed(false);setSealTarget(null);
    try{const result=await({execute,recover,seal}[mode])(adapter,store,p,()=>valid(captured));if(!valid(captured))return;
      setNotice(result.status==='pending'?'尚未查到原请求结果，已保留原请求。请继续回查，不能据此再次提交。':result.status==='sealed'?'本次原请求已永久封存，不能迟到执行；已有处置不会被撤销。':p.flow==='return'?'原请求已核验：已生成退回单，仍需单独发货、收货和入库。':'原请求已核验：已完成该次处置记账。此结果不代表当前库存。');
      if(source?.report.operation_id===p.operation_id){const fresh=await adapter.read(p.operation_id);if(valid(captured))setSource(fresh);}
    }catch(e){if(valid(captured))setError(message(e));}
    finally{if(valid(captured)){local(captured);setBusy(false);}}
  }
  return <section className="page-stack loss-execution-page">
    <div className="page-heading"><div><h1>报损处置</h1><p>依据总部批准逐项执行。退回单还需单独完成发货、收货和入库。</p></div><button disabled={busy} onClick={()=>void load()}>刷新</button>{onOpenScrap&&<button disabled={busy} onClick={()=>onOpenScrap()}>报废请求回查</button>}</div>
    {error&&<p className="alert alert-error" role="alert">{error}</p>}{notice&&<p className="alert" role="status">{notice}</p>}
    {busy&&<p role="status">正在核验，请稍候…</p>}
    {!storageReady&&<p role="alert">原请求存储不可用，已停止新处置。请保留浏览器数据。</p>}
    {!canWrite&&<p>当前仅可查看和回查原请求。</p>}
    {!!pending.length&&<section className="panel"><h2>原请求待核验（{pending.length}）</h2><p>中断后先回查，不会自动重发。查不到结果也不能直接重试。</p>
      {pending.map(p=><div key={p.command.headquarters_decision_id} className="toolbar"><span>{p.operation_no} · {p.material_name} · {names[p.disposition]}</span>
        <button disabled={busy} onClick={()=>void action(p,'recover')}>回查原请求</button><button disabled={busy||!canWrite} onClick={()=>setSealTarget(p)}>永久封存原请求</button></div>)}
    </section>}
    {sealTarget&&<section className="panel" role="alertdialog" aria-label="确认封存处置请求"><h2>永久封存本次原请求？</h2><p>先核验是否已经执行；未执行时永久阻止该原请求迟到执行。这不会撤销已经完成的库存记账。</p>
      <button disabled={busy||!canWrite} onClick={()=>void action(sealTarget,'seal')}>确认永久封存</button><button disabled={busy} onClick={()=>setSealTarget(null)}>取消</button></section>}
    <section className="panel"><h2>已终审报损单</h2><p>批准与实际处置分别核验。</p>
      {!rows.length&&!busy&&<p>{next?'本页无已终审单据，可继续下一页。':'当前没有已终审单据。'}</p>}
      <div className="table-scroll"><table><thead><tr><th>报损单</th><th>申请人 / 区域</th><th>操作</th></tr></thead><tbody>{rows.map(r=><tr key={r.operation_id}><td data-label="报损单"><span>{r.operation_no}</span></td><td data-label="申请人 / 区域"><span>{r.requester_name} / {r.owner_org_name}</span></td><td data-label="操作"><button disabled={busy} onClick={()=>void open(r.operation_id)}>查看处置明细</button></td></tr>)}</tbody></table></div>
      {next&&<button disabled={busy} onClick={()=>void load(next)}>下一页</button>}
    </section>
    {source&&<section className="panel" aria-label="处置明细"><h2>{source.report.operation_no}</h2><p>{source.report.requester_name} · {source.report.source_location_name}</p><p>原报损原因：{source.report.reason}</p>
      {source.decisions.map(d=>{const line=source.report.lines.find(l=>l.line_id===d.line_id)!;const unresolved=pending.some(p=>p.command.headquarters_decision_id===d.headquarters_decision_id);return <fieldset key={d.headquarters_decision_id} disabled={busy}><legend>{line.sku_code} · {line.material_name}</legend>
        <p>{line.quantity} {line.base_unit} · 批准处置：{names[d.disposition]}</p><p>逐行批准理由：{d.reason}</p>{!!line.serials.length&&<p>SN：{line.serials.map(s=>s.serial_no).join('、')}</p>}
        {d.original_posting?<><p>原处置已记账（历史事实，不代表当前库存）。{d.original_posting.return_operation_id&&'原处置已生成退回单；本页不证明已经发货、收货或入库。'}</p>{onOpenCorrection&&<button disabled={busy} onClick={()=>onOpenCorrection(d.original_posting!.disposition_id)}>查看纠正与历史</button>}</>:unresolved?<p>已有原请求待核验，请使用上方回查入口。</p>:d.disposition==='scrap'?(onOpenScrap?<button disabled={busy||!canWrite} onClick={()=>onOpenScrap(source.report.operation_id,d.headquarters_decision_id)}>办理报废</button>:<p>请从报废执行页办理此项批准。</p>):<>
          {d.disposition==='return_to_region'&&(source.return_routes_status==='available'?<label>退回路线<select aria-label={`退回路线 ${line.sku_code}`} value={routes[d.headquarters_decision_id]??''} onChange={e=>{setRoutes(old=>({...old,[d.headquarters_decision_id]:e.target.value}));setPrepared(null);setConfirmed(false);}}><option value="">请选择</option>{source.return_routes.map(r=><option key={r.target_location_id+':'+r.transit_location_id} value={r.target_location_id+':'+r.transit_location_id}>{r.target_location_name} · {r.transit_location_name}</option>)}</select></label>:<p>{source.return_routes_reason??'当前退回路线无法核验。'}</p>)}
          <button disabled={busy||!canWrite||!storageReady||(d.disposition==='return_to_region'&&!routes[d.headquarters_decision_id])} onClick={()=>void preview(d.headquarters_decision_id)}>预览处置</button>
        </>}
      </fieldset>;})}
    </section>}
    {prepared&&<section className="panel" aria-label="确认处置方案"><h2>确认处置方案</h2><p>{prepared.operation_no} · {prepared.material_name} · {prepared.preview.quantity} · {names[prepared.disposition]}</p><p>{prepared.preview.reason}</p>
      {prepared.flow==='return'?<><p>退回路线：{source?.return_routes.filter(r=>r.target_location_id===prepared.command.target_location_id&&r.transit_location_id===prepared.command.transit_location_id).map(r=>`${r.target_location_name} · ${r.transit_location_name}`).join('')}</p><p>本次仅生成退回单并转入待退回库存。实际发货、收货和入库需分别完成。</p></>:<p>本次将按批准的处置改变库存状态。</p>}
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>已核对物料、数量和处置方案，确认执行</label>
      <button disabled={busy||!confirmed||!canWrite||!storageReady} onClick={()=>void action(prepared,'execute')}>确认执行处置</button><button disabled={busy} onClick={()=>{setPrepared(null);setConfirmed(false);}}>取消</button>
    </section>}
  </section>;
}
