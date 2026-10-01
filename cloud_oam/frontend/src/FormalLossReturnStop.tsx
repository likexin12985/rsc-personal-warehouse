import { useEffect, useMemo, useRef, useState } from 'react';
import { canonical, type Identity, type LossLine } from './formalLossReview';
import type { StopAdapter } from './lossReturnStopAdapter';
import type { StopPending, StopSource } from './lossReturnStopContracts';
import { browserStore, execute, recover, seal, type Store } from './lossReturnStopRecovery';

type Props = { identity: Identity; rootId: string; line: LossLine; adapter: StopAdapter; store?: Store;
  onSettled?(): void | Promise<void> };
const message = (error: unknown) => error instanceof Error ? error.message : '结果尚未确认，请保留原请求并回查';

export default function FormalLossReturnStop({ identity, rootId, line, adapter, store: supplied, onSettled }: Props) {
  const store = useMemo(() => supplied ?? browserStore(), [supplied]);
  const [source, setSource] = useState<StopSource | null>(null), [pending, setPending] = useState<StopPending[]>([]);
  const [prepared, setPrepared] = useState<StopPending | null>(null), [sealTarget, setSealTarget] = useState<StopPending | null>(null);
  const [why, setWhy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false), [canWrite, setCanWrite] = useState(false), [storageReady, setStorageReady] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const epoch = useRef(0), active = useRef(false), inFlight = useRef(false), preparation = useRef(0);
  const lineBinding = canonical({ id: line.line_id, quantity: line.quantity, serials: line.serials.map(s => s.serial_id).sort() });
  const valid = (captured: number) => active.current && captured === epoch.current;
  const reset = () => { preparation.current++; setPrepared(null); setConfirmed(false); };
  function local(captured: number) {
    try {
      const values = store.list(identity.person_id).filter(p => p.command.root_disposition_id === rootId);
      if (valid(captured)) { setPending(values); setStorageReady(true); }
    } catch (e) { if (valid(captured)) { setStorageReady(false); setError(message(e)); } }
  }
  async function details(captured: number) {
    const current = await adapter.context(), value = await adapter.read(rootId);
    if (current.person_id !== identity.person_id || !current.can_read || current.authorization_version < identity.authorization_version ||
        current.authorization_version !== value.authorization_version || value.person_id !== identity.person_id || value.root_disposition_id !== rootId ||
        value.report_line_id !== line.line_id || value.quantity !== line.quantity ||
        canonical([...value.serial_ids].sort()) !== canonical(line.serials.map(s => s.serial_id).sort())) throw new Error('退回明细或当前身份无法对应，请重新核验');
    if (canonical(current) !== canonical(await adapter.context())) throw new Error('读取期间权限已变化，请重新核验');
    if (valid(captured)) { setSource(value); setCanWrite(current.can_write.inverses); }
  }
  async function load() {
    if (inFlight.current) return;
    const captured = epoch.current; inFlight.current = true; setBusy(true); setError(''); setSource(null); setCanWrite(false); reset(); setSealTarget(null); local(captured);
    try { await details(captured); } catch (e) { if (valid(captured)) setError(message(e)); }
    finally { if (valid(captured)) { inFlight.current = false; setBusy(false); } }
  }
  useEffect(() => {
    active.current = true; epoch.current++; inFlight.current = false; setPending([]); setWhy(''); setNotice(''); void load();
    return () => { active.current = false; epoch.current++; };
  }, [identity.person_id, identity.authorization_version, rootId, lineBinding, adapter, store]);
  useEffect(() => {
    const changed = () => { local(epoch.current); reset(); setSealTarget(null); };
    window.addEventListener('storage', changed); return () => window.removeEventListener('storage', changed);
  }, [identity.person_id, rootId, store]);
  async function preview() {
    if (inFlight.current || !storageReady || pending.length || !canWrite || source?.state !== 'preview_required' || !why.trim()) return;
    const captured = epoch.current; inFlight.current = true; setBusy(true); setError(''); reset();
    const generation = preparation.current;
    try {
      const result = await adapter.prepare(rootId, why.trim());
      if (valid(captured) && generation === preparation.current) setPrepared(result);
    } catch (e) { if (valid(captured)) { setError(message(e)); setSource(null); setCanWrite(false); } }
    finally { if (valid(captured)) { inFlight.current = false; setBusy(false); } }
  }
  async function action(value: StopPending, mode: 'execute' | 'recover' | 'seal') {
    if (inFlight.current || (mode === 'execute' && !confirmed)) return;
    const captured = epoch.current; inFlight.current = true; setBusy(true); setError(''); setNotice(''); reset(); setSealTarget(null);
    try {
      const result = await ({ execute, recover, seal }[mode])(adapter, store, value, () => valid(captured));
      if (!valid(captured)) return;
      setNotice(result.status === 'pending' ? '尚未查到原停止请求结果，已保留原请求；不能据此再次提交。'
        : result.status === 'sealed' ? '本次原停止请求已永久封存，不能迟到执行；已完成的库存记账不会被撤销。'
        : '已核验原请求：退回已停止，对应物资恢复原冻结状态。仍需独立审批和纠正执行。');
      await details(captured);
      if (valid(captured) && result.status !== 'pending') await onSettled?.();
    } catch (e) { if (valid(captured)) { setError(message(e)); setSource(null); setCanWrite(false); } }
    finally { if (valid(captured)) { local(captured); inFlight.current = false; setBusy(false); } }
  }
  const blocked = busy || !storageReady || pending.length > 0 || !canWrite;
  return <section className="panel" aria-label="退回停止与恢复">
    <div className="page-heading"><div><h2>停止尚未出库的退回</h2><p>仅支持原退回整批尚未出库的物资；停止后恢复冻结，等待独立纠正审批。</p></div>
      <button disabled={busy} onClick={() => void load()}>刷新退回停止状态</button></div>
    {busy && <p role="status">正在核验退回停止请求…</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!storageReady && <p role="alert">停止请求存储不可用，已禁止新操作，请保留浏览器数据。</p>}
    {!!pending.length && <div><h3>原退回停止请求待核验</h3><p>中断后先回查，不会自动重发或自动封存。</p>
      {pending.map(p => <div className="toolbar" key={p.command.request_id}>
        <button disabled={busy || !storageReady} onClick={() => void action(p, 'recover')}>回查原停止请求</button>
        <button disabled={busy || !storageReady || !canWrite} onClick={() => setSealTarget(p)}>永久封存停止请求</button>
      </div>)}</div>}
    {source?.state === 'stopped' && source.stop && <div role="status"><h3>原退回已停止</h3>
      <p>{line.material_name} · {source.quantity} {line.base_unit}；停止理由：{source.stop.reason}</p>
      <p>停止时间：{new Date(source.stop.stopped_at).toLocaleString('zh-CN')}。这是历史记账结果，当前库存需单独查询。</p></div>}
    {source?.state === 'downstream_compensation_required' && <p role="alert">已有出库或后续履约事实，不能整批停止。须按实际出库、发运、验收和入库情况办理对应补偿。</p>}
    {source && !!line.serials.length && <p>SN：{line.serials.map(s => s.serial_no).join('、')}</p>}
    {source?.state === 'preview_required' && <div><p>{line.material_name} · {source.quantity} {line.base_unit}</p>
      <label>退回停止理由<textarea value={why} maxLength={500} disabled={busy} onChange={e => { setWhy(e.target.value); reset(); }} /></label>
      <button disabled={blocked || !why.trim()} onClick={() => void preview()}>预览退回停止</button>
      {!canWrite && <p>当前没有退回停止权限；已完成请求仍可按读取权限回查。</p>}</div>}
    {prepared && <div role="group" aria-label="确认退回停止"><h3>确认整批停止</h3>
      <p>{line.material_name} · {prepared.source.quantity} {line.base_unit}；理由：{prepared.command.reason}</p>
      <p>将停止这张原退回单，并把对应物资恢复原冻结状态；不会删除原处置或履约历史。</p>
      <label><input type="checkbox" disabled={busy} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />已核对原退回、数量与停止理由，确认整批停止</label>
      <button disabled={blocked || !confirmed} onClick={() => void action(prepared, 'execute')}>确认停止退回</button>
      <button disabled={busy} onClick={reset}>取消本次停止</button></div>}
    {sealTarget && <div role="alertdialog" aria-label="确认永久封存停止请求"><h3>永久封存本次原停止请求？</h3>
      <p>先回查是否已经完成；未完成时阻止该原请求迟到执行。封存本身不会改变库存或撤销已有记账。</p>
      <button disabled={busy || !storageReady || !canWrite} onClick={() => void action(sealTarget, 'seal')}>确认封存停止请求</button>
      <button disabled={busy} onClick={() => setSealTarget(null)}>取消封存停止请求</button></div>}
  </section>;
}
