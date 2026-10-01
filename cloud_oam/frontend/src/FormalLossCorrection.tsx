import FormalLossReturnStop from './FormalLossReturnStop';
import FormalLossReturnHistory from './FormalLossReturnHistory';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import { DISPOSITIONS, type Flow, type Pending } from './lossCorrectionContracts';
import type { Adapter, Detail } from './lossCorrectionAdapter';
import { browserStore, execute, recover, seal, type Store } from './lossCorrectionRecovery';
import './lossExecution.css';
import './lossCorrection.css';

const names = { restore_available: '恢复可用', convert_used: '转旧件', convert_damaged: '转坏件', return_to_region: '退回区域仓', scrap: '报废' };
const actions = { inverses: '冲销', approvals: '批准纠正', executions: '执行纠正' };
const states = { active_execution: '已有有效处置', awaiting_approval: '已冲销，等待独立审批', awaiting_execution: '已批准，待执行纠正', dedicated_compensation_required: '需要专门补偿流程' };
const kinds = { original_execution: '原处置记账', inverse: '冲销记账', approval: '纠正批准（不改变库存）', correction_execution: '纠正记账' };
const noRights = { inverses: false, approvals: false, executions: false };
const message = (e: unknown) => e instanceof Error ? e.message : '结果未确认，请保留原请求并回查';
type Props = { identity: Identity; rootId: string; adapter: Adapter; store?: Store; onBack(): void };

export default function FormalLossCorrection({ identity, rootId, adapter, store: supplied, onBack }: Props) {
  const store = useMemo(() => supplied ?? browserStore(), [supplied]);
  const [detail, setDetail] = useState<Detail | null>(null), [pending, setPending] = useState<Pending[]>([]);
  const [rights, setRights] = useState(noRights), [storageReady, setStorageReady] = useState(false);
  const [why, setWhy] = useState(''), [disposition, setDisposition] = useState(''), [decision, setDecision] = useState('');
  const [prepared, setPrepared] = useState<Pending | null>(null), [sealTarget, setSealTarget] = useState<Pending | null>(null);
  const [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const epoch = useRef(0), active = useRef(false);
  const valid = (captured: number) => active.current && epoch.current === captured;
  const resetPreparation = () => { setPrepared(null); setConfirmed(false); };
  function local(captured: number) {
    try {
      const values = store.list(identity.person_id).filter(p => p.command.root_disposition_id === rootId);
      if (valid(captured)) { setPending(values); setStorageReady(true); }
    } catch (e) { if (valid(captured)) { setStorageReady(false); setError(message(e)); } }
  }
  async function refreshDetail(captured: number) {
    const context = await adapter.context(), value = await adapter.describe(rootId);
    if (context.person_id !== identity.person_id || value.source.person_id !== identity.person_id || value.source.root_disposition_id !== rootId || context.authorization_version < identity.authorization_version || context.authorization_version !== value.source.authorization_version || !context.can_read) throw new Error('当前身份或读取权限变化，请刷新');
    if (valid(captured)) { setDetail(value); setRights(context.can_write); setDecision(''); setDisposition(''); }
  }
  async function load() {
    const captured = epoch.current; setBusy(true); setError(''); setDetail(null); setRights(noRights); resetPreparation(); setSealTarget(null); local(captured);
    try { await refreshDetail(captured); } catch (e) { if (valid(captured)) setError(message(e)); }
    finally { if (valid(captured)) setBusy(false); }
  }
  useEffect(() => {
    active.current = true; epoch.current++; setWhy(''); setDecision(''); setDisposition(''); setNotice(''); setPending([]); void load();
    return () => { active.current = false; epoch.current++; };
  }, [identity.person_id, identity.authorization_version, rootId, adapter, store]);
  useEffect(() => {
    const changed = () => { local(epoch.current); resetPreparation(); setSealTarget(null); };
    window.addEventListener('storage', changed); return () => window.removeEventListener('storage', changed);
  }, [identity.person_id, rootId, store]);
  async function preview(flow: Flow) {
    if (busy || !storageReady || pending.length || !rights[flow]) return;
    const captured = epoch.current; setBusy(true); setError(''); resetPreparation();
    try {
      const result = await adapter.prepare(rootId, flow, why.trim(), flow === 'approvals' ? disposition : flow === 'executions' ? decision : undefined);
      if (valid(captured)) setPrepared(result);
    } catch (e) { if (valid(captured)) setError(message(e)); }
    finally { if (valid(captured)) setBusy(false); }
  }
  async function action(p: Pending, mode: 'execute' | 'recover' | 'seal') {
    if (busy || (mode === 'execute' && !confirmed)) return;
    const captured = epoch.current; setBusy(true); setError(''); setNotice(''); resetPreparation(); setSealTarget(null);
    try {
      const result = await ({ execute, recover, seal }[mode])(adapter, store, p, () => valid(captured));
      if (!valid(captured)) return;
      setNotice(result.status === 'pending' ? '尚未查到原请求结果，已保留原请求。请继续回查，不能据此再次提交。'
        : result.status === 'sealed' ? '本次原请求已永久封存，不能迟到执行；已经完成的记账不会被撤销。'
        : p.flow === 'inverses' ? '已核验原请求：冲销已记账，对应物资恢复冻结。仍需独立审批和纠正执行。'
        : p.flow === 'approvals' ? '已核验原请求：纠正决定已批准，库存未改变。'
        : '已核验原请求：本次纠正已记账。当前库存请到库存页核验。');
      await refreshDetail(captured);
    } catch (e) { if (valid(captured)) { setError(message(e)); setDetail(null); setRights(noRights); } }
    finally { if (valid(captured)) { local(captured); setBusy(false); } }
  }
  const source = detail?.source, selected = source?.approval_choices.find(c => c.correction_decision_id === decision);
  const originalReturn = source?.history.some(h => h.kind === 'original_execution' && h.fact_id === rootId && h.disposition === 'return_to_region') ?? false;
  const blocked = busy || !storageReady || !!pending.length || !why.trim();
  return <section className="page-stack loss-execution-page loss-correction-page">
    <div className="page-heading"><div><h1>报损纠正</h1><p>冲销、独立审批和纠正执行分别确认，所有历史记录保留。</p></div>
      <div className="correction-heading-actions"><button disabled={busy} onClick={onBack}>返回报损处置</button><button disabled={busy} onClick={() => void load()}>刷新</button></div></div>
    {error && <p className="alert alert-error" role="alert">{error}</p>}{notice && <p className="alert" role="status">{notice}</p>}
    {busy && <p role="status">正在核验，请稍候…</p>}
    {!storageReady && <p role="alert">原请求存储不可用，已停止新操作。请保留浏览器数据。</p>}
    {!!pending.length && <section className="panel"><h2>本单原请求待核验</h2><p>中断后先回查；不会自动重发，也不会自动封存。</p>
      {pending.map(p => <div className="toolbar" key={p.command.request_id}><span>{actions[p.flow]}请求</span>
        <button disabled={busy} onClick={() => void action(p, 'recover')}>回查原请求</button>
        <button disabled={busy || !rights[p.flow]} onClick={() => setSealTarget(p)}>永久封存原请求</button></div>)}
    </section>}
    {sealTarget && <section className="panel" role="alertdialog" aria-label="确认封存纠正请求"><h2>永久封存本次原请求？</h2>
      <p>先回查是否已完成；未完成时，永久阻止该原请求迟到执行。这不会撤销已有审批或库存记账。</p>
      <button disabled={busy || !rights[sealTarget.flow]} onClick={() => void action(sealTarget, 'seal')}>确认永久封存</button>
      <button disabled={busy} onClick={() => setSealTarget(null)}>取消封存</button></section>}
    {detail && source && <>
      <section className="panel" aria-label="原报损明细"><h2>{detail.report.operation_no} · {detail.line.material_name}</h2>
        <p>{detail.line.sku_code} · {source.quantity} {detail.line.base_unit}</p><p>{detail.report.requester_name} · {detail.report.owner_org_name} · {detail.report.source_location_name}</p>
        <p>原报损原因：{detail.report.reason}</p>{!!detail.line.serials.length && <p>SN：{detail.line.serials.map(s => s.serial_no).join('、')}</p>}
        <p className="correction-state">{states[source.chain_state]}</p><p>这里展示已核验的历史事实，当前库存需单独查询。</p>
      </section>
      {source.chain_state === 'dedicated_compensation_required' ? <section className="panel"><h2>需要专门处理</h2><p>{originalReturn ? '先核验下方退回状态；仅整批尚未出库的原退回可停止，已有履约须办理对应补偿。' : '原处置涉及报废，必须核验实物和生命周期，本页暂不提供报废冲销。'}</p></section> :
        <section className="panel" aria-label="纠正操作"><h2>选择本次操作</h2>
          <label>本次操作理由<textarea value={why} maxLength={500} disabled={busy} onChange={e => { setWhy(e.target.value); resetPreparation(); }} /></label>
          {source.inverse_preview_reference && <div><p>冲销将撤销该次有效处置的库存影响，将对应物资恢复冻结；不会删除原记录。</p>
            <button disabled={blocked || !rights.inverses} onClick={() => void preview('inverses')}>预览冲销</button>{!rights.inverses && <p>当前没有冲销权限。</p>}</div>}
          {source.approval_reference && <fieldset disabled={busy}><legend>独立纠正审批</legend><p>批准仅形成纠正决定，不改变库存。</p>
            <label>纠正处置<select value={disposition} onChange={e => { setDisposition(e.target.value); resetPreparation(); }}><option value="">请选择处置</option>
              {DISPOSITIONS.map(d => <option key={d} value={d}>{names[d]}</option>)}</select></label>
            {(disposition === 'return_to_region' || disposition === 'scrap') && <p>此处只批准决定；相应执行流程尚未开放，批准后仍保持待执行。</p>}
            <button disabled={blocked || !rights.approvals || !disposition} onClick={() => void preview('approvals')}>核对审批决定</button>{!rights.approvals && <p>当前没有纠正审批权限。</p>}
          </fieldset>}
          {!!source.approval_choices.length && <fieldset disabled={busy}><legend>执行已批准的纠正</legend><p>请选择准确审批决定，系统不会自动选择最新一条。</p>
            <label>已批准的纠正决定<select value={decision} onChange={e => { setDecision(e.target.value); resetPreparation(); }}><option value="">请选择审批决定</option>
              {source.approval_choices.map((c, n) => <option value={c.correction_decision_id} key={c.correction_decision_id}>{n + 1}. {names[c.disposition]} · {c.reason}</option>)}</select></label>
            {selected && <p>选择的审批：{names[selected.disposition]} · {selected.reason}</p>}
            {selected?.execution_mode === 'dedicated_flow_required' ? <p>该批准需要专门的退回或报废执行流程，本页暂不能执行，物资仍保持冻结。</p> :
              <button disabled={blocked || !rights.executions || !selected} onClick={() => void preview('executions')}>预览纠正执行</button>}
            {!rights.executions && <p>当前没有纠正执行权限。</p>}
          </fieldset>}
        </section>}
      {originalReturn && <>
        <FormalLossReturnStop identity={identity} rootId={rootId} line={detail.line} adapter={adapter.returnStop} onSettled={load} />
        <FormalLossReturnHistory identity={identity} rootId={rootId} line={detail.line} read={adapter.history} />
      </>}
      <section className="panel" aria-label="纠正历史"><h2>完整纠正历史</h2><div className="table-scroll"><table><thead><tr><th>事实</th><th>处置 / 数量</th><th>时间</th></tr></thead><tbody>
        {source.history.map(h => <tr key={h.fact_id}><td data-label="事实"><span>{kinds[h.kind]}</span></td><td data-label="处置 / 数量"><span>{h.disposition ? names[h.disposition] : '恢复冻结'}{h.quantity ? ` · ${h.quantity}` : ''}</span></td>
          <td data-label="时间"><span>{new Date(h.created_at).toLocaleString('zh-CN')}</span></td></tr>)}
      </tbody></table></div></section>
    </>}
    {prepared && <section className="panel" aria-label="确认纠正操作"><h2>确认{actions[prepared.flow]}</h2><p>{detail?.line.material_name} · {prepared.source.quantity} {detail?.line.base_unit}</p><p>理由：{prepared.command.reason}</p>
      {prepared.flow === 'approvals' ? <p>批准处置：{names[prepared.command.disposition!]}。本次只记录审批，库存不会改变。</p> :
        <p>{prepared.flow === 'inverses' ? '本次将对应物资恢复冻结，之后仍需独立审批。' : `本次按已选择的审批${names[prepared.preview!.disposition!]}，将改变库存状态。`}</p>}
      <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />已核对原处置、数量、理由和本次操作，确认继续</label>
      <button disabled={busy || !confirmed || !storageReady || !!pending.length || !rights[prepared.flow]} onClick={() => void action(prepared, 'execute')}>确认{actions[prepared.flow]}</button>
      <button disabled={busy} onClick={resetPreparation}>取消本次操作</button>
    </section>}
  </section>;
}
