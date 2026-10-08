import { useEffect, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import { conditionActionLabels, conditionStatusLabels, type ConditionHistory } from './returnConditionHistory';

type Props = { identity: Identity; inboundId: string; rootId: string; unit: string;
  read(root: string, inbound: string): Promise<ConditionHistory> };
export default function FormalReturnConditionHistory({ identity, inboundId, rootId, unit, read }: Props) {
  const [value, setValue] = useState<ConditionHistory | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), epoch = useRef(0), pending = useRef(false);
  useEffect(() => { epoch.current++; pending.current = false; setValue(null); setError(''); setBusy(false);
    return () => { epoch.current++; }; }, [identity.person_id, identity.authorization_version, inboundId, rootId, read]);
  async function load() {
    if (pending.current) return;
    const at = epoch.current; pending.current = true; setBusy(true); setValue(null); setError('');
    try { const result = await read(rootId, inboundId); if (at === epoch.current) setValue(result); }
    catch { if (at === epoch.current) setError('暂时无法完整核验成色纠正历史，请稍后重新查询。'); }
    finally { if (at === epoch.current) { pending.current = false; setBusy(false); } }
  }
  return <section aria-label="成色纠正历史">
    <button disabled={busy} onClick={() => void load()}>{busy ? '正在核验成色纠正…' : '查看成色纠正历史'}</button>
    {error && <p role="alert">{error}</p>}
    {value && <>
      <p>历史冻结份额 {value.held_quantity} {unit}；已纠正 {value.corrected_quantity} {unit}；尚未申请 {value.unclaimed_quantity} {unit}。</p>
      <p>以上为历史份额，不代表当前可用库存。总部批准后仍需执行，撤回或拒绝后仍需释放冻结。</p>
      {!value.cases.length && <p>尚无成色纠正申请。</p>}
      {value.cases.map((c, index) => <article key={c.case_id}>
        <h4>第 {index + 1} 笔 · {conditionStatusLabels[c.status]}</h4>
        <p>申请数量 {c.quantity} {unit}</p>
        <ol>{value.events.filter(e => e.fact.case_id === c.case_id).map(e => <li key={e.fact.event_id}>
          <strong>{conditionActionLabels[e.fact.action]}</strong> · <time dateTime={e.occurred_at}>{new Date(e.occurred_at).toLocaleString('zh-CN')}</time>
          <p>{e.fact.reason}</p>
          <p>{e.fact.stock_effect === 'none' ? '本次未变动库存' : e.fact.stock_effect === 'freeze' ? '已记录冻结流水' : e.fact.stock_effect === 'unfreeze' ? '已记录释放流水' : '已记录成色纠正流水'}；附件 {e.evidence_file_ids.length} 份。</p>
        </li>)}</ol>
      </article>)}
    </>}
  </section>;
}
