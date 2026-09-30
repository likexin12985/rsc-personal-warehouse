import { useState } from 'react';
import FormalFileUploadField, { type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import { fail, units, type History } from './formalReturnReceiving';
import { input, type Input } from './formalReturnReceipt';

type Choice = '' | 'accepted' | 'rejected' | 'shortage';
type ExceptionType = 'shortage' | 'damaged' | 'rejected' | 'wrong_material' | 'wrong_serial';
type Evidence = { description: string; file?: AvailableFormalFile };
type SerialDraft = { choice: Choice; damaged: boolean; sku: string; sn: string; qr: string };
type LineDraft = { accepted: string; rejected: string; shortage: string; damaged: string; rejection: 'rejected' | 'wrong_material' | 'wrong_serial'; serials: Record<string, SerialDraft>; evidence: Partial<Record<ExceptionType, Evidence>> };
const emptyLine = (): LineDraft => ({ accepted: '', rejected: '', shortage: '', damaged: '', rejection: 'rejected', serials: {}, evidence: {} });
const emptySerial = (): SerialDraft => ({ choice: '', damaged: false, sku: '', sn: '', qr: '' });
const names = { shortage: '短少', damaged: '破损', rejected: '拒收', wrong_material: '错料', wrong_serial: '错 SN' };
export function decimal(value: string): string {
  if (!/^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?$/.test(value)) fail('数量须为非负数，最多三位小数');
  const [whole, fraction = ''] = value.split('.'); return `${whole}.${fraction.padEnd(3, '0')}`;
}
const nonzero = (value: string) => { try { return units(decimal(value || '0')) > 0n; } catch { return false; } };
function activeTypes(d: LineDraft, tracked: boolean): ExceptionType[] {
  const serials = Object.values(d.serials);
  return [...((tracked ? serials.some(s => s.choice === 'shortage') : nonzero(d.shortage)) ? ['shortage' as const] : []),
    ...((tracked ? serials.some(s => s.choice === 'accepted' && s.damaged) : nonzero(d.damaged)) ? ['damaged' as const] : []),
    ...((tracked ? serials.some(s => s.choice === 'rejected') : nonzero(d.rejected)) ? [d.rejection] : [])];
}
function localNow() { const now = new Date(); return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 19); }

export default function ReturnReceiptForm({ current, disabled, uploader, onPrepare, onError }: {
  current: History; disabled: boolean; uploader: FormalFileUploadClient;
  onPrepare(body: Input): void; onError(message: string): void;
}) {
  const [drafts, setDrafts] = useState<Record<string, LineDraft>>({}), [reason, setReason] = useState(''), [receivedAt, setReceivedAt] = useState(localNow);
  const [uploads, setUploads] = useState<Record<string, boolean>>({});
  const uploadKey = (line: string, type: ExceptionType) => `${current.person_id}:${current.authorization_version}:${current.package.shipment_id}:${line}:${type}`;
  const uploading = current.package.lines.some(line => activeTypes(drafts[line.shipment_line_id] ?? emptyLine(), line.serials.length > 0).some(type => uploads[uploadKey(line.shipment_line_id, type)]));
  const update = (key: string, change: (draft: LineDraft) => LineDraft) => setDrafts(old => ({ ...old, [key]: change(old[key] ?? emptyLine()) }));
  const evidence = (key: string, type: ExceptionType, change: (value: Evidence) => Evidence) => update(key, d => ({ ...d, evidence: { ...d.evidence, [type]: change(d.evidence[type] ?? { description: '' }) } }));
  function prepare(event: React.FormEvent) {
    event.preventDefault(); if (disabled || uploading) return;
    try {
      const lines: Input['lines'] = [];
      for (const original of current.package.lines) {
        const d = drafts[original.shipment_line_id]; if (!d) continue;
        const tracked = original.serials.length > 0;
        const entries = Object.entries(d.serials), accepted = entries.filter(([, s]) => s.choice === 'accepted'), rejected = entries.filter(([, s]) => s.choice === 'rejected'), shortage = entries.filter(([, s]) => s.choice === 'shortage');
        const accepted_qty = tracked ? `${accepted.length}.000` : decimal(d.accepted || '0'), rejected_qty = tracked ? `${rejected.length}.000` : decimal(d.rejected || '0'), shortage_qty = tracked ? `${shortage.length}.000` : decimal(d.shortage || '0');
        const damaged_qty = tracked ? `${accepted.filter(([, s]) => s.damaged).length}.000` : decimal(d.damaged || '0');
        if (units(accepted_qty) + units(rejected_qty) + units(shortage_qty) === 0n) { if (units(damaged_qty)) fail('破损数量必须属于本次接受数量'); continue; }
        const types: ExceptionType[] = [...(units(shortage_qty) ? ['shortage' as const] : []), ...(units(damaged_qty) ? ['damaged' as const] : []), ...(units(rejected_qty) ? [d.rejection] : [])];
        lines.push({ shipment_line_id: original.shipment_line_id, accepted_qty, rejected_qty, shortage_qty, damaged_qty,
          accepted_serial_verifications: accepted.map(([serial_id, s]) => ({ serial_id, sku_code: s.sku, serial_no: s.sn, qr_code: s.qr })),
          damaged_serial_ids: accepted.filter(([, s]) => s.damaged).map(([sn]) => sn), rejected_serial_ids: rejected.map(([sn]) => sn), shortage_serial_ids: shortage.map(([sn]) => sn),
          exceptions: types.map(type => { const e = d.evidence[type]; if (!e?.file || e.file.status !== 'available' || e.file.purpose !== 'receipt_exception_evidence') fail(`${names[type]}需要上传并确认异常凭证`); return { exception_type: type, description: e.description, evidence_file_id: e.file.file_id }; }),
        });
      }
      if (!receivedAt || !Number.isFinite(new Date(receivedAt).getTime())) fail('请填写实际验收时间');
      onPrepare(input({ operator_person_id: current.person_id, received_at: new Date(receivedAt).toISOString(), reason, lines }, current.person_id));
    } catch (e) { onError(e instanceof Error ? e.message : '验收内容无效'); }
  }
  return <form onSubmit={prepare} aria-label="填写本次验收"><fieldset disabled={disabled}><legend>本次实物验收</legend>
    <p>仅填写本次实际验收的数量或 SN。破损包含在接受数量内；短少仍待后续确认。</p>
    <label>实际验收时间<input aria-label="实际验收时间" type="datetime-local" step="1" required value={receivedAt} onChange={e => setReceivedAt(e.target.value)} /></label>
    <label>验收说明<textarea aria-label="验收说明" required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
    {current.package.lines.map(original => {
      const key = original.shipment_line_id, remaining = current.lines.find(l => l.shipment_line_id === key)!, d = drafts[key] ?? emptyLine();
      if (units(remaining.unconfirmed_qty) === 0n) return null;
      const tracked = original.serials.length > 0, selected = Object.values(d.serials), rejected = tracked ? selected.some(s => s.choice === 'rejected') : nonzero(d.rejected);
      const exceptionTypes: ExceptionType[] = [...((tracked ? selected.some(s => s.choice === 'shortage') : nonzero(d.shortage)) ? ['shortage' as const] : []), ...((tracked ? selected.some(s => s.choice === 'accepted' && s.damaged) : nonzero(d.damaged)) ? ['damaged' as const] : []), ...(rejected ? [d.rejection] : [])];
      return <fieldset key={key}><legend>{original.sku_code} · {original.material_name}</legend><p>尚未确认 {remaining.unconfirmed_qty} {original.base_unit}</p>
        {!tracked ? <div className="form-grid">{([['accepted', '接受数量'], ['rejected', '拒收数量'], ['shortage', '短少数量'], ['damaged', '接受中破损数量']] as const).map(([field, label]) => <label key={field}>{label}<input inputMode="decimal" aria-label={`${label} ${original.sku_code}`} value={d[field]} placeholder="0" onChange={e => update(key, old => ({ ...old, [field]: e.target.value }))} /></label>)}</div> : remaining.unconfirmed_serials.map(sn => {
          const value = d.serials[sn.serial_id] ?? emptySerial();
          const change = (patch: Partial<SerialDraft>) => update(key, old => ({ ...old, serials: { ...old.serials, [sn.serial_id]: { ...(old.serials[sn.serial_id] ?? emptySerial()), ...patch } } }));
          return <fieldset key={sn.serial_id}><legend>SN {sn.serial_no}</legend><label>本次结果<select aria-label={`本次结果 ${sn.serial_no}`} value={value.choice} onChange={e => change({ choice: e.target.value as Choice, damaged: false })}><option value="">暂不确认</option><option value="accepted">接受</option><option value="rejected">拒收</option><option value="shortage">短少</option></select></label>
            {value.choice === 'accepted' && <><p>请用扫码枪或按实物标签录入三项内容，提交时由服务端核验。</p><div className="form-grid">{([['sku', '实物物料码'], ['sn', '实物 SN'], ['qr', '实物二维码']] as const).map(([field, label]) => <label key={field}>{label}<input required autoComplete="off" aria-label={`${label} ${sn.serial_no}`} value={value[field]} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} onChange={e => change({ [field]: e.target.value })} /></label>)}</div>
              <label><input type="checkbox" checked={value.damaged} onChange={e => change({ damaged: e.target.checked })} />该接受件有破损</label></>}
          </fieldset>;
        })}
        {rejected && <label>拒收原因类型<select aria-label={`拒收原因类型 ${original.sku_code}`} value={d.rejection} onChange={e => update(key, old => ({ ...old, rejection: e.target.value as LineDraft['rejection'] }))}><option value="rejected">拒收</option><option value="wrong_material">错料</option><option value="wrong_serial">错 SN</option></select></label>}
        {exceptionTypes.map(type => {
          const binding = uploadKey(key, type);
          return <fieldset key={type}><legend>{names[type]}凭证</legend><label>{names[type]}说明<textarea required maxLength={1000} aria-label={`${names[type]}说明 ${original.sku_code}`} value={d.evidence[type]?.description ?? ''} onChange={e => evidence(key, type, old => ({ ...old, description: e.target.value }))} /></label>
            <FormalFileUploadField purpose="receipt_exception_evidence" bindingKey={binding} label={`上传${names[type]}凭证 ${original.sku_code}`} disabled={disabled} client={uploader}
              onAvailableChange={files => evidence(key, type, old => ({ ...old, file: files[0] }))} onBlockingChange={blocked => setUploads(old => ({ ...old, [binding]: blocked }))} />
          </fieldset>;
        })}
      </fieldset>;
    })}
    <button type="submit" disabled={disabled || uploading}>核验并预览本次验收</button>
  </fieldset></form>;
}
