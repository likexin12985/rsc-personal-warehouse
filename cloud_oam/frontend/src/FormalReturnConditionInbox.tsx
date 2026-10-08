import { useEffect, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import type { ConditionAdapter, ConditionInbox } from './returnConditionAdapter';
import { conditionStatusLabels } from './returnConditionHistory';

type Props = { identity: Identity; adapter: Pick<ConditionAdapter, 'inbox'>; onOpen(inbound: string): void };
export default function FormalReturnConditionInbox({ identity, adapter, onOpen }: Props) {
  const [view, setView] = useState<'pending' | 'all'>('pending'), [page, setPage] = useState<ConditionInbox | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const epoch = useRef(0);
  async function load(after: string | null = null) {
    const captured = ++epoch.current; setBusy(true); setPage(null); setError('');
    try { const result = await adapter.inbox(view, after);
      if (captured !== epoch.current) return;
      if (result.person_id !== identity.person_id || result.authorization_version !== identity.authorization_version || result.view !== view) throw new Error();
      setPage(result);
    } catch { if (captured === epoch.current) setError('暂时无法核验案件列表，请刷新后重试。'); }
    finally { if (captured === epoch.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { epoch.current++; }; }, [identity.person_id, identity.authorization_version, adapter, view]);
  const visible = page?.person_id === identity.person_id && page.authorization_version === identity.authorization_version && page.view === view ? page : null;
  return <section className="page-stack"><div className="page-heading"><div><h1>成色纠正案件</h1>
    <p>显示当前权限范围内的案件。审批、执行和释放分别办理，列表不代表当前可用库存。</p></div>
    <button disabled={busy} onClick={() => void load()}>刷新案件列表</button></div>
    <label>案件范围<select aria-label="案件范围" value={view} onChange={e => setView(e.target.value as 'pending' | 'all')}>
      <option value="pending">尚未完成</option><option value="all">全部历史</option></select></label>
    {busy && <p role="status">正在核验案件…</p>}{error && <p role="alert">{error}</p>}
    {visible?.items.length === 0 && <p>当前范围没有符合条件的案件。</p>}
    {visible?.items.map(item => <article className="panel" key={item.inbound_line_id}><h2>{item.material_name}</h2>
      <p>{item.sku_code} · 原破损接受量 {item.historical_damaged_quantity} {item.base_unit}</p>
      <ul>{item.cases.map((c, index) => <li key={c.case_id}>第 {index + 1} 笔：{c.quantity} {item.base_unit} · {conditionStatusLabels[c.status]}</li>)}</ul>
      <button onClick={() => onOpen(item.inbound_line_id)}>查看并办理</button></article>)}
    {visible?.next_after_id && <button disabled={busy} onClick={() => void load(visible.next_after_id)}>下一页</button>}
  </section>;
}
