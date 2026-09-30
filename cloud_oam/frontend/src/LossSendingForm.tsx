import {useState} from 'react';
import {fail,units} from './formalReturnReceiving';
import {input,type Kind,type Options} from './formalLossSenderCommands';
type Proof={serial_id:string;sku_code:string;serial_no:string;qr_code:string};
type Draft={quantity:string;serials:Record<string,Proof>};
const names={new:'新件',used:'旧件',damaged:'坏件'};
export default function LossSendingForm({kind,choices,disabled,onPrepare,onError}:{kind:Kind;choices:Options;disabled:boolean;onPrepare(value:ReturnType<typeof input>):void;onError(message:string):void}){
 const [drafts,setDrafts]=useState<Record<string,Draft>>({}),[reason,setReason]=useState(''),[at,setAt]=useState(''),[carrier,setCarrier]=useState(''),[tracking,setTracking]=useState('');
 const departure=kind==='outbound_return',label=departure?'出库':'交运';
 function useCurrentTime(){const d=new Date(),pad=(v:number)=>String(v).padStart(2,'0');setAt(`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`);}
 const update=(key:string,fn:(v:Draft)=>Draft)=>setDrafts(old=>({...old,[key]:fn(old[key]??{quantity:'',serials:{}})}));
 function prepare(event:React.FormEvent){
  event.preventDefault();if(disabled)return;
  try{
   const parts=/^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d)(?:\.(\d{1,3}))?)?$/.exec(at),date=new Date(at);
   if(!parts||!Number.isFinite(date.valueOf())||[date.getFullYear(),date.getMonth()+1,date.getDate(),date.getHours(),date.getMinutes(),date.getSeconds()].some((v,i)=>v!==Number(parts[i+1]??0)))fail('请填写实际发生时间');
   const lines=choices.lines.flatMap(row=>{
    const key='outbound_line_id'in row?row.outbound_line_id:row.operation_line_id,d=drafts[key];if(!d)return [];
    const tracked=['serial','lot_and_serial'].includes(row.tracking_mode),selected=Object.values(d.serials);
    if(tracked?!selected.length:!d.quantity.trim())return [];
    const quantity=tracked?String(selected.length):d.quantity;
    return [departure?{operation_line_id:row.operation_line_id,quantity,serial_verifications:selected}:{outbound_line_id:key,quantity,serial_ids:selected.map(s=>s.serial_id)}];
   });
   const body=input(kind,{operator_person_id:choices.person_id,reason,lines,...(departure?{outbound_at:new Date(at).toISOString()}:{shipped_at:new Date(at).toISOString(),carrier,tracking_no:tracking})},choices.person_id);
   onPrepare(body);
  }catch(e){onError(e instanceof Error?e.message:'发件内容未通过核验');}
 }
 return <form onSubmit={prepare} aria-label={`填写本次${label}`}><fieldset disabled={disabled}><legend>本次{label}</legend>
  <p>{departure?'记录实物从个人仓离开，库存转入物理在途；交给承运商后另行登记交运。':'仅从已出库明细选择本包裹，不再次扣减个人仓库存。'}</p>
  {choices.lines.map(row=>{
   const key='outbound_line_id'in row?row.outbound_line_id:row.operation_line_id,d=drafts[key]??{quantity:'',serials:{}},tracked=['serial','lot_and_serial'].includes(row.tracking_mode),available=units(row.selectable_quantity)>0n;
   return <fieldset key={key} disabled={!available}><legend>{row.sku_code} · {row.material_name}</legend><p>{names[row.condition_code]} · 本次最多 {row.selectable_quantity} {row.base_unit}{row.lot_no&&` · 批次 ${row.lot_no}`}</p>
    {'outbound_no'in row&&<p>来源出库单：{row.outbound_no} · 未交运 {row.unshipped_quantity} {row.base_unit}</p>}
    {!available?<p>当前没有可选数量。</p>:!tracked?<label>本次{label}数量<input aria-label={`${label}数量 ${key}`} inputMode="decimal" autoComplete="off" value={d.quantity} placeholder="留空表示本次不选" onChange={e=>update(key,old=>({...old,quantity:e.target.value}))}/></label>:<>
     {row.serials.map(sn=><label key={sn.serial_id}><input type="checkbox" aria-label={`选择 SN ${sn.serial_no}`} checked={!!d.serials[sn.serial_id]} onChange={e=>update(key,old=>{const serials={...old.serials};if(e.target.checked)serials[sn.serial_id]={serial_id:sn.serial_id,sku_code:'',serial_no:'',qr_code:''};else delete serials[sn.serial_id];return {...old,serials};})}/>{sn.serial_no}</label>)}
     {departure&&Object.values(d.serials).map(proof=>{
      const expected=row.serials.find(sn=>sn.serial_id===proof.serial_id)!.serial_no;
      return <fieldset key={proof.serial_id}><legend>实物核验：{expected}</legend><p>请扫码或按实物标签录入，查询结果不代填。</p>{(['sku_code','serial_no','qr_code']as const).map((field,i)=><label key={field}>{['实物物料码','实物 SN','实物二维码'][i]}<input required autoComplete="off" maxLength={[80,200,250][i]} aria-label={`${['实物物料码','实物 SN','实物二维码'][i]} ${expected}`} value={proof[field]} onKeyDown={e=>{if(e.key==='Enter')e.preventDefault();}} onChange={e=>update(key,old=>({...old,serials:{...old.serials,[proof.serial_id]:{...old.serials[proof.serial_id],[field]:e.target.value}}}))}/></label>)}</fieldset>;
     })}
    </>}
   </fieldset>;
  })}
  <label>实际{label}时间<input aria-label={`实际${label}时间`} type="datetime-local" step="1" required value={at} onChange={e=>setAt(e.target.value)}/></label><button type="button" onClick={useCurrentTime}>使用当前时间</button><p>请按真实发生时间填写；正在办理时可选择当前时间。</p>
  {!departure&&<><label>承运商<input aria-label="承运商" maxLength={100} required value={carrier} onChange={e=>setCarrier(e.target.value)}/></label><label>运单号<input aria-label="运单号" autoComplete="off" maxLength={100} required value={tracking} onChange={e=>setTracking(e.target.value)}/></label></>}
  <label>{label}说明<textarea aria-label={`${label}说明`} required maxLength={500} value={reason} onChange={e=>setReason(e.target.value)}/></label>
  <button type="submit" disabled={disabled||!choices.lines.some(r=>units(r.selectable_quantity)>0n)}>核验并预览本次{label}</button>
 </fieldset></form>;
}
