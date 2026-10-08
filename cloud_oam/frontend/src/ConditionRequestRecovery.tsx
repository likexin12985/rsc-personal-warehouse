import { useEffect, useMemo, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import { action, type Pending } from './returnConditionCommands';
import { conditionActionLabels, conditionStatusLabels, type Fact } from './returnConditionHistory';
import { browserStore, recover, seal, type Store, type Transport } from './returnConditionRecovery';

export default function ConditionRequestRecovery({ identity, transport, store: supplied, onSettled }: {
  identity: Identity; transport: Transport; store?: Store; onSettled?(): void | Promise<void>;
}) {
  const store = useMemo(() => supplied ?? browserStore(), [supplied]);
  const [rows, setRows] = useState<Pending[]>([]), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [outcome, setOutcome] = useState<Fact | null>(null);
  const [busy, setBusy] = useState(false), [closing, setClosing] = useState<Pending | null>(null), [accepted, setAccepted] = useState(false);
  const epoch = useRef(0), inFlight = useRef(false);
  useEffect(() => {
    epoch.current++; inFlight.current = false; setBusy(false); setError(''); setNotice(''); setOutcome(null); setClosing(null); setAccepted(false); setRows([]);
    try { setRows(store.list(identity.person_id)); } catch { setError('本机原请求不可完整读取，请保留记录并联系管理员。'); }
    return () => { epoch.current++; };
  }, [identity.person_id, identity.authorization_version, store, transport]);
  async function run(p: Pending, mode: 'recover' | 'prepare-close' | 'close') {
    if (inFlight.current || (mode === 'close' && !accepted)) return;
    const at = epoch.current; inFlight.current = true; setBusy(true); setError(''); setNotice(''); setOutcome(null);
    try {
      if (mode === 'prepare-close') {
        const context = await transport.context(action(p.original));
        if (at !== epoch.current) return;
        if (context.person_id !== identity.person_id || !context.can_read || !context.can_write) {
          setError('当前权限允许的操作不足，无法结束原请求；仍可尝试只读回查。'); return;
        }
        setClosing(p); setAccepted(false); return;
      }
      const result = await (mode === 'close' ? seal : recover)(transport, store, p, () => at === epoch.current);
      if (at !== epoch.current) return;
      setClosing(null); setAccepted(false); setRows(store.list(identity.person_id));
      if (result.status === 'found') setOutcome(result.result);
      setNotice(result.status === 'pending' ? '暂未查到确定结果。原请求已保留，不能重复发送或创建替代请求。'
        : result.status === 'sealed' ? '原请求已永久关闭，未因此变动库存。后续操作需重新核验并准备新请求。'
        : '已核对原请求的历史结果。本次回查没有重新执行操作，也不代表当前库存状态。');
      if (result.status !== 'pending') await onSettled?.();
    } catch {
      if (at === epoch.current) { setClosing(null); setAccepted(false);
        setError('原请求结果尚未完整确认，记录已保留。请稍后回查，不要重复发送。'); }
    } finally { if (at === epoch.current) { inFlight.current = false; setBusy(false); } }
  }
  return <section className="panel" aria-label="成色纠正原请求恢复">
    <h2>成色纠正原请求恢复</h2>
    <p>刷新或断网后，先查询原结果。查不到不表示操作未发生，也不能直接重发。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {outcome && <section aria-label="原请求历史结果"><h3>{conditionActionLabels[outcome.action]} · {conditionStatusLabels[outcome.status]}</h3>
      <p>本次数量 {outcome.quantity}；{outcome.stock_effect === 'none' ? '本次没有库存流水' : '已核验本次库存流水'}。</p>
      <p>原操作理由：{outcome.reason}</p><p>这是该请求发生时的结果，后续案件进展请查看成色纠正历史。</p>
    </section>}
    {!error && !rows.length && <p>本机当前账户没有待核验的成色纠正请求。</p>}
    <ul>{rows.map(p => <li key={p.original.request_id}>
      <p>{conditionActionLabels[action(p.original)]} · 请求 {p.original.request_id}</p>
      <button disabled={busy} onClick={() => void run(p, 'recover')}>查询原结果</button>
      <button disabled={busy} onClick={() => void run(p, 'prepare-close')}>结束未执行的原请求</button>
    </li>)}</ul>
    {closing && <section role="dialog" aria-modal="true" aria-label="确认永久关闭原请求">
      <p>系统会先回查。若已有结果，只读取结果；只有确认原请求未执行并成功封存后，才会结束它。此操作不会释放冻结或纠正库存。</p>
      <label><input type="checkbox" checked={accepted} disabled={busy} onChange={e => setAccepted(e.target.checked)} />我确认永久关闭这条原请求，不再用原请求重发</label>
      <button disabled={busy || !accepted} onClick={() => void run(closing, 'close')}>确认结束原请求</button>
      <button disabled={busy} onClick={() => { setClosing(null); setAccepted(false); }}>保留，继续回查</button>
    </section>}
  </section>;
}
