import { useEffect, useRef, useState } from 'react';
import type { Identity } from './formalLossReview';
import type { ConditionAdapter } from './returnConditionAdapter';
import type { ConditionHistory } from './returnConditionHistory';
export default function ReceiptConditionSources({ identity, receipt, shipment, root, read, onOpen, disabled }: {
  identity: Identity; receipt: string; shipment: string; root: string; read: ConditionAdapter['receipt'];
  onOpen(inbound: string): void; disabled: boolean;
}) {
  const [items, setItems] = useState<ConditionHistory[] | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const epoch = useRef(0), working = useRef(false);
  useEffect(() => { epoch.current++; working.current = false; setItems(null); setBusy(false); setError('');
    return () => { epoch.current++; }; }, [identity.person_id, identity.authorization_version, receipt, shipment, root, read]);
  async function load() {
    if (disabled || working.current) return; const generation = epoch.current; working.current = true;
    setBusy(true); setItems(null); setError('');
    try { const rows = await read(receipt, shipment, root); if (generation === epoch.current) setItems(rows); }
    catch { if (generation === epoch.current) setError('暂时无法完整核验本次入库成色，请稍后重新查询。'); }
    finally { if (generation === epoch.current) { working.current = false; setBusy(false); } }
  }
  return <section aria-label="本次验收的历史成色核查">
    <button disabled={disabled || busy} onClick={() => void load()}>核查本次入库成色</button>
    {busy && <p role="status">正在核验原入库记录…</p>}{error && <p role="alert">{error}</p>}
    {items && !items.length && <p>本次核验未发现按新件或旧件入账的历史破损份额。</p>}
    {!!items?.length && <ul>{items.map(item => <li key={item.inbound_line_id}>
      {item.sku_code} · {item.material_name} · 历史破损份额 {item.historical_damaged_quantity} {item.base_unit}
      <button disabled={disabled || busy} onClick={() => onOpen(item.inbound_line_id)}>查看并办理成色纠正</button>
    </li>)}</ul>}
  </section>;
}
