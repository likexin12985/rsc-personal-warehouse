import { useState } from 'react';
import type { Serial } from './scrapRecoverySources';
/** Local physical check only. Server still derives immutable stock/SN bindings. */
export default function ScrapSerialCheck({sku,serials,disabled,onVerified}:{sku:string;serials:readonly Serial[];disabled:boolean;onVerified:(value:readonly Serial[])=>void}){
  const [material,setMaterial]=useState(''),[mode,setMode]=useState<'serial_no'|'qr_code'>('serial_no'),[value,setValue]=useState('');
  const [checked,setChecked]=useState<string[]>([]),[notice,setNotice]=useState('');
  function scan(){
    if(disabled||material.trim()!==sku)return;
    const input=value.trim(),matches=serials.filter(s=>s[mode]===input);
    if(!input||matches.length!==1){setNotice('输入与本次报废的实物标识不匹配，未计入核对。');return;}
    const serial=matches[0];if(checked.includes(serial.serial_id)){setNotice('这件物资已经核对，不能重复计数。');setValue('');return;}
    const next=[...checked,serial.serial_id];setChecked(next);setValue('');setNotice(`已核对 ${serial.serial_no}`);
    onVerified(next.length===serials.length?[...serials]:[]);
  }
  function clear(){setChecked([]);setValue('');setNotice('');onVerified([]);}
  return <fieldset disabled={disabled} className="panel"><legend>逐件实物核对</legend>
    <p>先核对物料号，再用扫码枪输入或手工录入每件实物的 SN 或二维码。大小写必须一致。</p>
    <label>实物物料号<input aria-label="实物物料号" maxLength={200} value={material} onChange={e=>{setMaterial(e.target.value);clear();}}/></label>
    <label>标识类型<select aria-label="标识类型" value={mode} onChange={e=>{setMode(e.target.value as 'serial_no'|'qr_code');setValue('');setNotice('');}}><option value="serial_no">SN</option><option value="qr_code">二维码</option></select></label>
    <label>实物标识<input aria-label="实物标识" maxLength={250} value={value} onChange={e=>setValue(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();scan();}}}/></label>
    <button type="button" disabled={disabled||material.trim()!==sku||!value.trim()} onClick={scan}>核对这件物资</button><button type="button" disabled={disabled||!checked.length} onClick={clear}>重新核对全部</button>
    <p role="status">已核对 {checked.length} / {serials.length} 件</p>{notice&&<p role="status">{notice}</p>}
    <ul>{serials.map(s=><li key={s.serial_id}>SN：{s.serial_no} · 二维码：{s.qr_code} · {checked.includes(s.serial_id)?'已核对':'待核对'}</li>)}</ul>
  </fieldset>;
}
