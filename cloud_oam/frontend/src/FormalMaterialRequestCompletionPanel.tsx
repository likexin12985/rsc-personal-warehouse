import { type VersionedMaterialRequestRemainder, remainingStages, validateVersionedMaterialRequestRemainder } from "./materialRequestRemainder";
import { useEffect, useState } from "react";
import type { FormalMaterialRequestAccess, FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type MaterialRequestCompletion, validateMaterialRequestCompletion } from "./materialRequestCompletion";
import { Button, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null;
  access: FormalMaterialRequestAccess | null; closed?: boolean };

export default function FormalMaterialRequestCompletionPanel(props: Props) {
  if (!props.detail || !props.access || !props.adapter.completionQuantities
      || !["approved", "partially_approved", "cancelled"].includes(props.detail.states.request_status)) return null;
  return <Completion key={`${props.detail.request_id}:${props.detail.request_version}:${JSON.stringify(props.access)}`}
    adapter={props.adapter} detail={props.detail} closed={props.closed} />;
}

function Completion({ adapter, detail, closed }: { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail; closed?: boolean }) {
  const [value, setValue] = useState<MaterialRequestCompletion | null>(null);
  const [remainder, setRemainder] = useState<VersionedMaterialRequestRemainder | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setValue(null); setRemainder(null); setError(""); setLoading(true);
    Promise.all([adapter.completionQuantities!(detail.request_id), adapter.remainingFulfillment?.(detail.request_id)]).then(([result, stages]) => {
      const checked = validateMaterialRequestCompletion(result, detail);
      const remaining = stages ? validateVersionedMaterialRequestRemainder(stages, detail) : null;
      if (remaining && remaining.lines.some(line => {
        const covered = checked.lines.find(item => item.request_line_id === line.request_line_id);
        return !covered || covered.posted_qty !== line.posted_qty || covered.cancelled_qty !== line.cancelled_qty;
      })) throw new Error("核对期间履约数量已变化，请重新读取");
      if (live) { setValue(checked); setRemainder(remaining); }
    }).catch(reason => { if (live) setError(showError(reason)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [adapter, detail, attempt]);
  return <section className="opening-detail-section" aria-label="结单核对">
    <header><div><h3>结单核对</h3><p>逐项核对最终批准数量的入账和取消依据。业务是否关闭以独立关闭记录为准。</p></div>
      <Button tone="secondary" disabled={loading} onClick={() => setAttempt(value => value + 1)}>重新核对</Button></header>
    {loading && <p role="status">正在核对审批、入账和取消记录…</p>}
    {error && <div className="alert alert-error" role="alert">{error}；本次核对未完成。</div>}
    {value && <>
      <p>{value.quantity_coverage_complete ? (closed ? "批准数量已全部入账或取消，业务已关闭。" : "批准数量已全部入账或取消，尚未完成结单。") : "仍有批准数量待入账或取消。"}</p>
      {value.pending_inbound_orders > 0 && <p>还有 {value.pending_inbound_orders} 张入账单待过账。</p>}
      {remainder && <p>未结束补货任务：{remainder.open_supply_tasks}；待确认替代：{remainder.pending_substitutions}。剩余数量需按当前阶段处理，不能直接视为可取消数量。</p>}
      <div className="completion-lines">{[...detail.lines].sort((a, b) => a.line_no - b.line_no).map(item => {
        const line = value.lines.find(row => row.request_line_id === item.request_line_id)!;
        const pending = remainder?.lines.find(row => row.request_line_id === item.request_line_id);
        const returned = remainder?.schema_version === "2.0"
          ? remainder.lines.find(row => row.request_line_id === item.request_line_id) : null;
        return <article className="completion-line" key={line.request_line_id} aria-label={`需求明细 ${item.line_no}`}>
          <h4>需求明细 {item.line_no}</h4>
          <dl><div><dt>最终批准</dt><dd>{line.approved_qty}</dd></div>
            <div><dt>个人仓已入账</dt><dd>{line.posted_qty}</dd></div>
            <div><dt>已取消</dt><dd>{line.cancelled_qty}</dd></div>
            <div><dt>待入账或取消</dt><dd>{line.remaining_qty}</dd></div></dl>
          {pending && <dl aria-label={`明细 ${item.line_no} 剩余履约分布`}>{Object.entries(remainingStages).map(([key, label]) =>
            <div key={key}><dt>{label}</dt><dd>{pending[key as keyof typeof remainingStages]}</dd></div>)}</dl>}
          {returned && <dl aria-label={`明细 ${item.line_no} 退回补偿分布`}>
            <div><dt>已退回入账待补偿</dt><dd>{returned.returned_pending_compensation_qty}</dd></div>
            <div><dt>未履约已取消</dt><dd>{returned.unfulfilled_cancelled_qty}</dd></div>
            <div><dt>退回后已补偿取消</dt><dd>{returned.return_compensated_qty}</dd></div>
          </dl>}
        </article>;
      })}</div>
    </>}
  </section>;
}
