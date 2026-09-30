import { useEffect, useMemo, useRef, useState } from 'react';
import { apiNoReplay } from './api';
import { executeFormalFileUpload, prepareFormalFileUpload } from './formalFileUpload';
import type { FormalFileUploadClient } from './FormalFileUploadField';
import { canonical, fail, type Identity } from './formalReturnReceiving';
import { pending, type Input, type Pending, type Sources } from './formalLossSubmission';
import { browserStore, recover, seal, submit, type Context, type Resolution, type Store } from './lossSubmissionRecovery';
import type { Adapter } from './lossSubmissionAdapter';
import LossSubmissionForm from './LossSubmissionForm';
const condition = { new: '新件', used: '旧件', damaged: '坏件' };
const errorText = (e: unknown) => e instanceof Error ? e.message : '报损结果尚未核验，请保留原请求';
export default function FormalLossSubmissionPage({ identity, adapter, store: provided, uploader: supplied }: {
  identity: Identity; adapter: Adapter; store?: Store; uploader?: FormalFileUploadClient;
}) {
  const store = useMemo(() => provided ?? browserStore(), [provided]);
  const [current, setCurrent] = useState<Sources | null>(null), [requests, setRequests] = useState<Pending[]>([]), [storageReady, setStorageReady] = useState(false);
  const [canWrite, setCanWrite] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [prepared, setPrepared] = useState<Pending | null>(null), [confirmed, setConfirmed] = useState(false), [sealTarget, setSealTarget] = useState<Pending | null>(null);
  const generation = useRef(0), working = useRef(false);
  function access(value: Context, write = false) {
    if (value.person_id !== identity.person_id || value.authorization_version !== identity.authorization_version || !value.can_read || (write && !value.can_write)) fail('当前报损身份或权限已变化，请重新进入');
    return value;
  }
  const uploader = useMemo<FormalFileUploadClient>(() => supplied ?? {
    prepare: prepareFormalFileUpload,
    async execute(value) {
      const before = access(await adapter.context(), true);
      const result = await executeFormalFileUpload(value, { requester: apiNoReplay });
      const after = access(await adapter.context(), true); if (canonical(before) !== canonical(after)) fail('上传期间权限变化，请重新核验凭证');
      return result;
    },
  }, [supplied, adapter, identity.person_id, identity.authorization_version]);
  function local(epoch: number) {
    try { const rows = store.list(identity.person_id); if (epoch === generation.current) { setRequests(rows); setStorageReady(true); } }
    catch (e) { if (epoch === generation.current) { setStorageReady(false); setError(errorText(e)); } }
  }
  async function action(work: (epoch: number, valid: () => boolean) => Promise<void>) {
    if (working.current) return;
    const epoch = generation.current, valid = () => epoch === generation.current;
    working.current = true; setBusy(true); setError('');
    try { await work(epoch, valid); } catch (e) { if (valid()) setError(errorText(e)); }
    finally { if (valid()) { working.current = false; setBusy(false); local(epoch); } }
  }
  async function load(epoch: number) {
    const before = access(await adapter.context()); if (epoch !== generation.current) return;
    setCanWrite(before.can_write); setCurrent(null);
    if (!before.can_write) return;
    const result = await adapter.readSources();
    if (result.person_id !== identity.person_id || result.authorization_version !== identity.authorization_version) fail('报损来源身份变化');
    if (epoch === generation.current) setCurrent(result);
  }
  useEffect(() => {
    const epoch = ++generation.current; working.current = false;
    setCurrent(null); setRequests([]); setCanWrite(false); setPrepared(null); setConfirmed(false); setSealTarget(null); setNotice(''); local(epoch);
    void action(async n => load(n));
    return () => { generation.current++; };
  }, [adapter, store, identity.person_id, identity.authorization_version]);
  async function prepare(body: Input) {
    if (!current || !storageReady || requests.length) return;
    await action(async (_epoch, valid) => {
      const before = access(await adapter.context(), true), snapshot = await adapter.prepare(body);
      if (snapshot.sources.location_id !== current.location_id || snapshot.sources.custody_effective_from !== current.custody_effective_from) fail('个人仓保管责任变化，请刷新后重新选择');
      const p = pending({ v: 1, person_id: before.person_id, authorization_version: before.authorization_version, ...snapshot,
        command: { ...body, expected_plan_hash: snapshot.preview.plan_hash, request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() } });
      const after = access(await adapter.context(), true); if (canonical(before) !== canonical(after)) fail('预检期间权限变化');
      if (valid()) { setPrepared(p); setConfirmed(false); }
    });
  }
  function message(result: Resolution) {
    return result.status === 'submitted' ? `报损单 ${result.submission.operation_no} 已核验提交并冻结对应库存，仍需审批及后续处置。`
      : result.status === 'sealed' ? '原报损请求已永久封存，未撤销其他已发生业务。'
      : '尚未观察到原报损结果，请保留原请求并继续回查，勿重新提交。';
  }
  function refresh() { void action(async epoch => { setCurrent(null); setPrepared(null); setConfirmed(false); setSealTarget(null); setCanWrite(false); await load(epoch); }); }
  async function send() {
    if (!prepared || !confirmed) return; const original = prepared;
    await action(async (epoch, valid) => {
      try {
        const result = await submit(adapter, store, original, valid); if (!valid()) return;
        setNotice(message(result)); if (result.status !== 'unknown') await load(epoch);
      } finally { if (valid()) { setPrepared(null); setConfirmed(false); } }
    });
  }
  async function resolve(original: Pending, permanent = false) {
    await action(async (epoch, valid) => {
      const result = permanent ? await seal(adapter, store, original, true, valid) : await recover(adapter, store, original, valid);
      if (!valid()) return; setNotice(message(result)); setSealTarget(null); setPrepared(null); setConfirmed(false);
      if (result.status !== 'unknown') await load(epoch);
    });
  }
  return <section className="page-stack return-receiving-page loss-submission-page">
    <div className="page-heading"><div><h1>本人报损</h1><p>按实际持有的物料提交报损，冻结、审批和后续处置分别记录。</p></div><button disabled={busy} onClick={refresh}>刷新本人库存</button></div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}{notice && <div className="alert" role="status">{notice}</div>}{busy && <p role="status">正在核验，请勿重复操作…</p>}
    {!storageReady && <p role="alert">本机原报损请求不可读，已停止新提交。请保留浏览器数据。</p>}
    {requests.length > 0 && <section className="panel" aria-label="报损原请求待核验"><h2>原报损请求待核验（{requests.length}）</h2><p>网络中断不代表未执行。先回查原请求，再处理后续报损。</p>{requests.map(p => <article key={`${p.sources.location_id}:${p.command.request_id}`}><strong>{p.command.reason}</strong><p>本次 {p.command.lines.length} 项物料 · {p.preview.lines[0].source.location_name}</p><div className="toolbar"><button disabled={busy} onClick={() => void resolve(p)}>回查原报损请求</button><button disabled={busy || !canWrite} onClick={() => setSealTarget(p)}>永久封存原报损请求</button></div></article>)}</section>}
    {sealTarget && <section className="panel" role="alertdialog" aria-label="确认封存原报损请求"><h2>永久封存原报损请求？</h2><p>原因：{sealTarget.command.reason}</p><p>先回查；尚未执行时阻止本次原请求迟到执行，不撤销已经发生的冻结或审批。</p><button disabled={busy} onClick={() => void resolve(sealTarget, true)}>确认永久封存</button><button disabled={busy} onClick={() => setSealTarget(null)}>取消封存</button></section>}
    {!busy && !canWrite && <p>当前不能发起新报损，可按读权限回查已有原请求。</p>}
    {canWrite && !current && !busy && <p>本人可用库存待核验，请刷新，不能据此认定库存为零。</p>}
    {current && <section className="panel" aria-label="本人报损来源"><h2>本人当前可用库存</h2><p>来源核验时间：{new Date(current.queried_at).toLocaleString('zh-CN')}</p>
      {!current.items.length && <p>当前已核验，没有可用于报损的可用库存。</p>}
      {!!requests.length && <p>已有待核验原报损请求，暂停新报损。</p>}
      {current.items.length > 0 && !requests.length && <LossSubmissionForm key={`${identity.person_id}:${identity.authorization_version}:${current.location_id}:${current.queried_at}`} current={current} adapter={adapter} uploader={uploader} disabled={busy || !canWrite || !storageReady || !!prepared} onPrepare={value => void prepare(value)} onError={setError} />}
    </section>}
    {prepared && <section className="panel" aria-label="确认本次报损"><h2>确认本次报损</h2><p>原因：{prepared.command.reason}</p><ul>{prepared.preview.lines.map(line => <li key={line.source.stock_account_id}>
      {line.source.sku_code} · {line.source.material_name} · {condition[line.source.condition_code]} · 本次 {line.selected_quantity} {line.source.base_unit} · {line.source.location_name}{line.source.lot_no && ` · 批次 ${line.source.lot_no}`}
      {!!line.selected_serials.length && <p>本次 SN：{line.selected_serials.map(s => s.serial_no).join('、')}</p>}
    </li>)}</ul><p>已核验凭证：{prepared.preview.evidence.map(f => f.original_filename).join('、')}</p><p>提交后冻结以上物料；审批通过、退回和报废均需后续独立操作。</p>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />已核对本次物料、数量、SN、原因和凭证</label>
      <div className="toolbar"><button disabled={busy || !confirmed || !canWrite || !storageReady} onClick={() => void send()}>确认提交报损</button><button disabled={busy} onClick={() => { setPrepared(null); setConfirmed(false); }}>返回修改</button></div>
    </section>}
  </section>;
}
