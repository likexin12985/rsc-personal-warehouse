import { useEffect, useRef, useState } from 'react';
import FormalFileUploadField, { type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import { checkSelection, input, type Input, type SerialOptions, type Sources } from './formalLossSubmission';
import { fail } from './formalReturnReceiving';
import type { Adapter } from './lossSubmissionAdapter';
type Proof = { serial_id: string; expected: string; sku_code: string; serial_no: string; qr_code: string };
type Draft = { quantity: string; proofs: Record<string, Proof> };
type Options = { items: SerialOptions['items']; next: string | null; exact: string | null; error?: string };
const names = { new: '新件', used: '旧件', damaged: '坏件' };
export default function LossSubmissionForm({ current, adapter, uploader, disabled, onPrepare, onError }: {
  current: Sources; adapter: Adapter; uploader: FormalFileUploadClient; disabled: boolean;
  onPrepare(value: Input): void; onError(message: string): void;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({}), [reason, setReason] = useState(''), [files, setFiles] = useState<readonly AvailableFormalFile[]>([]);
  const [uploading, setUploading] = useState(false), [loading, setLoading] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, Options>>({}), [search, setSearch] = useState<Record<string, string>>({});
  const generation = useRef(0), reading = useRef(false);
  useEffect(() => { generation.current++; return () => { generation.current++; }; }, []);
  const update = (account: string, action: (draft: Draft) => Draft) => setDrafts(old => ({ ...old, [account]: action(old[account] ?? { quantity: '', proofs: {} }) }));
  async function load(account: string, more = false, exact: string | null = null) {
    if (reading.current || disabled) return;
    const epoch = generation.current, before = options[account], after = more ? before?.next : null;
    if (more && !after) return;
    reading.current = true; setLoading(account);
    try {
      const page = await adapter.serials(current, account, after ?? null, exact);
      if (epoch !== generation.current) return;
      if (page.stock_account_id !== account || page.ledger_cursor !== current.ledger_cursor) fail('SN 库存快照变化，请刷新');
      if (more && page.items.some(s => before.items.some(old => old.serial_id === s.serial_id))) fail('SN 分页重叠，请重新查询');
      setOptions(old => ({ ...old, [account]: { items: more ? [...before.items, ...page.items] : page.items, next: page.next_after_id, exact } }));
    } catch (e) {
      if (epoch === generation.current) setOptions(old => ({ ...old, [account]: { items: [], next: null, exact, error: e instanceof Error ? e.message : 'SN 待核验' } }));
    } finally { if (epoch === generation.current) { reading.current = false; setLoading(null); } }
  }
  function prepare(event: React.FormEvent) {
    event.preventDefault(); if (disabled || uploading || reading.current) return;
    try {
      if (!files.length || files.some(f => f.status !== 'available' || f.purpose !== 'stock_loss_evidence')) fail('请先上传并核验报损凭证');
      const lines = current.items.flatMap(account => {
        const d = drafts[account.stock_account_id]; if (!d) return [];
        const tracked = ['serial', 'lot_and_serial'].includes(account.tracking_mode), proofs = Object.values(d.proofs);
        if (tracked ? !proofs.length : !d.quantity.trim()) return [];
        return [{ stock_account_id: account.stock_account_id, quantity: tracked ? String(proofs.length) : d.quantity,
          serial_verifications: proofs.map(({ expected: _expected, ...proof }) => proof) }];
      });
      const body = input({ operator_person_id: current.person_id, reason, evidence_file_ids: files.map(f => f.file_id), lines }, current.person_id);
      checkSelection(body, current); onPrepare(body);
    } catch (e) { onError(e instanceof Error ? e.message : '报损内容未通过核验'); }
  }
  return <form onSubmit={prepare} aria-label="填写本人报损"><fieldset disabled={disabled}><legend>本次报损</legend>
    <p>填写本次实际报损物料。提交后冻结对应库存，仍需区域核实、总部审批及后续处置。</p>
    {current.items.map(account => {
      const key = account.stock_account_id, d = drafts[key] ?? { quantity: '', proofs: {} }, tracked = ['serial', 'lot_and_serial'].includes(account.tracking_mode), page = options[key];
      return <fieldset key={key}><legend>{account.sku_code} · {account.material_name}</legend>
        <p>{names[account.condition_code]} · 当前可用 {account.quantity} {account.base_unit} · {account.location_name}{account.lot_no && ` · 批次 ${account.lot_no}`}</p>
        {!tracked ? <label>本次报损数量<input aria-label={`报损数量 ${account.sku_code}`} inputMode="decimal" placeholder="留空表示本次不报损" value={d.quantity} onChange={e => update(key, old => ({ ...old, quantity: e.target.value }))} /></label> : <>
          <label>查询实物 SN<input aria-label={`查询实物 SN ${account.sku_code}`} autoComplete="off" value={search[key] ?? ''} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} onChange={e => setSearch(old => ({ ...old, [key]: e.target.value }))} /></label>
          <div className="toolbar"><button type="button" disabled={!!loading} onClick={() => void load(key)}>查询可选 SN</button><button type="button" disabled={!!loading || !search[key]} onClick={() => void load(key, false, search[key])}>精确查询 SN</button></div>
          {loading === key && <p role="status">正在核验 SN…</p>}{page?.error && <p role="alert">SN 待核验：{page.error}</p>}
          {page && !page.error && !page.items.length && <p>本次查询未找到匹配 SN。</p>}
          {page?.items.map(sn => <label key={sn.serial_id}><input type="checkbox" aria-label={`选择 SN ${sn.serial_no}`} checked={!!d.proofs[sn.serial_id]} onChange={e => update(key, old => {
            const proofs = { ...old.proofs };
            if (e.target.checked) proofs[sn.serial_id] = { serial_id: sn.serial_id, expected: sn.serial_no, sku_code: '', serial_no: '', qr_code: '' }; else delete proofs[sn.serial_id];
            return { ...old, proofs };
          })} />{sn.serial_no}</label>)}
          {page?.next && <button type="button" disabled={!!loading} onClick={() => void load(key, true)}>下一页 SN</button>}
          {Object.values(d.proofs).map(proof => <fieldset key={proof.serial_id}><legend>本次已选 SN {proof.expected}</legend><p>按实物标签扫码或录入，查询结果不会代填验证内容。</p>
            {(['sku_code', 'serial_no', 'qr_code'] as const).map((field, index) => { const label = ['实物物料码', '实物 SN', '实物二维码'][index]; return <label key={field}>{label}<input required autoComplete="off" maxLength={index === 0 ? 80 : index === 1 ? 200 : 250} aria-label={`${label} ${proof.expected}`} value={proof[field]} onKeyDown={e => { if (e.key === 'Enter') e.preventDefault(); }} onChange={e => update(key, old => ({ ...old, proofs: { ...old.proofs, [proof.serial_id]: { ...old.proofs[proof.serial_id], [field]: e.target.value } } }))} /></label>; })}
            <button type="button" onClick={() => update(key, old => { const proofs = { ...old.proofs }; delete proofs[proof.serial_id]; return { ...old, proofs }; })}>移除 SN {proof.expected}</button>
          </fieldset>)}
        </>}
      </fieldset>;
    })}
    <label>报损原因<textarea aria-label="报损原因" required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
    <FormalFileUploadField purpose="stock_loss_evidence" bindingKey={`${current.person_id}:${current.authorization_version}:${current.location_id}:${current.queried_at}`} label="上传报损凭证" multiple disabled={disabled} client={uploader} onAvailableChange={setFiles} onBlockingChange={setUploading} />
    <button type="submit" disabled={disabled || uploading || !!loading}>核验并预览本次报损</button>
  </fieldset></form>;
}
