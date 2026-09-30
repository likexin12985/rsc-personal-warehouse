import { useEffect, useMemo, useRef, useState } from 'react';
import { prepare, type Blocked, type Decision, type Disposition, type Evidence, type Identity, type Pending, type Report, type Stage } from './formalLossReview';
import { browserStore, recover, seal, submit, type Store } from './lossReviewRecovery';
import type { Adapter } from './lossReviewAdapter';
const names={regional:'区域核实',headquarters:'总部终审'};
const statuses={awaiting_regional:'待区域核实',awaiting_headquarters:'待总部终审',approved:'已终审，待处置'};
const dispositionOptions:[Disposition,string][]=[['restore_available','恢复可用'],['convert_used','转旧件'],['convert_damaged','转坏件'],['return_to_region','退回区域仓'],['scrap','报废']];
const errorMessage=(cause:unknown)=>cause instanceof Error?cause.message:'操作未确认，请保留原请求并重新核验';
type Props={identity:Identity;stages:Stage[];adapter:Adapter;store?:Store};
export default function FormalLossReviews({identity,stages,adapter,store:provided}:Props){
  const store=useMemo(()=>provided??browserStore(),[provided]);
  const [stage,setStage]=useState<Stage>(stages[0]??'regional'),[view,setView]=useState<'pending'|'all'>('pending');
  const [items,setItems]=useState<(Report|Blocked)[]>([]),[next,setNext]=useState<string|null>(null);
  const [selected,setSelected]=useState<Report|null>(null),[pending,setPending]=useState<Pending[]>([]);
  const [comment,setComment]=useState(''),[decisions,setDecisions]=useState<Record<string,{disposition:''|Disposition;reason:string}>>({});
  const [busy,setBusy]=useState(''),[loading,setLoading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [canWrite,setCanWrite]=useState(false),[storageReady,setStorageReady]=useState(false),[confirmed,setConfirmed]=useState(false);
  const [sealTarget,setSealTarget]=useState<Pending|null>(null),[photo,setPhoto]=useState<{file:Evidence;url:string;expires_at:string}|null>(null);
  const alive=useRef(true),readEpoch=useRef(0),detailEpoch=useRef(0),stageRef=useRef(stage);stageRef.current=stage;
  const valid=()=>alive.current&&stageRef.current===stage;
  function localRequests(){try{const records=store.list(identity.person_id);if(alive.current){setPending(records);setStorageReady(true);}return records;}catch(cause){if(alive.current){setStorageReady(false);setError(errorMessage(cause));}return [];}}
  async function load(after:string|null=null){
    const epoch=++readEpoch.current;setLoading(true);setError('');setPhoto(null);localRequests();
    try{const access=await adapter.context(stage);const page=await adapter.list(stage,view,after);
      if(!valid()||epoch!==readEpoch.current)return;
      setCanWrite(access.can_write);setItems(old=>after?[...old.filter(x=>!page.items.some(y=>y.operation_id===x.operation_id)),...page.items]:page.items);setNext(page.next_after_id);
    }catch(cause){if(valid()&&epoch===readEpoch.current){setError(errorMessage(cause));if(!after){setItems([]);setNext(null);}setCanWrite(false);}}
    finally{if(valid()&&epoch===readEpoch.current)setLoading(false);}
  }
  useEffect(()=>{alive.current=true;setSelected(null);setSealTarget(null);setComment('');setDecisions({});setPhoto(null);setConfirmed(false);void load();return()=>{alive.current=false;readEpoch.current++;detailEpoch.current++;};},[stage,view,adapter,store,identity.person_id,identity.authorization_version]);
  useEffect(()=>{if(!photo)return;const timer=window.setTimeout(()=>setPhoto(null),Math.max(0,Date.parse(photo.expires_at)-Date.now()));return()=>window.clearTimeout(timer);},[photo]);
  async function open(operation:string){
    const epoch=++detailEpoch.current;setBusy('detail');setError('');setSelected(null);setPhoto(null);setConfirmed(false);setComment('');setDecisions({});
    try{const data=await adapter.read(stage,operation);if(valid()&&epoch===detailEpoch.current)setSelected(data);}
    catch(cause){if(valid()&&epoch===detailEpoch.current)setError(errorMessage(cause));}
    finally{if(valid()&&epoch===detailEpoch.current)setBusy('');}
  }
  async function resolve(value:Pending,mode:'recover'|'seal'){
    setBusy(mode);setError('');setNotice('');setPhoto(null);
    const originalStage=value.command.stage;const current=()=>alive.current&&stageRef.current===originalStage;
    try{const outcome=await(mode==='seal'?seal:recover)(adapter,store,value,current);if(!current())return;
      setSealTarget(null);setNotice(outcome.status==='pending'?'尚未查到原请求结果。已保留原请求，不能据此再次提交。':outcome.status==='sealed'?'本次原审批请求已永久封存。':'原审批结果已核验。');localRequests();await load();
      if(selected?.operation_id===value.command.original.operation_id)await open(selected.operation_id);
    }catch(cause){if(current()){setError(errorMessage(cause));localRequests();}}
    finally{if(current())setBusy('');}
  }
  async function approve(event:React.FormEvent){
    event.preventDefault();if(!selected||!confirmed||!storageReady||busy)return;
    setBusy('submit');setError('');setNotice('');setPhoto(null);
    const current=()=>alive.current&&stageRef.current===stage;
    try{const rows:Decision[]=selected.lines.map(line=>({line_id:line.line_id,disposition:decisions[line.line_id]?.disposition as Disposition,reason:decisions[line.line_id]?.reason??''}));
      const value=await prepare(identity,stage,selected,comment,stage==='headquarters'?rows:undefined);
      const outcome=await submit(adapter,store,value,current);if(!current())return;
      setNotice(outcome.status==='found'?(stage==='regional'?'区域核实已记录，等待总部终审。':'总部终审已记录，仍需执行后续处置。'):'结果尚未确认，请回查原请求。');setConfirmed(false);localRequests();await load();await open(selected.operation_id);
    }catch(cause){if(current()){setError(errorMessage(cause));localRequests();}}
    finally{if(current())setBusy('');}
  }
  async function viewPhoto(file:Evidence){
    setBusy('photo');setError('');setPhoto(null);const operation=selected?.operation_id,epoch=detailEpoch.current;
    try{const link=await adapter.download(stage,file);if(valid()&&epoch===detailEpoch.current&&operation)setPhoto({file,...link});}
    catch(cause){if(valid())setError(errorMessage(cause));}finally{if(valid())setBusy('');}
  }
  const ownPending=pending.filter(p=>p.command.stage===stage);
  const unresolved=selected&&ownPending.some(p=>p.command.original.operation_id===selected.operation_id);
  const eligible=selected?.approval_stage===(stage==='regional'?'awaiting_regional':'awaiting_headquarters');
  return <section className="page-stack loss-review-page">
    <div className="page-heading"><div><h1>报损审批</h1><p>核对原单、物料和照片，分别记录区域核实与总部终审。审批不改变库存。</p></div></div>
    <div className="filter-bar"><label>审批阶段 <select aria-label="审批阶段" value={stage} disabled={!!busy} onChange={e=>setStage(e.target.value as Stage)}>{stages.map(s=><option value={s} key={s}>{names[s]}</option>)}</select></label>
      <label>查看范围 <select aria-label="查看范围" value={view} disabled={!!busy} onChange={e=>setView(e.target.value as 'pending'|'all')}><option value="pending">待处理</option><option value="all">全部记录</option></select></label>
      <button className="button" disabled={loading||!!busy} onClick={()=>void load()}>刷新</button></div>
    {error&&<div className="alert alert-error" role="alert">{error}</div>}{notice&&<div className="alert" role="status">{notice}</div>}
    {!storageReady&&<p role="alert">本机恢复记录不可读，已停止新审批，请保留浏览器数据。</p>}
    {!!ownPending.length&&<section className="panel"><h2>原请求待核验（{ownPending.length}）</h2><p>网络错误或离开页面后，先回查原请求，不会自动重发。</p>
      {ownPending.map(p=><div key={p.command.original.operation_id} className="toolbar"><span>原单标识 {p.command.original.operation_id}</span>
        <button disabled={!!busy} onClick={()=>void open(p.command.original.operation_id)}>查看原单</button>
        <button disabled={!!busy} onClick={()=>void resolve(p,'recover')}>回查原请求</button>
        <button disabled={!!busy||!canWrite} onClick={()=>setSealTarget(p)}>永久封存原请求</button></div>)}
    </section>}
    {sealTarget&&<section className="panel" role="alertdialog" aria-label="确认封存原请求"><h2>永久封存本次原请求？</h2><p>先查询是否已经审批；若尚未执行，则永久阻止本次原请求迟到执行。这不会撤销已有审批。</p><button disabled={!!busy} onClick={()=>void resolve(sealTarget,'seal')}>确认永久封存</button><button disabled={!!busy} onClick={()=>setSealTarget(null)}>取消</button></section>}
    <section className="panel"><h2>{names[stage]} · {view==='pending'?'待处理':'全部记录'}</h2>
      {loading&&<p role="status">正在核验审批记录…</p>}
      {!loading&&!items.length&&<p>{next?'本页没有待处理单据，可继续查看下一页。':'当前范围没有记录。'}</p>}
      <div className="table-scroll"><table><thead><tr><th>报损单</th><th>申请人 / 区域</th><th>审批状态</th><th>操作</th></tr></thead><tbody>
        {items.map(row=><tr key={row.operation_id}>{row.availability==='blocked'?<><td>记录待核验</td><td>—</td><td>原单证据不完整</td><td><button disabled={!!busy} onClick={()=>void open(row.operation_id)}>重新核验</button></td></>:<><td>{row.operation_no}</td><td>{row.requester_name} / {row.owner_org_name}</td><td>{statuses[row.approval_stage]}</td><td><button disabled={!!busy} onClick={()=>void open(row.operation_id)}>查看详情</button></td></>}</tr>)}
      </tbody></table></div>{next&&<button disabled={loading||!!busy} onClick={()=>void load(next)}>下一页</button>}
    </section>
    {selected&&<section className="panel" aria-label="报损详情"><h2>{selected.operation_no}</h2><p>{selected.requester_name} · {selected.owner_org_name} · {selected.source_location_name}</p><p>提交时间：{new Date(selected.submitted_at).toLocaleString('zh-CN')}</p><p>报损原因：{selected.reason}</p><p>审批状态：{statuses[selected.approval_stage]}</p>
      <h3>原报损明细</h3><ul>{selected.lines.map(line=><li key={line.line_id}>{line.sku_code} · {line.material_name} · {line.quantity} {line.base_unit} · {{new:'新件',used:'旧件',damaged:'坏件'}[line.condition_code]}{line.lot_no&&` · 批次 ${line.lot_no}`}{!!line.serials.length&&<p>SN：{line.serials.map(s=>s.serial_no).join('、')}</p>}</li>)}</ul>
      <h3>原单照片与附件</h3>{selected.evidence.map(file=><button key={file.file_id} disabled={!!busy} onClick={()=>void viewPhoto(file)}>{file.original_filename}</button>)}
      {photo&&<p><a href={photo.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">打开 {photo.file.original_filename}</a>（临时链接，失效后重新申请）</p>}
      {selected.regional_review&&<p>区域核实意见：{selected.regional_review.comment}</p>}
      {selected.headquarters_review&&<><p>总部终审意见：{selected.headquarters_review.comment}</p><ul>{selected.headquarters_review.decisions.map(d=><li key={d.line_id}>{selected.lines.find(l=>l.line_id===d.line_id)?.material_name}：{dispositionOptions.find(([v])=>v===d.disposition)?.[1]}，{d.reason}</li>)}</ul></>}
      {eligible&&canWrite&&!unresolved&&<form onSubmit={event=>void approve(event)}><fieldset disabled={!!busy||!storageReady}><legend>{names[stage]}</legend>
        <label>审批意见<textarea required maxLength={1000} value={comment} onChange={e=>{setComment(e.target.value);setConfirmed(false);}} /></label>
        {stage==='headquarters'&&selected.lines.map(line=><fieldset key={line.line_id}><legend>{line.sku_code} · {line.material_name}</legend>
          <label>处置决定<select required aria-label={`处置决定 ${line.sku_code}`} value={decisions[line.line_id]?.disposition??''} onChange={e=>{setDecisions(old=>({...old,[line.line_id]:{reason:old[line.line_id]?.reason??'',disposition:e.target.value as Disposition}}));setConfirmed(false);}}><option value="">请选择</option>{dispositionOptions.map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label>
          <label>逐行理由<textarea required maxLength={500} aria-label={`逐行理由 ${line.sku_code}`} value={decisions[line.line_id]?.reason??''} onChange={e=>{setDecisions(old=>({...old,[line.line_id]:{disposition:old[line.line_id]?.disposition??'',reason:e.target.value}}));setConfirmed(false);}} /></label>
        </fieldset>)}
        <label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已核对原单、明细和证据，确认提交以上意见</label>
        <button type="submit" disabled={!confirmed||!!busy||!storageReady}>确认提交{names[stage]}</button>
      </fieldset></form>}
      {unresolved&&<p>此单已有原请求待核验，请使用上方回查或封存入口。</p>}
      {eligible&&!canWrite&&<p>当前仅可查看，审批写权限未开通。</p>}
    </section>}
  </section>;
}
