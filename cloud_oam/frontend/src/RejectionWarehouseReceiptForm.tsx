import { useState } from 'react';
import FormalFileUploadField, { defaultFormalFileUploadClient, type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import { decimal } from './ReturnReceiptForm';
import { fail, units } from './formalReturnReceiving';
import { checkSelection, receiptInput, type Detail, type ReceiptInput, type Amounts } from './rejectionWarehouse';

type Selection = { choice: '' | 'accepted' | 'rejected' | 'shortage'; damaged: boolean; sku: string; sn: string; qr: string };
const empty = (): Selection => ({ choice: '', damaged: false, sku: '', sn: '', qr: '' });
type Exception = Amounts['exceptions'][number]['exception_type'];
const names = { shortage: '短少', damaged: '破损', rejected: '拒收', wrong_material: '错料', wrong_serial: '错 SN' };
function now() { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19); }
export default function RejectionWarehouseReceiptForm({ current, disabled, onPrepare, onError, uploader = defaultFormalFileUploadClient }: {
  current: Detail; disabled: boolean; onPrepare(input: ReceiptInput): void; onError(message: string): void; uploader?: FormalFileUploadClient;
}) {
  const [qty, setQty] = useState({ accepted: '', rejected: '', shortage: '', damaged: '' });
  const [selected, setSelected] = useState<Record<string, Selection>>({});
  const [rejection, setRejection] = useState<'rejected' | 'wrong_material' | 'wrong_serial'>('rejected');
  const [evidence, setEvidence] = useState<Partial<Record<Exception, { description: string; file?: AvailableFormalFile }>>>({});
  const [uploads, setUploads] = useState<Record<string, boolean>>({});
  const [reason, setReason] = useState(''), [sku, setSku] = useState(''), [receivedAt, setReceivedAt] = useState(now);
  const s = current.source, tracked = s.serials.length > 0, entries = Object.entries(selected);
  const accepted = entries.filter(([, v]) => v.choice === 'accepted'), rejected = entries.filter(([, v]) => v.choice === 'rejected'), shortage = entries.filter(([, v]) => v.choice === 'shortage');
  function positive(value: string) { try { return units(decimal(value || '0')) > 0n; } catch { return false; } }
  const types: Exception[] = [...((tracked ? shortage.length > 0 : positive(qty.shortage)) ? ['shortage' as const] : []),
    ...((tracked ? accepted.some(([, v]) => v.damaged) : positive(qty.damaged)) ? ['damaged' as const] : []),
    ...((tracked ? rejected.length > 0 : positive(qty.rejected)) ? [rejection] : [])];
  const uploading = types.some(t => uploads[t]);
  function prepare(event: React.FormEvent) {
    event.preventDefault(); if (disabled || uploading) return;
    try {
      if (!receivedAt || !Number.isFinite(new Date(receivedAt).getTime())) fail('请填写实际验收时间');
      const amounts: Amounts = {
        accepted_qty: tracked ? `${accepted.length}.000` : decimal(qty.accepted || '0'),
        rejected_qty: tracked ? `${rejected.length}.000` : decimal(qty.rejected || '0'),
        shortage_qty: tracked ? `${shortage.length}.000` : decimal(qty.shortage || '0'),
        damaged_qty: tracked ? `${accepted.filter(([, v]) => v.damaged).length}.000` : decimal(qty.damaged || '0'),
        accepted_serial_verifications: accepted.map(([serial_id, v]) => ({ serial_id, sku_code: v.sku, serial_no: v.sn, qr_code: v.qr })),
        rejected_serial_ids: rejected.map(([sid]) => sid), shortage_serial_ids: shortage.map(([sid]) => sid),
        damaged_serial_ids: accepted.filter(([, v]) => v.damaged).map(([sid]) => sid),
        exceptions: types.map(t => { const e = evidence[t];
          if (!e?.file || e.file.status !== 'available' || e.file.purpose !== 'receipt_exception_evidence') fail(`${names[t]}需要已确认的异常凭证`);
          return { exception_type: t, description: e.description, evidence_file_id: e.file.file_id };
        }),
      };
      const input = receiptInput({ expected_request_version: s.request_version, reason: reason.trim(),
        registration_request_hash: s.registration_request_hash, handover_id: s.handover_id, handover_request_hash: s.handover_request_hash,
        custody_assignment_id: s.custody_assignment_id, received_at: new Date(receivedAt).toISOString(), amounts,
        observed_sku_code: units(amounts.accepted_qty) ? sku : null });
      checkSelection(input, current); onPrepare(input);
    } catch (error) { onError(error instanceof Error ? error.message : '验收内容未通过核验'); }
  }
  return <form onSubmit={prepare} aria-label="拒收退回实物验收"><fieldset disabled={disabled}><legend>登记本次验收</legend>
    <p>仅登记本次实际确认的实物。破损属于接受数量；短少仍待后续确认，验收后还需独立入账。</p>
    <label>实际验收时间<input type="datetime-local" step="1" required value={receivedAt} onChange={e => setReceivedAt(e.target.value)} /></label>
    <label>验收说明<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
    {!tracked ? <div className="form-grid">{([['accepted', '接受数量'], ['rejected', '拒收数量'], ['shortage', '短少数量'], ['damaged', '接受中破损数量']] as const).map(([field, label]) =>
      <label key={field}>{label}<input inputMode="decimal" placeholder="0" value={qty[field]} onChange={e => setQty(old => ({ ...old, [field]: e.target.value }))} /></label>)}</div>
      : current.unconfirmed_serials.map(sn => {
        const value = selected[sn.serial_id] ?? empty();
        const change = (patch: Partial<Selection>) => setSelected(old => ({ ...old, [sn.serial_id]: { ...(old[sn.serial_id] ?? empty()), ...patch } }));
        return <fieldset key={sn.serial_id}><legend>SN {sn.serial_no}</legend><label>本次结果<select aria-label={`本次结果 ${sn.serial_no}`} value={value.choice} onChange={e => change({ choice: e.target.value as Selection['choice'], damaged: false })}>
          <option value="">暂不确认</option><option value="accepted">接受</option><option value="rejected">拒收</option><option value="shortage">短少</option></select></label>
          {value.choice === 'accepted' && <><p>请扫描或按实物标签录入。</p>{([['sku', '实物物料码'], ['sn', '实物 SN'], ['qr', '实物二维码']] as const).map(([field, label]) =>
            <label key={field}>{label}<input required autoComplete="off" aria-label={`${label} ${sn.serial_no}`} value={value[field]} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} onChange={e => change({ [field]: e.target.value })} /></label>)}
            <label><input type="checkbox" checked={value.damaged} onChange={e => change({ damaged: e.target.checked })} />该接受件有破损</label></>}
        </fieldset>;
      })}
    {(tracked ? accepted.length > 0 : positive(qty.accepted)) && <label>本次接受实物物料码<input required autoComplete="off" value={sku} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} onChange={e => setSku(e.target.value)} /></label>}
    {(tracked ? rejected.length > 0 : positive(qty.rejected)) && <label>拒收类型<select value={rejection} onChange={e => setRejection(e.target.value as typeof rejection)}>
      <option value="rejected">拒收</option><option value="wrong_material">错料</option><option value="wrong_serial">错 SN</option></select></label>}
    {types.map(t => <fieldset key={t}><legend>{names[t]}凭证</legend><label>{names[t]}说明<textarea required maxLength={1000} value={evidence[t]?.description ?? ''}
      onChange={e => setEvidence(old => ({ ...old, [t]: { ...old[t], description: e.target.value } }))} /></label>
      <FormalFileUploadField purpose="receipt_exception_evidence" bindingKey={`${s.receiver_person_id}:${s.authorization_version}:${s.return_id}:${t}`}
        label={`上传${names[t]}凭证`} disabled={disabled} client={uploader}
        onAvailableChange={files => setEvidence(old => ({ ...old, [t]: { description: old[t]?.description ?? '', file: files[0] } }))}
        onBlockingChange={blocked => setUploads(old => ({ ...old, [t]: blocked }))} />
    </fieldset>)}
    <button type="submit" disabled={disabled || uploading}>核验本次验收</button>
  </fieldset></form>;
}
