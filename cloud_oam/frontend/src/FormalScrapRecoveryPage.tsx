import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import type { Adapter, Prepared } from './scrapRecoveryAdapter';
import { stageLabels, stateLabels, type Stage, type Source, type Queue, type Serial } from './scrapRecoverySources';
import type { Pending } from './formalScrapCommands';
import { browserStore, submit, recover, seal, type Store } from './formalScrapRecovery';
import FormalFileUploadField, { type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import './lossExecution.css';
import ScrapSerialCheck from './ScrapSerialCheck';
type Props={identity:Identity;adapter:Adapter;stages:Stage[];store?:Store;uploadClient?:FormalFileUploadClient};
const errorMessage=(e:unknown)=>e instanceof Error?e.message:'结果未确认，请保留原请求后回查';
export default function FormalScrapRecovery({identity,adapter,stages,store:provided,uploadClient}:Props){
  const store=useMemo(()=>provided??browserStore(),[provided]);
  const [stage,setStage]=useState<Stage>(stages[0]??'apply'),[page,setPage]=useState<Queue|null>(null),[selected,setSelected]=useState<Source|null>(null);
  const [requests,setRequests]=useState<Pending[]>([]),[prepared,setPrepared]=useState<Prepared|null>(null),[closing,setClosing]=useState<Pending|null>(null);
  const [reason,setReason]=useState(''),[decision,setDecision]=useState(''),[files,setFiles]=useState<readonly AvailableFormalFile[]>([]);
  const [confirmed,setConfirmed]=useState(false),[uploading,setUploading]=useState(false),[busy,setBusy]=useState(false),[canWrite,setCanWrite]=useState(false);
  const [storageReady,setStorageReady]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [verifiedSerials,setVerifiedSerials]=useState<readonly Serial[]>([]);
  const [download,setDownload]=useState<{url:string;expires_at:string}|null>(null);
  const epoch=useRef(0),active=useRef(false),inflight=useRef(false);
  const valid=(n:number)=>active.current&&epoch.current===n;
  function invalidate(){setPrepared(null);setConfirmed(false);}
  function local(n:number){try{const rows=store.list(identity.person_id).filter(p=>p.kind===stage);if(valid(n)){setRequests(rows);setStorageReady(true);}}
    catch(e){if(valid(n)){setStorageReady(false);setError(errorMessage(e));}}}
  async function work(run:(n:number)=>Promise<void>){if(inflight.current)return;inflight.current=true;const n=epoch.current;setBusy(true);setError('');
    try{await run(n);}catch(e){if(valid(n))setError(errorMessage(e));}finally{if(valid(n)){inflight.current=false;local(n);setBusy(false);}}}
  async function load(after?:string|null){await work(async n=>{
    invalidate();setSelected(null);setVerifiedSerials([]);setCanWrite(false);setPage(null);setDownload(null);setClosing(null);local(n);
    const c=await adapter.context(stage);if(c.person_id!==identity.person_id||c.authorization_version!==identity.authorization_version||!c.can_read)throw new Error('身份或查看权限已变化，请重新进入');
    if(valid(n))setCanWrite(c.can_write);
    const result=await adapter.list(stage,after);if(valid(n))setPage(result);
  });}
  useEffect(()=>{active.current=true;epoch.current++;inflight.current=false;setRequests([]);setNotice('');setReason('');setDecision('');setFiles([]);void load();
    return()=>{active.current=false;epoch.current++;};},[identity.person_id,identity.authorization_version,adapter,store,stage]);
  useEffect(()=>{const changed=()=>local(epoch.current);window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[identity.person_id,store,stage]);
  useEffect(()=>{if(!download)return;const delay=Math.max(0,Date.parse(download.expires_at)-Date.now());const t=setTimeout(()=>setDownload(null),delay);return()=>clearTimeout(t);},[download]);
  async function choose(s:Source){await work(async n=>{invalidate();setSelected(null);setVerifiedSerials([]);setReason('');setDecision('');setFiles([]);setDownload(null);
    const result=await adapter.read(stage,s.scrap_reference.scrap_line_id);if(valid(n)){setVerifiedSerials([]);setSelected(result);}
  });}
  const unresolved=!!selected&&requests.some(p=>'action' in p.original&&p.original.source.scrap_line_id===selected.scrap_reference.scrap_line_id);
  const physicalRequired=!!selected?.serials.length&&(stage==='apply'||stage==='execute'||stage==='regional'&&decision==='verified');
  const ready=!!selected?.next_reference&&canWrite&&storageReady&&!unresolved&&!uploading&&(!physicalRequired||verifiedSerials.length===selected?.serials.length)&&!!reason.trim()&&
    (stage==='apply'?files.length>0&&files.length<=20:stage==='regional'||stage==='headquarters'?!!decision:true);
  async function preview(){if(!ready||!selected)return;await work(async n=>{invalidate();const value=await adapter.prepare(stage,selected.scrap_reference.scrap_line_id,{reason:reason.trim(),decision,evidence:files.map(f=>f.file_id),verifiedSerials});if(valid(n))setPrepared(value);});}
  async function action(p:Pending,mode:'submit'|'recover'|'seal'){
    if(mode==='submit'&&(!confirmed||!ready))return;
    await work(async n=>{invalidate();setClosing(null);setNotice('');
      const r=await({submit,recover,seal}[mode])(adapter,store,p,()=>valid(n));if(!valid(n))return;
      setNotice(r.status==='pending'?'尚未查到原请求结果，已保留完整原请求。请继续回查，不可再次提交。':r.status==='sealed'?'原请求已永久封存，不能迟到执行；已发生的库存流水不会撤销。':p.kind==='execute'?'找回入库已核验：已恢复原冻结份额，尚未恢复可用。此结果是历史记账记录。':'原审批记录已核验，未改变库存。请根据最新状态继续下一阶段。');
      if(selected){const result=await adapter.read(stage,selected.scrap_reference.scrap_line_id);if(valid(n)){setVerifiedSerials([]);setSelected(result);}}
      const result=await adapter.list(stage);if(valid(n))setPage(result);
    });
  }
  async function evidence(file:string){if(!selected)return;await work(async n=>{setDownload(null);const result=await adapter.download(stage,selected.scrap_reference.scrap_line_id,file);if(valid(n))setDownload(result);});}
  return <section className="page-stack loss-execution-page scrap-recovery-page">
    <div className="page-heading"><div><h1>报废物资找回</h1><p>申请、区域核实、总部审批和找回入库分别办理。找回入库恢复原冻结份额，后续处置另行审批。</p></div><button disabled={busy||uploading} onClick={()=>void load()}>刷新</button></div>
    <nav aria-label="找回阶段">{stages.map(k=><button key={k} aria-pressed={stage===k} disabled={busy||uploading} onClick={()=>setStage(k)}>{stageLabels[k]}</button>)}</nav>
    {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}{busy&&<p role="status">正在核验，请稍候…</p>}
    {!storageReady&&<p role="alert">原请求存储不可用，已停止新提交。请保留浏览器数据。</p>}
    {!canWrite&&<p>当前仅可查看和回查原请求。</p>}
    {!!requests.length&&<section className="panel"><h2>{stageLabels[stage]}原请求待核验（{requests.length}）</h2><p>刷新或断网后先回查，查不到也不重发。</p>{requests.map(p=><div key={p.original.request_id}><span>请求 {p.original.request_id}</span><button disabled={busy} onClick={()=>void action(p,'recover')}>回查原请求</button><button disabled={busy||!canWrite} onClick={()=>setClosing(p)}>永久封存原请求</button></div>)}</section>}
    {closing&&<section role="alertdialog" aria-label="确认封存找回请求" className="panel"><h2>永久封存本次原请求？</h2><p>先核验是否已执行；未执行时阻止本次请求迟到执行。这不会撤销已有业务事实。</p><button disabled={busy||!canWrite} onClick={()=>void action(closing,'seal')}>确认永久封存</button><button disabled={busy} onClick={()=>setClosing(null)}>取消封存</button></section>}
    {page&&<section className="panel"><h2>{stageLabels[stage]}记录</h2>{!page.items.length&&<p>当前范围没有记录。</p>}{page.items.map(s=>s.availability==='blocked'?<p role="alert" key={s.scrap_line_id}>一条报废记录未能完整核验，请刷新或联系总部。该记录不可操作。</p>:<div key={s.scrap_reference.scrap_line_id}><p>{s.operation_no} · {s.material_name} · {s.quantity} {s.base_unit} · {s.requester_name} · {stateLabels[s.state]}</p><button disabled={busy||uploading} onClick={()=>void choose(s)}>查看 {s.operation_no} · {s.sku_code}</button></div>)}{page.next_after_id&&<button disabled={busy||uploading} onClick={()=>void load(page.next_after_id)}>下一页</button>}<button disabled={busy||uploading} onClick={()=>void load()}>返回首页记录</button></section>}
    {selected&&<section className="panel"><h2>{selected.operation_no} · {selected.material_name}</h2><p>{selected.sku_code} · {selected.quantity} {selected.base_unit} · {selected.requester_name}</p><p>{stateLabels[selected.state]}</p>
      {!!selected.serials.length&&<details><summary>查看本次 {selected.serials.length} 件序列号物资</summary><ul>{selected.serials.map(s=><li key={s.serial_id}>SN：{s.serial_no} · 二维码：{s.qr_code}</li>)}</ul></details>}
      {selected.applications.map((h,i)=><section key={h.application.fact_id}><h3>第 {i+1} 次找回申请</h3><p>申请说明：{h.application.reason}</p>{h.evidence_file_ids.map((f,i)=><button key={f} disabled={busy} onClick={()=>void evidence(f)}>查看证据 {i+1}</button>)}
        {h.regional_reviews.map(r=><p key={r.fact_id}>区域核实：{r.decision==='verified'?'已核实':'需补证据'} · {r.reason}</p>)}
        {h.headquarters_reviews.map(r=><p key={r.fact_id}>总部审批：{r.decision==='approve'?'批准找回（审批本身不入库）':'退回区域重审'} · {r.reason}</p>)}</section>)}
      {download&&<p><a href={download.url} target="_blank" rel="noopener noreferrer">打开已核验附件</a>（临时链接，过期后重新获取）</p>}
      {selected.recovery_posting&&<p>已恢复原冻结份额 {selected.recovery_posting.quantity} {selected.base_unit}；不表示当前库存可用。</p>}
      {!selected.next_reference?<p>本阶段没有可办理操作，请查看历史或切换阶段。</p>:unresolved?<p>本记录已有原请求待核验，请先回查。</p>:<fieldset disabled={busy||!canWrite||!storageReady}><legend>{stageLabels[stage]}</legend>
        {(stage==='regional'||stage==='headquarters')&&<label>处理意见<select aria-label="处理意见" value={decision} onChange={e=>{setDecision(e.target.value);setVerifiedSerials([]);invalidate();}}><option value="">请选择处理意见</option>{stage==='regional'?<><option value="verified">实物已核实</option><option value="needs_evidence">要求补充证据</option></>:<><option value="approve">批准找回</option><option value="request_regional_review">退回区域重审</option></>}</select></label>}
        <label>办理说明<textarea aria-label="办理说明" maxLength={500} value={reason} onChange={e=>{setReason(e.target.value);invalidate();}}/></label>
        {stage==='apply'&&<FormalFileUploadField purpose="stock_loss_evidence" bindingKey={`${identity.person_id}:${identity.authorization_version}:${selected.scrap_reference.scrap_line_id}`} label="找回实物证据" multiple client={uploadClient} disabled={busy||!canWrite||!storageReady}
          onAvailableChange={v=>{setFiles(v);invalidate();}} onBlockingChange={setUploading}/>}
        {physicalRequired&&<ScrapSerialCheck key={`${selected.scrap_reference.scrap_line_id}:${selected.queried_at}`} sku={selected.sku_code} serials={selected.serials} disabled={busy||!canWrite||!storageReady} onVerified={v=>{setVerifiedSerials(v);invalidate();}}/>}
        <button disabled={busy||!ready} onClick={()=>void preview()}>{stage==='execute'?'预览找回入库':'核对本次办理'}</button>
      </fieldset>}
    </section>}
    {prepared&&<section className="panel" aria-label="确认找回办理"><h2>确认{stageLabels[stage]}</h2><p>{prepared.source.operation_no} · {prepared.source.material_name} · {prepared.source.quantity} {prepared.source.base_unit}</p><p>{reason}</p>
      {stage==='regional'&&<p>{decision==='verified'?'实物已核实':'要求补充证据'}</p>}{stage==='headquarters'&&<p>{decision==='approve'?'批准找回':'退回区域重审'}</p>}
      <p>{prepared.preview?'将生成反向库存流水，恢复原冻结份额；不会直接变为可用库存。':'本次仅保存申请或审批记录，不改变库存。'}</p>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>已核对本次对象、证据和处理意见</label><button disabled={busy||!confirmed||!ready} onClick={()=>void action(prepared.pending,'submit')}>确认{stageLabels[stage]}</button><button disabled={busy} onClick={invalidate}>取消本次确认</button>
    </section>}
  </section>;
}
