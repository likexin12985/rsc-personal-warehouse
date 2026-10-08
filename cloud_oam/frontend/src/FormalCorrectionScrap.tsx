import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import type { Detail } from './lossCorrectionAdapter';
import type { Adapter, Prepared } from './scrapCorrectionAdapter';
import type { Pending } from './formalScrapCommands';
import { browserStore, submit, recover, seal, type Store } from './formalScrapRecovery';
import FormalFileUploadField, { type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import './lossExecution.css';

type Props={identity:Identity;adapter:Adapter;rootId?:string;decisionId?:string;store?:Store;
  uploadClient?:FormalFileUploadClient;onBack:()=>void};
const message=(e:unknown)=>e instanceof Error?e.message:'结果未确认，请保留原请求并回查';
export default function FormalCorrectionScrap({identity,adapter,rootId,decisionId,store:provided,uploadClient,onBack}:Props){
  const store=useMemo(()=>provided??browserStore(),[provided]);
  const [source,setSource]=useState<Detail|null>(null),[requests,setRequests]=useState<Pending[]>([]);
  const [prepared,setPrepared]=useState<Prepared|null>(null),[closing,setClosing]=useState<Pending|null>(null);
  const [files,setFiles]=useState<readonly AvailableFormalFile[]>([]),[uploading,setUploading]=useState(false);
  const [reason,setReason]=useState(''),[confirmed,setConfirmed]=useState(false),[canWrite,setCanWrite]=useState(false);
  const [storageReady,setStorageReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const epoch=useRef(0),active=useRef(false);
  const valid=(n:number)=>active.current&&epoch.current===n;
  function local(n:number){try{const rows=store.list(identity.person_id).filter(p=>p.kind==='correction');
    if(valid(n)){setRequests(rows);setStorageReady(true);}}catch(e){if(valid(n)){setStorageReady(false);setError(message(e));}}}
  async function load(){
    const n=epoch.current;setBusy(true);setError('');setPrepared(null);setConfirmed(false);local(n);
    try{const c=await adapter.context('correction');if(c.person_id!==identity.person_id||c.authorization_version!==identity.authorization_version||!c.can_read)throw new Error('身份或权限已变化，请重新进入');
      const s=rootId?await adapter.describe(rootId):null;
      if(valid(n)){setSource(s);setCanWrite(c.can_write);}
    }catch(e){if(valid(n)){setError(message(e));setCanWrite(false);setSource(null);}}
    finally{if(valid(n))setBusy(false);}
  }
  useEffect(()=>{active.current=true;epoch.current++;setSource(null);setPrepared(null);setClosing(null);setFiles([]);setReason('');setNotice('');void load();
    return()=>{active.current=false;epoch.current++;};},[identity.person_id,identity.authorization_version,adapter,store,rootId,decisionId]);
  useEffect(()=>{const changed=()=>local(epoch.current);window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[identity.person_id,store]);
  const selected=source?.source.approval_choices.find(d=>d.correction_decision_id===decisionId&&d.disposition==='scrap');
  const line=source?.line;
  const unresolved=requests.some(p=>'execution_reason' in p.original&&p.original.source.kind==='correction'&&p.original.source.root_disposition_id===rootId);
  function invalidate(){setPrepared(null);setConfirmed(false);}
  async function preview(){
    if(busy||uploading||!storageReady||!canWrite||!rootId||!decisionId||!files.length||!reason.trim())return;
    const n=epoch.current;setBusy(true);setError('');invalidate();
    try{const p=await adapter.prepare(rootId,decisionId,reason.trim(),files.map(f=>f.file_id));if(valid(n))setPrepared(p);}
    catch(e){if(valid(n))setError(message(e));}finally{if(valid(n))setBusy(false);}
  }
  async function action(p:Pending,mode:'submit'|'recover'|'seal'){
    if(busy||mode==='submit'&&!confirmed)return;const n=epoch.current;
    setBusy(true);setError('');setNotice('');setClosing(null);invalidate();
    try{const r=await({submit,recover,seal}[mode])(adapter,store,p,()=>valid(n));if(!valid(n))return;
      setNotice(r.status==='pending'?'尚未查到原请求结果，已保留原请求，请继续回查，不能再次提交。':r.status==='sealed'?'原请求已永久封存，不能迟到执行；已发生的库存记账不会撤销。':'原请求已核验：本次纠正报废已记账并从可管理资产移出。此历史结果不代表当前库存。');
      if(rootId){const fresh=await adapter.describe(rootId);if(valid(n))setSource(fresh);}
    }catch(e){if(valid(n))setError(message(e));}
    finally{if(valid(n)){local(n);setBusy(false);}}
  }
  return <section className="page-stack loss-execution-page">
    <div className="page-heading"><div><h1>纠正报废</h1><p>依据独立纠正批准执行，上传本次证据后预览并确认。</p></div><button onClick={onBack} disabled={busy}>返回报损纠正</button><button onClick={()=>void load()} disabled={busy}>刷新</button></div>
    {error&&<p role="alert" className="alert alert-error">{error}</p>}{notice&&<p role="status" className="alert">{notice}</p>}
    {busy&&<p role="status">正在核验，请稍候…</p>}
    {!storageReady&&<p role="alert">原请求存储不可用，已停止新提交。请保留浏览器数据。</p>}
    {!canWrite&&<p>当前仅可查看和回查原请求。</p>}
    {!!requests.length&&<section className="panel"><h2>报废原请求待核验（{requests.length}）</h2><p>刷新或断网后先回查，查不到结果也不能自动重发。</p>{requests.map(p=><div className="toolbar" key={p.original.request_id}><span>请求 {p.original.request_id}</span><button disabled={busy} onClick={()=>void action(p,'recover')}>回查报废原请求</button><button disabled={busy||!canWrite} onClick={()=>setClosing(p)}>永久封存报废原请求</button></div>)}</section>}
    {closing&&<section className="panel" role="alertdialog" aria-label="确认封存报废请求"><h2>永久封存本次原请求？</h2><p>先查是否已执行；未执行时阻止此请求迟到执行。这不会撤销已有库存记账。</p><button disabled={busy||!canWrite} onClick={()=>void action(closing,'seal')}>确认永久封存</button><button disabled={busy} onClick={()=>setClosing(null)}>取消封存</button></section>}
    {!rootId&&<p>请从报损纠正页选择已批准的报废决定。此页也可回查已有原请求。</p>}
    {rootId&&source&&(!selected||!line)&&<p role="alert">没有与当前选择对应的报废批准，请返回重新选择。</p>}
    {source&&selected&&line&&<section className="panel"><h2>{source.report.operation_no} · {line.material_name}</h2><p>{line.sku_code} · {line.quantity} {line.base_unit} · {source.report.requester_name}</p><p>总部批准理由：{selected.reason}</p>{!!line.serials.length&&<p>SN：{line.serials.map(s=>s.serial_no).join('、')}</p>}
      {source.source.chain_state!=='awaiting_execution'?<p>当前没有待执行的纠正报废，请查看完整历史。</p>:unresolved?<p>本行已有原请求待核验，请使用上方回查入口。</p>:<fieldset disabled={busy||!canWrite||!storageReady}><legend>本次报废证据</legend>
        <label>执行说明<textarea aria-label="报废执行说明" maxLength={500} value={reason} onChange={e=>{setReason(e.target.value);invalidate();}}/></label>
        <FormalFileUploadField purpose="stock_loss_evidence" bindingKey={`${identity.person_id}:${identity.authorization_version}:${rootId}:${decisionId}`} label="本次报废附件" multiple disabled={busy||!canWrite||!storageReady} client={uploadClient}
          onAvailableChange={value=>{setFiles(value);invalidate();}} onBlockingChange={setUploading}/>
        <button disabled={busy||uploading||!files.length||files.length>20||!reason.trim()} onClick={()=>void preview()}>预览报废</button>
      </fieldset>}
    </section>}
    {prepared&&<section className="panel" aria-label="确认报废方案"><h2>确认报废方案</h2><p>{prepared.detail.report.operation_no} · {prepared.detail.line.material_name} · {prepared.preview.quantity}</p><p>确认后将从可管理资产移出，并保留不可变库存流水和证据。</p><label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>已核对实物、数量和本次证据，确认报废</label><button disabled={busy||!confirmed||!canWrite||!storageReady} onClick={()=>void action(prepared.pending,'submit')}>确认执行报废</button><button disabled={busy} onClick={invalidate}>取消预览</button></section>}
  </section>;
}
