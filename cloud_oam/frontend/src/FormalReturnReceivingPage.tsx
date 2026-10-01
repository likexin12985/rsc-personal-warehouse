import { useEffect, useMemo, useRef, useState } from 'react';
import { apiNoReplay } from './api';
import { executeFormalFileUpload, prepareFormalFileUpload } from './formalFileUpload';
import type { FormalFileUploadClient } from './FormalFileUploadField';
import ReturnReceiptForm from './ReturnReceiptForm';
import { canonical, fail, units, type History, type Identity, type Receipt } from './formalReturnReceiving';
import * as acceptance from './formalReturnReceipt';
import * as inbound from './formalReturnInbound';
import { browserStore, pending, recover, seal, submit, type Context, type Pending, type Store } from './returnReceivingRecovery';
import type { Adapter } from './returnReceivingAdapter';
type Item = Awaited<ReturnType<Adapter['list']>>['items'][number];
type InboundState = ReturnType<typeof inbound.state>;
const errorText = (e: unknown) => e instanceof Error ? e.message : '结果暂未确认，请保留原请求';
const conditionName = { new: '新件', used: '旧件', damaged: '坏件' };
const originName = (p: { origin?: unknown }) => p.origin ? '报损退回' : '工单退回';
function SerialList({ label, ids, line }: { label: string; ids: string[]; line: History['package']['lines'][number] }) {
  if (!ids.length) return null;
  return <p>{label}：{ids.map(id => line.serials.find(sn => sn.serial_id === id)?.serial_no ?? '待核验').join('、')}</p>;
}
type Prepared = { value: Pending; body?: acceptance.Input; plan?: inbound.InboundPreview };

export default function FormalReturnReceiving({ identity, adapter, store: provided, uploader: suppliedUploader }: {
  identity: Identity; adapter: Adapter; store?: Store; uploader?: FormalFileUploadClient;
}) {
  const store = useMemo(() => provided ?? browserStore(), [provided]);
  const [items, setItems] = useState<Item[]>([]), [next, setNext] = useState<string | null>(null), [selected, setSelected] = useState<History | null>(null);
  const [inbounds, setInbounds] = useState<Record<string, { state?: InboundState; error?: string }>>({});
  const [requests, setRequests] = useState<Pending[]>([]), [storageReady, setStorageReady] = useState(false), [canWrite, setCanWrite] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [prepared, setPrepared] = useState<Prepared | null>(null), [confirmed, setConfirmed] = useState(false), [sealTarget, setSealTarget] = useState<Pending | null>(null);
  const session = useRef(0), working = useRef(false);
  function currentAccess(value: Context, write = false) {
    if (value.person_id !== identity.person_id || value.authorization_version !== identity.authorization_version || !value.can_read || (write && !value.can_write)) fail('当前身份或权限已变化，请重新进入接收页');
    return value;
  }
  const uploader = useMemo<FormalFileUploadClient>(() => suppliedUploader ?? {
    prepare: prepareFormalFileUpload,
    async execute(value) {
      const before = currentAccess(await adapter.context(), true);
      const result = await executeFormalFileUpload(value, { requester: apiNoReplay });
      const after = currentAccess(await adapter.context(), true);
      if (canonical(before) !== canonical(after)) fail('上传期间权限变化，请重新核验凭证'); return result;
    },
  }, [adapter, identity.person_id, identity.authorization_version, suppliedUploader]);
  function local(epoch: number) {
    try { const rows = store.list(identity.person_id); if (session.current === epoch) { setRequests(rows); setStorageReady(true); } }
    catch (cause) { if (session.current === epoch) { setStorageReady(false); setError(errorText(cause)); } }
  }
  async function list(epoch: number, after: string | null = null) {
    const access = currentAccess(await adapter.context()), page = await adapter.list(after);
    if (epoch !== session.current) return;
    setCanWrite(access.can_write); setItems(old => after ? [...old.filter(r => !page.items.some(p => p.shipment_id === r.shipment_id)), ...page.items] : page.items); setNext(page.next_after_id); local(epoch);
  }
  async function read(epoch: number, shipment: string) {
    const data = await adapter.read(shipment); if (session.current !== epoch) return;
    if (data.person_id !== identity.person_id || data.authorization_version !== identity.authorization_version) fail('接收身份已变化');
    setSelected(data); setInbounds({});
    const states = await Promise.all(data.receipts.map(async r => {
      try { return [r.receipt_id, { state: await adapter.inboundState(data, r.receipt_id) }] as const; }
      catch (cause) { return [r.receipt_id, { error: errorText(cause) }] as const; }
    }));
    if (session.current === epoch) setInbounds(Object.fromEntries(states));
  }
  async function action(work: (epoch: number, valid: () => boolean) => Promise<void>) {
    if (working.current) return;
    const epoch = session.current, valid = () => session.current === epoch;
    working.current = true; setBusy(true); setError('');
    try { await work(epoch, valid); } catch (cause) { if (valid()) setError(errorText(cause)); }
    finally { if (valid()) { working.current = false; setBusy(false); local(epoch); } }
  }
  useEffect(() => {
    const epoch = ++session.current; working.current = false;
    setItems([]); setNext(null); setSelected(null); setInbounds({}); setPrepared(null); setSealTarget(null); setConfirmed(false); setNotice(''); setCanWrite(false); setRequests([]); local(epoch);
    void action(async n => list(n));
    return () => { session.current++; };
  }, [adapter, store, identity.person_id, identity.authorization_version]);
  const open = (shipment: string) => action(async epoch => { setSelected(null); setPrepared(null); setConfirmed(false); setSealTarget(null); await read(epoch, shipment); });
  const refresh = () => action(async epoch => { setSelected(null); setPrepared(null); setConfirmed(false); setSealTarget(null); setItems([]); setNext(null); setCanWrite(false); await list(epoch); });
  async function prepareReceipt(body: acceptance.Input) {
    if (!selected || !storageReady) return;
    await action(async (_epoch, valid) => {
      const access = currentAccess(await adapter.context(), true), current = await adapter.read(selected.package.shipment_id);
      if (canonical(current.package) !== canonical(selected.package)) fail('包裹或接收责任变化，请刷新');
      acceptance.checkSelection(body, current);
      const original = pending({ v: 1, kind: 'receipt', person_id: access.person_id, authorization_version: access.authorization_version, package: current.package,
        command: { ...body, expected_plan_hash: '0'.repeat(64), request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() } });
      if (original.kind !== 'receipt') fail();
      const view = await acceptance.preview(await adapter.preview(original), access, body, current);
      const after = currentAccess(await adapter.context(), true); if (canonical(access) !== canonical(after)) fail('预检期间权限变化');
      if (!valid()) return;
      setPrepared({ value: pending({ ...original, command: { ...original.command, expected_plan_hash: view.plan_hash } }), body }); setConfirmed(false);
    });
  }
  async function prepareInbound(accepted: Receipt) {
    if (!selected || !storageReady) return;
    await action(async (_epoch, valid) => {
      const access = currentAccess(await adapter.context(), true), current = await adapter.read(selected.package.shipment_id), verified = current.receipts.find(r => r.receipt_id === accepted.receipt_id);
      if (!verified || canonical(verified) !== canonical(accepted)) fail('原验收事实变化，请刷新');
      if ((await adapter.inboundState(current, accepted.receipt_id)).status !== 'not_posted') fail('此验收已完成入库，请刷新');
      const c = { operator_person_id: access.person_id, expected_plan_hash: '0'.repeat(64), request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() };
      const value = pending({ v: 1, kind: 'inbound', person_id: access.person_id, authorization_version: access.authorization_version, package: current.package, receipt: verified,
        original: { receipt_id: accepted.receipt_id, shipment_id: current.package.shipment_id, target_location_id: current.package.target_location_id, target_custody_assignment_id: current.package.custody_assignment_id, command: c, request_hash: '0'.repeat(64) } });
      if (value.kind !== 'inbound') fail();
      const plan = inbound.preview(await adapter.preview(value), access, verified), command = { ...c, expected_plan_hash: plan.plan_hash };
      const request_hash = await inbound.hash({ receipt_id: accepted.receipt_id, request_id: c.request_id, plan_hash: plan.plan_hash });
      const after = currentAccess(await adapter.context(), true); if (canonical(access) !== canonical(after)) fail('预检期间权限变化'); if (!valid()) return;
      setPrepared({ value: pending({ ...value, original: { ...value.original, command, request_hash } }), plan }); setConfirmed(false);
    });
  }
  function message(status: string) { return status === 'accepted' ? '本次实物验收已核验，仍需单独入库。' : status === 'posted' ? '独立入库已核验，库存交易已过账。' : status === 'sealed' ? '本次原请求已永久封存，未撤销其他业务事实。' : '尚未观察到原请求结果。已保留请求，请继续回查，勿重新提交。'; }
  async function send() {
    if (!prepared || !confirmed) return; const original = prepared.value;
    await action(async (epoch, valid) => {
      try { const result = await submit(adapter, store, original, valid); if (!valid()) return; setNotice(message(result.status)); await read(epoch, original.package.shipment_id); }
      finally { if (valid()) { setPrepared(null); setConfirmed(false); } }
    });
  }
  async function resolve(value: Pending, mode: 'recover' | 'seal') {
    await action(async (epoch, valid) => {
      const result = mode === 'seal' ? await seal(adapter, store, value, true, valid) : await recover(adapter, store, value, valid);
      if (!valid()) return; setNotice(message(result.status)); setSealTarget(null); setPrepared(null); setConfirmed(false);
      if (selected?.package.shipment_id === value.package.shipment_id) await read(epoch, value.package.shipment_id);
    });
  }
  const unresolved = selected && requests.some(p => p.package.shipment_id === selected.package.shipment_id);
  const hasRemaining = selected?.lines.some(l => units(l.unconfirmed_qty) > 0n);
  return <section className="page-stack return-receiving-page">
    <div className="page-heading"><div><h1>退回收货与入库</h1><p>核验送达区域仓的报损退回或工单旧坏件。实物验收与库存入库分别确认。</p></div><button disabled={busy} onClick={() => void refresh()}>刷新包裹</button></div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}{notice && <div className="alert" role="status">{notice}</div>}
    {busy && <p role="status">正在核验，请勿重复操作…</p>}
    {!storageReady && <p role="alert">本机原请求不可读，已停止新验收和入库。请保留浏览器数据。</p>}
    {requests.length > 0 && <section className="panel" aria-label="原请求待核验"><h2>原请求待核验（{requests.length}）</h2><p>回查已有结果；网络中断不代表未执行。</p>{requests.map(p => <article key={p.package.shipment_id} className="return-pending"><strong>{p.package.shipment_no} · {p.kind === 'receipt' ? '实物验收' : '独立入库'}</strong><div className="toolbar"><button disabled={busy} onClick={() => void open(p.package.shipment_id)}>查看原包裹</button><button disabled={busy} onClick={() => void resolve(p, 'recover')}>回查原请求</button><button disabled={busy || !canWrite} onClick={() => setSealTarget(p)}>永久封存原请求</button></div></article>)}</section>}
    {sealTarget && <section className="panel" role="alertdialog" aria-label="确认永久封存"><h2>永久封存本次原请求？</h2><p>先回查已执行结果；如果尚未执行，则阻止本次请求迟到执行。这不会撤销已有验收或入库。</p><button disabled={busy} onClick={() => void resolve(sealTarget, 'seal')}>确认永久封存</button><button disabled={busy} onClick={() => setSealTarget(null)}>取消封存</button></section>}
    <section className="panel"><h2>本人负责的退回包裹</h2>{!busy && !items.length && <p>当前没有可接收的退回包裹。</p>}<div className="table-scroll"><table><thead><tr><th>退回包裹</th><th>来源 / 收货仓</th><th>物流</th><th>操作</th></tr></thead><tbody>{items.map(row => <tr key={row.shipment_id}>{row.verification_status === 'unavailable' ? <><td>原单待核验</td><td colSpan={2}>{row.message}</td><td><button disabled={busy} onClick={() => void open(row.shipment_id)}>重新核验</button></td></> : <><td>{row.shipment_no}</td><td>{originName(row)} / {row.target_location_name}</td><td>{row.carrier} · {row.tracking_no}</td><td><button disabled={busy} onClick={() => void open(row.shipment_id)}>查看包裹</button></td></>}</tr>)}</tbody></table></div>{next && <button disabled={busy} onClick={() => void action(epoch => list(epoch, next))}>下一页包裹</button>}</section>
    {selected && <section className="panel" aria-label="退回包裹详情"><h2>{selected.package.shipment_no}</h2><p>{originName(selected.package)} · {selected.package.operation_no} · {selected.package.target_location_name}</p><p>承运商：{selected.package.carrier}　运单：{selected.package.tracking_no}</p>
      <div className="table-scroll"><table><thead><tr><th>物料</th><th>发出成色</th><th>发货</th><th>已接受</th><th>已拒收</th><th>接受中破损</th><th>尚未确认</th></tr></thead><tbody>{selected.package.lines.map(line => { const total = selected.lines.find(l => l.shipment_line_id === line.shipment_line_id)!; return <tr key={line.shipment_line_id}><td>{line.sku_code}<br />{line.material_name}（{line.base_unit}）</td><td>{conditionName[line.condition_code]}</td><td>{line.shipped_quantity}</td><td>{total.accepted_qty}</td><td>{total.rejected_qty}</td><td>{total.damaged_qty}</td><td>{total.unconfirmed_qty}</td></tr>; })}</tbody></table></div>
      {unresolved && <p>此包裹已有原请求待核验，已停止新验收和入库，请先使用上方回查入口。</p>}
      {!canWrite && <p>当前为只读，可查看并回查已有原请求。</p>}
      {hasRemaining && canWrite && !unresolved && <ReturnReceiptForm key={`${identity.person_id}:${identity.authorization_version}:${selected.package.shipment_id}:${selected.receipts.length}`} current={selected} disabled={busy || !storageReady || !!prepared} uploader={uploader} onPrepare={body => void prepareReceipt(body)} onError={setError} />}
      <h3>逐次验收与独立入库</h3>{!selected.receipts.length && <p>尚无实物验收记录。</p>}{selected.receipts.map(r => {
        const proof = inbounds[r.receipt_id], posted = proof?.state?.status === 'posted';
        return <article className="return-receipt" key={r.receipt_id}><h4>{r.receipt_no}</h4><p>实物验收：{r.status === 'exception' ? '有异常' : '已接受'} · {new Date(r.received_at).toLocaleString('zh-CN')} · {r.reason}</p><ul>{r.lines.map(l => <li key={l.shipment_line_id}>{l.material_name}：接受 {l.accepted_qty}、拒收 {l.rejected_qty}、短少 {l.shortage_qty}、接受中破损 {l.damaged_qty}{l.exceptions.map(e => <p key={e.exception_type}>异常说明：{e.description}（凭证已关联）</p>)}</li>)}</ul>
          <p>库存入库：{posted ? '已过账' : proof?.state?.status === 'not_posted' ? '尚未入库' : '待核验'}</p>{proof?.error && <p role="alert">{proof.error}</p>}
          {posted && <p>库存交易：{proof.state!.inbound!.posting_transaction_id}</p>}
          {!posted && proof?.state?.status === 'not_posted' && r.lines.some(l => units(l.accepted_qty) > 0n) && <button disabled={busy || !canWrite || !storageReady || !!unresolved || !!prepared} onClick={() => void prepareInbound(r)}>预览入库 {r.receipt_no}</button>}
        </article>;
      })}
    </section>}
    {prepared && <section className="panel" aria-label="确认本次操作"><h2>{prepared.value.kind === 'receipt' ? '确认本次实物验收' : '确认独立入库'}</h2><p>退回包裹：{prepared.value.package.shipment_no}　目标仓：{prepared.value.package.target_location_name}</p>
      {prepared.body && <><p>实际验收时间：{new Date(prepared.body.received_at).toLocaleString('zh-CN')}　说明：{prepared.body.reason}</p><ul>{prepared.body.lines.map(l => {
        const line = prepared.value.package.lines.find(s => s.shipment_line_id === l.shipment_line_id)!;
        return <li key={l.shipment_line_id}>{line.sku_code} · {line.material_name}：接受 {l.accepted_qty}、拒收 {l.rejected_qty}、短少 {l.shortage_qty}、接受中破损 {l.damaged_qty}
          <SerialList label="接受 SN" ids={l.accepted_serial_verifications.map(s => s.serial_id)} line={line} />
          <SerialList label="拒收 SN" ids={l.rejected_serial_ids} line={line} />
          <SerialList label="短少 SN" ids={l.shortage_serial_ids} line={line} />
          <SerialList label="接受中破损 SN" ids={l.damaged_serial_ids} line={line} />
          {l.exceptions.map(e => <p key={e.exception_type}>异常说明：{e.description}（凭证已核验）</p>)}</li>;
      })}</ul><p>本次仅记录实物验收，提交后还需单独入库。</p></>}
      {prepared.plan && <><ul>{prepared.plan.lines.map(l => {
        const line = prepared.value.package.lines.find(s => s.shipment_line_id === l.shipment_line_id)!;
        return <li key={`${l.receipt_line_id}:${l.condition_code}`}>{line.sku_code} · {line.material_name} · {l.accepted_qty} · {conditionName[l.condition_code]}
          <SerialList label="本次入库 SN" ids={l.serial_ids} line={line} /></li>;
      })}</ul><p>只对本次验收已接受的物料入库，保留原成色；短少和拒收不会入账，破损异常仍需后续处理。</p></>}
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />已核对本次物料、数量、SN 和目标仓</label>
      <div className="toolbar"><button disabled={busy || !confirmed || !storageReady || !canWrite} onClick={() => void send()}>{prepared.value.kind === 'receipt' ? '确认提交验收' : '确认独立入库'}</button><button disabled={busy} onClick={() => { setPrepared(null); setConfirmed(false); }}>返回修改</button></div>
    </section>}
  </section>;
}
