import { useEffect, useMemo, useRef, useState } from 'react';
import { canonical, fail, type Identity } from './formalReturnReceiving';
import { command, fingerprint, inboundInput, type Command, type Detail, type Preview } from './rejectionWarehouse';
import { createStore, type Adapter, type Store, type Pending } from './rejectionWarehouseAdapter';
import RejectionWarehouseReceiptForm from './RejectionWarehouseReceiptForm';
import type { FormalFileUploadClient } from './FormalFileUploadField';

const conditions = { new: '新品', used: '旧件', damaged: '坏件' };
export default function FormalRejectionWarehousePage({ identity, adapter, store: supplied, uploader, onBack }: {
  identity: Identity; adapter: Adapter; store?: Store; uploader?: FormalFileUploadClient; onBack(): void;
}) {
  const store = useMemo(() => supplied ?? createStore(), [supplied]), [stored, setStored] = useState(() => store.read());
  const [page, setPage] = useState<Awaited<ReturnType<Adapter['list']>> | null>(null), [current, setCurrent] = useState<Detail | null>(null);
  const [prepared, setPrepared] = useState<Command | null>(null), [planned, setPlanned] = useState<Preview | null>(null);
  const [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const mounted = useRef(true), sequence = useRef(0), running = useRef(false);
  const locked = busy || stored.kind !== 'missing';
  useEffect(() => {
    mounted.current = true;
    const update = () => { sequence.current += 1; setStored(store.read()); setPrepared(null); setPlanned(null); };
    window.addEventListener('storage', update);
    return () => { mounted.current = false; sequence.current += 1; window.removeEventListener('storage', update); };
  }, [store]);
  function isCurrent(n: number) { return mounted.current && sequence.current === n; }
  function applyDetail(value: Detail) {
    setCurrent(value);
    setPage(previous => previous && previous.person_id === value.source.receiver_person_id
      && previous.authorization_version === value.source.authorization_version ? { ...previous,
        items: previous.items.map(item => item.return_id === value.source.return_id
          ? { ...item, verification_status: 'verified', detail: value } : item) } : null);
  }
  async function perform(fn: (valid: () => boolean) => Promise<void>) {
    if (running.current) return; running.current = true;
    const n = ++sequence.current; setBusy(true); setError(''); setNotice('');
    try { await fn(() => isCurrent(n)); }
    catch (e) { if (isCurrent(n)) setError(e instanceof Error ? e.message : '操作结果暂时无法核验，请保留原请求'); }
    finally { running.current = false; if (mounted.current) { setBusy(false); setStored(store.read()); } }
  }
  async function assertIdentity() {
    const c = await adapter.context();
    if (c.person_id !== identity.person_id || c.authorization_version !== identity.authorization_version || !c.can_read) fail('当前身份或权限变化，请重新进入');
  }
  const load = (after: string | null = null) => perform(async valid => {
    await assertIdentity(); const result = await adapter.list(after);
    if (valid()) { setPage(result); setCurrent(null); setPrepared(null); setPlanned(null); }
  });
  const open = (returnId: string) => perform(async valid => {
    await assertIdentity(); const result = await adapter.read(returnId);
    if (valid()) { applyDetail(result); setPrepared(null); setPlanned(null); }
  });
  const plan = (receiptId: string) => perform(async valid => {
    if (store.read().kind !== 'missing' || !current) fail('请先核验原请求');
    await assertIdentity(); const fresh = await adapter.read(current.source.return_id), result = await adapter.preview(fresh, receiptId);
    if (valid()) { applyDetail(fresh); setPlanned(result); setPrepared(null); setReason(''); }
  });
  const submit = () => perform(async valid => {
    if (store.read().kind !== 'missing' || !current || !prepared) fail('请先核验原请求与本次内容');
    await assertIdentity(); const c = command(prepared), digest = await fingerprint(c);
    if (!valid()) return;
    const original: Pending = { v: 1, ...identity, return_id: current.source.return_id, trace: crypto.randomUUID(),
      key: crypto.randomUUID(), fingerprint: digest, command: c };
    store.persist(original); setStored(store.read()); setPrepared(null); setPlanned(null);
    await adapter.submit(original, store, valid);
    const updated = await adapter.recover(original, store, valid);
    if (valid()) { applyDetail(updated); setNotice(c.kind === 'receipt' ? '本次验收已核验。接受的实物需另行确认入账。' : '本次库存入账已核验。'); }
  });
  const recover = () => perform(async valid => {
    const saved = store.read(); if (saved.kind !== 'valid' || saved.value.person_id !== identity.person_id) fail('原请求无法由当前身份核验，请保留记录');
    await assertIdentity(); const result = await adapter.recover(saved.value, store, valid);
    if (valid()) { applyDetail(result); setPrepared(null); setPlanned(null); setNotice('原请求及完整历史已核验，没有重新提交。'); }
  });
  const prepareInbound = () => {
    try {
      if (!planned || locked) return;
      setPrepared({ kind: 'inbound', receipt_id: planned.receipt_id, input: inboundInput({ expected_request_version: planned.request_version,
        reason: reason.trim(), receipt_request_hash: planned.receipt_request_hash, expected_plan_hash: planned.plan_hash }) });
      setError('');
    } catch (e) { setError(e instanceof Error ? e.message : '请核对入账说明'); }
  };
  return <section className="page-stack return-receiving-page rejection-warehouse-page"><header className="page-heading"><h1>拒收退回 · 来源仓验收与入账</h1><button onClick={onBack}>返回退回工作台</button></header>
    <p>仅展示当前负责仓库已交承运的拒收退回。实物验收、库存入账分别办理。</p>
    {error && <p className="alert alert-error" role="alert">{error}</p>}{notice && <p className="alert" role="status">{notice}</p>}
    {stored.kind !== 'missing' && <section className="panel" aria-label="原仓库请求待核验"><h2>原请求待核验</h2>
      <p>原请求已保留。先只读核验结果，再办理新的验收或入账。</p>
      {stored.kind === 'valid' ? <><p>{stored.value.command.kind === 'receipt' ? '实物验收' : '库存入账'}请求</p>
        <button disabled={busy || stored.value.person_id !== identity.person_id} onClick={() => void recover()}>只读核验原请求</button>
        {stored.value.person_id !== identity.person_id && <p>该请求属于其他人员，请切回原账号核验。</p>}</>
        : <p>浏览器中的原请求不可读取或已损坏，请保留记录并联系管理员。</p>}
    </section>}
    <button disabled={busy} onClick={() => void load()}>刷新本人仓库退回</button>
    {page && <section className="panel" aria-label="本人仓库退回"><h2>已交运退回</h2>{page.items.length === 0 && <p>当前没有可读取的仓库退回。</p>}
      {page.items.map(item => <article className="return-receipt" key={item.return_id}>{item.detail ? <><h3>{item.detail.source.return_no} · {item.detail.source.material_name}</h3>
        <p>{item.detail.source.sku_code} · {item.detail.source.target_location_name}</p><p>待确认 {item.detail.unconfirmed_qty} · 待入账 {item.detail.pending_inbound_qty} · 已入账 {item.detail.posted_qty}</p>
        <button disabled={busy} onClick={() => void open(item.return_id)}>查看退回与验收</button></> : <p role="alert">{item.message}</p>}</article>)}
      {page.next_after_id && <button disabled={busy} onClick={() => void load(page.next_after_id)}>下一页退回</button>}
    </section>}
    {current && <section className="panel" aria-label="退回详情"><h2>{current.source.return_no} · {current.source.material_name}</h2>
      <p>{current.source.sku_code} · {current.source.target_location_name} · {current.source.carrier} {current.source.tracking_no}</p>
      <dl className="rejection-quantity-grid"><div><dt>已交运</dt><dd>{current.source.quantity} {current.source.base_unit}</dd></div><div><dt>已接受</dt><dd>{current.accepted_qty}<small>其中破损 {current.damaged_qty}</small></dd></div>
        <div><dt>已拒收</dt><dd>{current.rejected_qty}</dd></div><div><dt>尚未确认</dt><dd>{current.unconfirmed_qty}</dd></div><div><dt>接受后待入账</dt><dd>{current.pending_inbound_qty}</dd></div><div><dt>已入账</dt><dd>{current.posted_qty}</dd></div></dl>
      {current.receive_permitted && <RejectionWarehouseReceiptForm key={canonical(current)} current={current} disabled={locked || prepared !== null} uploader={uploader}
        onPrepare={input => { if (store.read().kind === 'missing') { setPrepared({ kind: 'receipt', input }); setPlanned(null); setError(''); } }} onError={setError} />}
      <h3>逐次验收与独立入账</h3>{current.receipts.length === 0 && <p>尚无验收记录。</p>}
      {current.receipts.map(row => <article className="return-receipt" key={row.receipt.receipt_id}><h4>{new Date(row.receipt.received_at).toLocaleString()} · {row.receipt.reason}</h4>
        <p>接受 {row.receipt.amounts.accepted_qty} · 拒收 {row.receipt.amounts.rejected_qty} · 本次短少 {row.receipt.amounts.shortage_qty} · 接受中破损 {row.receipt.amounts.damaged_qty}</p>
        <p>{row.inbound ? '已独立入账' : row.receipt.amounts.accepted_qty === '0.000' ? '本次没有可入账实物' : '接受实物待独立入账'}</p>
        {row.post_permitted && <button disabled={locked} onClick={() => void plan(row.receipt.receipt_id)}>预览本次实物入账</button>}
      </article>)}
      {planned && <section className="panel" aria-label="独立入账预览"><h3>确认入账至 {planned.target_location_name}</h3>
        {planned.parts.map(part => <p key={part.condition_code}>{conditions[part.condition_code]} {part.quantity} {current.source.base_unit}{part.serials.length > 0 && ` · SN ${part.serials.map(sn => sn.serial_no).join('、')}`}</p>)}
        <label>入账说明<textarea disabled={locked} maxLength={500} value={reason} onChange={e => { setReason(e.target.value); setPrepared(null); }} /></label>
        <button disabled={locked} onClick={prepareInbound}>核验入账内容</button>
      </section>}
      {prepared && <section className="panel" aria-label="确认仓库操作"><h3>{prepared.kind === 'receipt' ? '确认本次实物验收' : '确认独立库存入账'}</h3>
        {prepared.kind === 'receipt' && <p>接受 {prepared.input.amounts.accepted_qty} · 拒收 {prepared.input.amounts.rejected_qty} · 短少 {prepared.input.amounts.shortage_qty} · 接受中破损 {prepared.input.amounts.damaged_qty}</p>}
        <p>{prepared.input.reason}</p><button disabled={locked} onClick={() => void submit()}>确认提交本次{prepared.kind === 'receipt' ? '验收' : '入账'}</button>
        <button disabled={busy} onClick={() => setPrepared(null)}>返回修改</button>
      </section>}
    </section>}
  </section>;
}
