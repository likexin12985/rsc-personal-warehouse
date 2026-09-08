import { useEffect, useRef, useState } from "react";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail } from "./formalMaterialRequests";
import { type FulfillmentPreparation, PREPARATION_BLOCKERS, preparationMatchesDetail, validateFulfillmentPreparation } from "./materialRequestFulfillmentPreparation";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { Button, Modal, showError } from "./ui";

export default function FormalMaterialRequestFulfillmentPreparationPanel({ adapter, access, detail, otherWriteBusy, otherWriteBlocked }: {
  adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail;
  otherWriteBusy: boolean; otherWriteBlocked: () => boolean;
}) {
  const [page, setPage] = useState<FulfillmentPreparation | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [onlyReady, setOnlyReady] = useState(false);
  const active = useRef(false), generation = useRef(0);
  const signature = JSON.stringify([access, detail.request_id, detail.request_version, detail.current_revision_id, detail.states, otherWriteBusy]);
  const context = useRef({ adapter, signature });
  if (context.current.adapter !== adapter || context.current.signature !== signature) {
    generation.current += 1;
    context.current = { adapter, signature };
  }
  useEffect(() => {
    setPage(null); setError(""); setLoading(false); setOnlyReady(false);
    return () => { generation.current += 1; };
  }, [adapter, signature]);
  const canRead = access?.can_read && access.can_read_allocation_options && typeof adapter.listFulfillmentPreparation === "function"
    && typeof adapter.loadIdentityNoReplay === "function" && typeof adapter.loadAccessNoReplay === "function" && typeof adapter.detailNoReplay === "function";
  const eligible = ["approved", "partially_approved"].includes(detail.states.request_status) && detail.states.outbound_status === "not_started";
  async function load(lineId: string) {
    if (!canRead || !access || !eligible || active.current || otherWriteBusy || otherWriteBlocked()) return;
    const turn = generation.current;
    const current = () => turn === generation.current;
    active.current = true; setLoading(true); setPage(null); setError(""); setOnlyReady(false);
    async function freshContext() {
      const [identityRaw, accessRaw, detailRaw] = await Promise.all([
        adapter.loadIdentityNoReplay!(), adapter.loadAccessNoReplay!(), adapter.detailNoReplay!(detail.request_id),
      ]);
      const identity = validateFormalMaterialRequestFreshIdentity(identityRaw);
      const freshAccess = validateFormalMaterialRequestAccess(accessRaw);
      const freshDetail = validateMaterialRequestDetail(detailRaw, detail.request_id);
      if (!access || identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version
          || !freshAccess.can_read || !freshAccess.can_read_allocation_options || !same(access, freshAccess)) throw new Error("身份或库存授权已变化，请重新进入需求页面");
      if (!same(detail, freshDetail)) throw new Error("需求已有更新，请重新打开详情后核对履约准备");
      return freshDetail;
    }
    try {
      await freshContext();
      if (!current() || otherWriteBlocked()) return;
      const result = validateFulfillmentPreparation(await adapter.listFulfillmentPreparation!(detail.request_id, lineId));
      const fresh = await freshContext();
      if (!preparationMatchesDetail(result, fresh, lineId)) throw new Error("履约清单与当前需求明细不一致，请刷新详情");
      if (current() && !otherWriteBlocked()) setPage(result);
    } catch (caught) {
      if (current()) setError(showError(caught));
    } finally {
      active.current = false;
      if (current()) setLoading(false);
    }
  }
  const shown = canRead && !otherWriteBusy && page && preparationMatchesDetail(page, detail, page.request_line_id) ? page : null;
  const rows = shown?.items.filter(row => !onlyReady || row.preparation_status === "ready_for_review") ?? [];
  return <section className="opening-detail-section" aria-label="履约准备">
    <header><div><h3>履约准备</h3><p>按原占用记录核对剩余数量与 SN。查看清单不会改变拣货、出库或发运状态。</p></div></header>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {otherWriteBusy && <p role="status">需求操作正在进行或结果待核验，完成后可刷新履约清单。</p>}
    {canRead && eligible && <div className="form-actions">{detail.lines.filter(line => ["approved", "partially_approved"].includes(line.status)).map(line =>
      <Button key={line.request_line_id} disabled={loading || otherWriteBusy} onClick={() => void load(line.request_line_id)}>查看明细 {line.line_no} 履约准备</Button>
    )}</div>}
    {loading && <p role="status">正在核验原占用、释放记录与库存快照…</p>}
    {shown && <Modal title="履约准备清单" wide onClose={() => setPage(null)}>
      <p>仅展示当前库存授权范围内的原占用。数量已核验至 {new Date(shown.projected_at).toLocaleString("zh-CN", { hour12: false })} 的库存快照。</p>
      <div className="form-actions">
        <Button tone="secondary" disabled={loading} onClick={() => void load(shown.request_line_id)}>刷新履约清单</Button>
        <Button tone="secondary" onClick={() => setOnlyReady(value => !value)}>{onlyReady ? "显示全部占用" : "只看可核对实物"}</Button>
      </div>
      {rows.length ? <div className="table-wrap"><table aria-label="原占用履约准备"><thead><tr>
        <th>原占用 / 来源</th><th>物料</th><th>原占用</th><th>已释放</th><th>仍占用</th><th>本次核验</th>
      </tr></thead><tbody>{rows.map(row => <tr key={row.reservation_id}>
        <td>{row.reservation_no}<br />{row.location_name}</td><td>{row.sku_code}<br />{row.material_name}</td>
        <td>{row.reserved_qty}</td><td>{row.released_qty}</td><td>{row.remaining_reserved_qty}</td>
        <td>{row.preparation_status === "ready_for_review" ? <span>可核对实物：{row.verified_held_qty}</span>
          : row.preparation_status === "released" ? <span>已全部释放</span>
          : <div><strong>待处理</strong>{row.blockers.map(reason => <p key={reason}>{PREPARATION_BLOCKERS[reason]}</p>)}</div>}
          {row.serials.length > 0 && <details><summary>查看本笔 SN（{row.serials.length}）</summary><ul>{row.serials.map(serial => <li key={serial.serial_id}>{serial.serial_no}</li>)}</ul></details>}
        </td>
      </tr>)}</tbody></table></div>
        : <p>{onlyReady ? "当前没有可核对实物的占用。可切换到全部占用查看原因。" : "当前授权范围内没有已建立的库存占用。"}</p>}
      <p>此清单用于出库前核对。实际拣货和出库仍需各自的履约单据及库存过账。</p>
    </Modal>}
  </section>;
}
