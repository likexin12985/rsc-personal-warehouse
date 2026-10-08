import { useEffect, useRef, useState } from 'react';
import type { Identity, LossLine } from './formalLossReview';
import type { ReturnHistory, Share } from './lossReturnHistory';
import ReturnConditionHistory from './FormalReturnConditionHistory';
import type { ConditionHistory } from './returnConditionHistory';

const labels: Record<Share['stage'], string> = { not_outbound: '尚未出库', outbound_not_shipped: '已出库，未发运',
  shipped_unconfirmed: '已发运，未确认收货', accepted_not_inbound: '已接受，未入库', rejected: '已拒收', posted_inbound: '已入库' };
type Props = { identity: Identity; rootId: string; line: LossLine; read(root: string): Promise<ReturnHistory>;
  readCondition?(root: string, inbound: string): Promise<ConditionHistory>; onOpenCondition?(inbound: string): void };

export default function FormalLossReturnHistory({ identity, rootId, line, read, readCondition, onOpenCondition }: Props) {
  const [history, setHistory] = useState<ReturnHistory | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const epoch = useRef(0), inFlight = useRef(false);
  useEffect(() => {
    epoch.current++; inFlight.current = false; setHistory(null); setBusy(false); setError('');
    return () => { epoch.current++; };
  }, [identity.person_id, identity.authorization_version, rootId, read]);
  async function load() {
    if (inFlight.current) return;
    const current = epoch.current; inFlight.current = true; setBusy(true); setHistory(null); setError('');
    try {
      const value = await read(rootId);
      if (current === epoch.current) setHistory(value);
    } catch (error) {
      if (current === epoch.current) setError(error instanceof Error ? error.message : '暂时无法查询退回历史，请稍后刷新');
    } finally {
      if (current === epoch.current) { inFlight.current = false; setBusy(false); }
    }
  }
  const names = (ids: string[]) => ids.map(id => line.serials.find(s => s.serial_id === id)?.serial_no ?? id).join('、');
  return <section className="panel" aria-label="退回履约历史">
    <h2>退回履约历史</h2>
    <p>出库、发运、验收和入库分别核验。累计入库量不代表当前可回收库存。</p>
    <button disabled={busy} onClick={() => void load()}>{busy ? '正在核验退回历史…' : '查询退回历史'}</button>
    {error && <p role="alert">{error}</p>}
    {history && <>
      {history.lines.map(value => <div key={value.operation_line_id}>
        <div className="table-scroll"><table><caption>{line.material_name} · 原退回数量 {value.original_quantity} {line.base_unit}</caption>
          <thead><tr><th>履约阶段</th><th>数量</th><th>SN</th></tr></thead><tbody>
            {value.shares.map(share => <tr key={share.stage}><td data-label="履约阶段">{labels[share.stage]}</td>
              <td data-label="数量">{share.quantity}</td><td data-label="SN">{names(share.serial_ids) || '—'}</td></tr>)}
          </tbody></table></div>
        <p>接受量中破损 {value.damaged_accepted_quantity} {line.base_unit}；破损量已计入接受量，不另外累加。是否入库见上方阶段。</p>
        {!!value.shortage_observations.length && <div><p>历史短少记录（可能后来补收，不直接代表当前短少）：</p><ul>
          {value.shortage_observations.map((row, i) => <li key={row.receipt_line_id}>第 {i + 1} 次记录：{row.quantity} {line.base_unit}</li>)}
        </ul></div>}
      </div>)}
      {!!history.classification_exceptions.length && <div role="alert"><h3>历史入库成色需要核查</h3>
        <p>以下旧记录包含破损接受量，却按原成色入账。原记录已保留，尚未核验物料现状，也未执行库存纠正。</p>
        <ul>{history.classification_exceptions.map(issue => <li key={issue.inbound_line_id}>
          {issue.affected_quantity} {line.base_unit}：记录为{issue.recorded_condition === 'new' ? '新件' : '旧件'}，按验收应为坏件。
          {!!issue.affected_serial_ids.length && <span> SN：{names(issue.affected_serial_ids)}</span>}
          {onOpenCondition && <button disabled={busy} onClick={() => onOpenCondition(issue.inbound_line_id)}>办理成色纠正</button>}
          {readCondition && <ReturnConditionHistory identity={identity} rootId={rootId} inboundId={issue.inbound_line_id}
            unit={line.base_unit} read={readCondition} />}
        </li>)}</ul></div>}
    </>}
  </section>;
}
