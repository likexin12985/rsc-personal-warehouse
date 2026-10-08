import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import { conditionActionLabels, conditionStatusLabels, conditionTransitionAllowed, type Action, type ConditionHistory } from './returnConditionHistory';
import type { ConditionAdapter, PreparedCondition } from './returnConditionAdapter';
import { conditionUnits, type ConditionSource } from './returnConditionSource';
import type { Scan } from './returnConditionCommands';
import { browserStore, submit, type Store } from './returnConditionRecovery';
import ConditionRequestRecovery from './ConditionRequestRecovery';
import ConditionSerialCheck from './ConditionSerialCheck';
import ConditionEventEvidence from './ConditionEventEvidence';
import FormalFileUploadField, { type AvailableFormalFile, type FormalFileUploadClient } from './FormalFileUploadField';
import './lossExecution.css';

type Props = { identity: Identity; inboundId: string; adapter: ConditionAdapter; store?: Store;
  uploadClient?: FormalFileUploadClient; onBack(): void };
export default function FormalReturnConditionPage({ identity, inboundId, adapter, store: supplied, uploadClient, onBack }: Props) {
  const store = useMemo(() => supplied ?? browserStore(), [supplied]);
  const [history, setHistory] = useState<ConditionHistory | null>(null), [source, setSource] = useState<ConditionSource | null>(null);
  const [caseId, setCaseId] = useState(''), [kind, setKind] = useState<Action | ''>('submit'), [canWrite, setCanWrite] = useState(false);
  const [reason, setReason] = useState(''), [amount, setAmount] = useState(''), [chosen, setChosen] = useState<string[]>([]);
  const [files, setFiles] = useState<readonly AvailableFormalFile[]>([]), [scans, setScans] = useState<Scan[]>([]);
  const [physical, setPhysical] = useState(false), [uploading, setUploading] = useState(false), [busy, setBusy] = useState(false);
  const [prepared, setPrepared] = useState<PreparedCondition | null>(null), [confirmed, setConfirmed] = useState(false);
  const [storageReady, setStorageReady] = useState(false), [unresolved, setUnresolved] = useState(false), [recoveryVersion, setRecoveryVersion] = useState(0);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const epoch = useRef(0), inFlight = useRef(false);
  const invalidate = () => { setPrepared(null); setConfirmed(false); };
  function local() { try { store.list(identity.person_id); const state = store.read(identity.person_id, inboundId);
    setStorageReady(state.status === 'valid' || state.status === 'missing'); setUnresolved(state.status !== 'missing');
  } catch { setStorageReady(false); setUnresolved(true); } }
  async function work(run: (current: () => boolean) => Promise<void>) {
    if (inFlight.current) return; const captured = epoch.current; inFlight.current = true; setBusy(true); setError('');
    try { await run(() => captured === epoch.current); }
    catch { if (captured === epoch.current) { setError('本次核验或操作未能完整确认。若已发送，请先回查原请求；不要重复提交。'); invalidate(); } }
    finally { if (captured === epoch.current) { local(); setBusy(false); inFlight.current = false; } }
  }
  async function load() { await work(async current => {
    invalidate(); setHistory(null); setSource(null); setCaseId(''); setKind('submit'); setCanWrite(false); setScans([]); setChosen([]); setPhysical(false); setFiles([]); setReason(''); setAmount(''); local();
    const c = await adapter.context('submit');
    if (c.person_id !== identity.person_id || c.authorization_version !== identity.authorization_version || !c.can_read) throw new Error();
    const h = await adapter.read(inboundId); if (!current()) return;
    setHistory(h); setCanWrite(c.can_write);
    if (c.can_write) { const s = await adapter.source(inboundId); if (current()) setSource(s); }
  }); }
  useEffect(() => { epoch.current++; inFlight.current = false; setNotice(''); setHistory(null); setSource(null); void load();
    return () => { epoch.current++; }; }, [identity.person_id, identity.authorization_version, inboundId, adapter, store]);
  useEffect(() => { const changed = () => { local(); invalidate(); setRecoveryVersion(n => n + 1); };
    window.addEventListener('storage', changed); return () => window.removeEventListener('storage', changed);
  }, [identity.person_id, inboundId, store]);
  function selection(id: string) { if (inFlight.current) return; invalidate(); setCaseId(id); setKind(id ? '' : 'submit'); setCanWrite(false);
    setScans([]); setChosen([]); setPhysical(false); setFiles([]); setReason(''); setAmount('');
    if (!id) void chooseAction('submit');
  }
  async function chooseAction(value: Action | '') { if (inFlight.current) return; invalidate(); setKind(value); setCanWrite(false); setScans([]); setPhysical(false); setFiles([]);
    if (!value) return;
    await work(async current => { const c = await adapter.context(value);
      if (c.person_id !== identity.person_id || c.authorization_version !== identity.authorization_version || !c.can_read) throw new Error();
      if (current()) setCanWrite(c.can_write);
      if (value === 'submit' && c.can_write) { const s = await adapter.source(inboundId); if (current()) setSource(s); }
    });
  }
  const selected = history?.cases.find(c => c.case_id === caseId);
  const physicalRequired = ['submit', 'verify_region', 'execute', 'release'].includes(kind);
  const tracked = kind === 'submit' ? source?.tracking_mode === 'serial' || source?.tracking_mode === 'lot_and_serial' : !!selected?.serial_ids.length;
  const selectedIds = kind === 'submit' ? chosen : selected?.serial_ids ?? [];
  const serials = history?.serials.filter(s => selectedIds.includes(s.serial_id)) ?? [];
  const quantity = kind === 'submit' && tracked ? `${chosen.length}.000` : amount;
  const needsEvidence = kind === 'submit' || kind === 'supplement' || kind === 'verify_region';
  let validAmount = kind !== 'submit';
  try { if (kind === 'submit' && source?.claimable_quantity !== null && source?.claimable_quantity !== undefined)
    validAmount = conditionUnits(quantity) > 0n && conditionUnits(quantity) <= conditionUnits(source.claimable_quantity); } catch { /* input remains unready */ }
  const ready = !!history && !!kind && canWrite && storageReady && !unresolved && !uploading && !!reason.trim()
    && validAmount && (!needsEvidence || files.length > 0) && files.length <= 20
    && (!physicalRequired || physical && (!tracked || serials.length > 0 && scans.length === serials.length));
  async function preview() { if (!ready || !kind) return; await work(async current => {
    invalidate(); const value = await adapter.prepare(inboundId, kind, caseId || null,
      { reason: reason.trim(), evidence_file_ids: files.map(f => f.file_id), quantity, scans });
    if (current()) setPrepared(value);
  }); }
  async function send() { if (!prepared || !confirmed || !ready) return; const value = prepared;
    await work(async current => { invalidate(); setNotice('');
      try { const result = await submit(adapter, store, value.pending, current); if (!current()) return;
        setNotice(result.status === 'pending' ? '结果未知，原请求已保留。请先回查，不要重复发送。'
          : result.status === 'sealed' ? '原请求已封存，没有因此改变库存。'
          : `${conditionActionLabels[result.result.action]}已核验：${conditionStatusLabels[result.result.status]}。`);
        if (result.status !== 'pending') { const h = await adapter.read(inboundId); if (current()) { setHistory(h); setKind(''); setCanWrite(false); setSource(null); setScans([]); setPhysical(false); } }
      } finally { if (current()) setRecoveryVersion(n => n + 1); }
    });
  }
  return <section className="page-stack loss-execution-page">
    <div className="page-heading"><div><h1>历史入库成色纠正</h1><p>原入库记录保留。申请冻结、独立复核、批准、执行或释放分别办理。</p></div>
      <button disabled={busy || uploading} onClick={onBack}>返回</button><button disabled={busy || uploading} onClick={() => void load()}>刷新案件</button></div>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{busy && <p role="status">正在核验，请稍候…</p>}
    <ConditionRequestRecovery key={recoveryVersion} identity={identity} transport={adapter} store={store} onSettled={load} />
    {!storageReady && <p role="alert">原请求存储不可用，已停止新操作。请保留浏览器数据。</p>}
    {unresolved && <p>这条入库明细已有原请求待核验，请先回查或明确封存后再准备新操作。</p>}
    {history && <section className="panel"><h2>{history.material_name}</h2><p>{history.sku_code} · 原破损接受份额 {history.historical_damaged_quantity} {history.base_unit}</p>
      <p>历史冻结 {history.held_quantity}、已纠正 {history.corrected_quantity}、尚未申请 {history.unclaimed_quantity} {history.base_unit}。这些份额不代表当前可用库存。</p>
      <label>办理案件<select aria-label="办理案件" disabled={busy || uploading} value={caseId} onChange={e => selection(e.target.value)}>
        <option value="">新建成色纠正申请</option>{history.cases.map((c, i) => <option key={c.case_id} value={c.case_id}>第 {i + 1} 笔 · {c.quantity} {history.base_unit} · {conditionStatusLabels[c.status]}</option>)}
      </select></label>
      {selected && <ol>{history.events.filter(e => e.fact.case_id === selected.case_id).map(e => <li key={e.fact.event_id}>{conditionActionLabels[e.fact.action]} · {e.fact.reason}
        <ConditionEventEvidence key={`${identity.person_id}:${identity.authorization_version}:${history.history_fingerprint}:${caseId}:${kind}`}
          adapter={adapter} inbound={inboundId} event={e.fact.event_id} files={e.evidence_file_ids} />
      </li>)}</ol>}
      <fieldset disabled={busy || uploading || !storageReady || unresolved}><legend>本次办理</legend>
        <label>本次动作<select aria-label="本次动作" value={kind} onChange={e => void chooseAction(e.target.value as Action | '')}>
          <option value="">请选择本次动作</option>{caseId && selected ? (Object.keys(conditionActionLabels) as Action[]).filter(a => conditionTransitionAllowed(a, selected.status)).map(a =>
            <option key={a} value={a}>{conditionActionLabels[a]}</option>) : <option value="submit">提交并冻结</option>}
        </select></label>
        {!canWrite && <p>当前没有此动作的办理权限，可以查看案件或回查原请求。</p>}
        {kind === 'submit' && source && <><p>本次可申请份额：{source.claimable_quantity ?? '待进一步核对'} {history.base_unit}</p>
          {tracked ? <fieldset disabled={!canWrite}><legend>选择本次实物</legend>{source.serials.filter(s => s.claimable_for_correction).map(s => <label key={s.serial_id}>
            <input type="checkbox" checked={chosen.includes(s.serial_id)} onChange={e => { invalidate(); setScans([]); setPhysical(false);
              setChosen(v => e.target.checked ? [...v, s.serial_id] : v.filter(id => id !== s.serial_id)); }} />{s.serial_no}</label>)}<p>本次数量 {quantity} {history.base_unit}</p></fieldset>
            : <label>本次申请数量<input aria-label="本次申请数量" inputMode="decimal" value={amount} disabled={!canWrite} onChange={e => { setAmount(e.target.value); setPhysical(false); invalidate(); }} /></label>}
        </>}
        <label>办理说明<textarea aria-label="办理说明" maxLength={500} value={reason} disabled={!canWrite} onChange={e => { setReason(e.target.value); invalidate(); }} /></label>
        {!!kind && <FormalFileUploadField purpose="return_condition_evidence" bindingKey={`${identity.person_id}:${identity.authorization_version}:${inboundId}:${caseId}:${kind}`}
          label="本次成色核验附件" multiple client={uploadClient} disabled={busy || !canWrite || !storageReady || unresolved}
          onAvailableChange={v => { setFiles(v); invalidate(); }} onBlockingChange={setUploading} />}
        {physicalRequired && tracked && serials.length > 0 && <ConditionSerialCheck key={`${caseId}:${kind}:${serials.map(s => s.serial_id).join(',')}:${history.history_fingerprint}`}
          sku={history.sku_code} serials={serials} disabled={busy || !canWrite} onVerified={v => { setScans(v); invalidate(); }} />}
        {physicalRequired && <label><input type="checkbox" checked={physical} disabled={!canWrite} onChange={e => { setPhysical(e.target.checked); invalidate(); }} />已现场核对本次实物、数量和成色</label>}
        <button disabled={!ready || busy} onClick={() => void preview()}>核对本次办理</button>
      </fieldset>
    </section>}
    {prepared && <section className="panel" aria-label="确认成色纠正办理"><h2>确认{conditionActionLabels[actionOf(prepared)]}</h2>
      <p>{prepared.history.material_name} · {prepared.pending.original.reason}</p>
      <p>本次数量 {prepared.pending.original.action === 'submit_return_condition' ? prepared.pending.original.quantity
        : prepared.history.cases.find(c => c.case_id === (prepared.pending.original as { case_id: string }).case_id)?.quantity} {prepared.history.base_unit}
        · 原记录为{prepared.history.recorded_condition === 'new' ? '新件' : '旧件'}，纠正目标为坏件</p>
      {'serial_verifications' in prepared.pending.original && prepared.pending.original.serial_verifications.length > 0
        && <p>本次已核验 SN：{prepared.pending.original.serial_verifications.map(s => s.serial_no).join('、')}</p>}
      <p>{actionOf(prepared) === 'submit' ? '提交后冻结本次份额，等待独立复核。' : actionOf(prepared) === 'execute' ? '将把本案冻结份额按坏件成色入账。'
        : actionOf(prepared) === 'release' ? '将释放本案冻结份额，不执行成色纠正。' : '本次只记录决定，不改变库存；批准后仍需执行，拒绝或取消后仍需释放。'}</p>
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />已核对本次对象、证据和库存影响，确认继续</label>
      <button disabled={!confirmed || !ready || busy} onClick={() => void send()}>确认提交本次办理</button><button disabled={busy} onClick={invalidate}>取消本次确认</button>
    </section>}
  </section>;
}
function actionOf(p: PreparedCondition): Action { return p.pending.original.action === 'submit_return_condition' ? 'submit' : p.pending.original.action; }
