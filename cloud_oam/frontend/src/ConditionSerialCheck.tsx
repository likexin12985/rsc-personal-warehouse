import { useState } from 'react';
import type { Scan } from './returnConditionCommands';
type Serial = { serial_id: string; serial_no: string; qr_code: string };
export default function ConditionSerialCheck({ sku, serials, disabled, onVerified }: {
  sku: string; serials: readonly Serial[]; disabled: boolean; onVerified(value: Scan[]): void;
}) {
  const [material, setMaterial] = useState(''), [mode, setMode] = useState<'serial_no' | 'qr_code'>('serial_no');
  const [input, setInput] = useState(''), [checked, setChecked] = useState<string[]>([]), [notice, setNotice] = useState('');
  function scan() {
    if (disabled || material.trim() !== sku) return;
    const matches = serials.filter(s => s[mode] === input.trim());
    if (!input.trim() || matches.length !== 1 || checked.includes(matches[0].serial_id)) {
      setNotice('标识不匹配、不唯一或已核对，本次未计数。'); return;
    }
    const next = [...checked, matches[0].serial_id]; setChecked(next); setInput(''); setNotice('已核对 ' + matches[0].serial_no);
    onVerified(next.length === serials.length ? serials.map(s => ({ ...s, sku_code: material.trim() })) : []);
  }
  return <fieldset disabled={disabled}><legend>逐件实物核验</legend>
    <p>填写实物物料号，再扫描或录入每件实物的 SN 或二维码。显示的编号不会自动计入核验。</p>
    <label>实物物料号<input aria-label="实物物料号" value={material} maxLength={80} onChange={e => {
      setMaterial(e.target.value); setChecked([]); setInput(''); setNotice(''); onVerified([]);
    }} /></label>
    <label>标识类型<select aria-label="标识类型" value={mode} onChange={e => { setMode(e.target.value as typeof mode); setInput(''); }}>
      <option value="serial_no">SN</option><option value="qr_code">二维码</option></select></label>
    <label>实物标识<input aria-label="实物标识" value={input} maxLength={250} onChange={e => setInput(e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); scan(); } }} /></label>
    <button type="button" disabled={disabled || material.trim() !== sku || !input.trim()} onClick={scan}>核对这件物资</button>
    <button type="button" disabled={disabled || !checked.length} onClick={() => { setChecked([]); setInput(''); setNotice(''); onVerified([]); }}>重新核对全部</button>
    <p role="status">已核对 {checked.length} / {serials.length} 件</p>{notice && <p role="status">{notice}</p>}
    <ul>{serials.map(s => <li key={s.serial_id}>SN：{s.serial_no} · {checked.includes(s.serial_id) ? '已核对' : '待核对'}</li>)}</ul>
  </fieldset>;
}
