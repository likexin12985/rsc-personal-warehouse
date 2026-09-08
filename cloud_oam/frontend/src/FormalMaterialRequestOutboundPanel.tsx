import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAccess, type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type OutboundInput, type OutboundOption, type OutboundPage, type OutboundSentinel, type OutboundStore, outboundUnits, validateOutboundPage, validateOutboundResult } from "./materialRequestOutbound";
import { hasOutboundRecovery, matchOutboundResult, recoverOutbound } from "./materialRequestOutboundRecovery";
import { Button, Field, Modal, showError } from "./ui";

type Selection = Readonly<{ before: MaterialRequestDetail; page: OutboundPage; option: OutboundOption; quantity: string; reason: string; serialIds: readonly string[] }>;
function matches(page: OutboundPage, detail: MaterialRequestDetail, lineId: string) {
  return page.request_id === detail.request_id && page.request_line_id === lineId && page.request_version === detail.request_version
    && page.revision_id === detail.current_revision_id && page.revision_no === detail.current_revision_no && same(page.state_axes, detail.states)
    && detail.lines.some(line => line.request_line_id === lineId && line.revision_id === page.revision_id);
}
export default function FormalMaterialRequestOutboundPanel({ adapter, access, detail, store, otherWriteBusy, otherWriteBlocked, onBlocking, onDetail }: {
  adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail | null;
  store: OutboundStore; otherWriteBusy: boolean; otherWriteBlocked: () => boolean;
  onBlocking: (blocked: boolean) => void; onDetail: (detail: MaterialRequestDetail) => void;
}) {
  const [page, setPage] = useState<OutboundPage | null>(null), [selection, setSelection] = useState<Selection | null>(null);
  const [running, setRunning] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const active = useRef(false), generation = useRef(0);
  const context = useRef({ adapter, access, store, requestId: detail?.request_id, requestVersion: detail?.request_version });
  if (context.current.adapter !== adapter || context.current.store !== store || !same(context.current.access, access)
      || context.current.requestId !== detail?.request_id || context.current.requestVersion !== detail?.request_version) {
    generation.current += 1; context.current = { adapter, access, store, requestId: detail?.request_id, requestVersion: detail?.request_version };
  }
  const stored = store.read(), blocked = stored.kind !== "missing";
  const otherBlocked = () => otherWriteBusy || otherWriteBlocked();
  const canWrite = hasOutboundRecovery(adapter) && typeof adapter.createOutbound === "function" && typeof adapter.listOutboundOptions === "function";
  async function recover() {
    const current = store.read();
    if (active.current || current.kind !== "valid" || !hasOutboundRecovery(adapter)) return;
    const turn = generation.current; active.current = true; setRunning(true); onBlocking(true); setError("");
    try {
      const outcome = await recoverOutbound(adapter, store, current.value, () => turn === generation.current);
      if (turn !== generation.current) return;
      setSelection(null); setPage(null); onDetail(outcome.detail); onBlocking(false);
      setMessage(`出库过账 ${outcome.command.posting_no} 已核验，实物出库数量 ${outcome.command.outbound_qty}。`);
    } catch (caught) { if (turn === generation.current) setError(showError(caught)); }
    finally { active.current = false; if (turn === generation.current) setRunning(false); }
  }
  const accessSignature = JSON.stringify(access);
  useEffect(() => {
    setPage(null); setSelection(null); setLoading(false); setRunning(false);
    const current = store.read(); onBlocking(current.kind !== "missing");
    if (current.kind === "valid" && access && !active.current) void recover();
    else if (current.kind === "corrupt" || current.kind === "unavailable") setError("出库核验存储不可用，写入保持暂停；保留原请求记录");
    return () => { generation.current += 1; onBlocking(store.read().kind !== "missing"); };
    // Recovery is always read-only and remains anchored to the original context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store, accessSignature, detail?.request_id, detail?.request_version]);
  async function load(lineId: string) {
    if (!detail || !access?.can_read_allocation_options || !adapter.listOutboundOptions || !canWrite || active.current || loading || blocked || otherBlocked()) return;
    const turn = generation.current; setLoading(true); setError("");
    try {
      const value = validateOutboundPage(await adapter.listOutboundOptions(detail.request_id, lineId));
      if (!matches(value, detail, lineId)) throw new Error("出库候选与需求版本不一致，请刷新详情");
      if (turn === generation.current) setPage(value);
    } catch (caught) { if (turn === generation.current) setError(showError(caught)); }
    finally { if (turn === generation.current) setLoading(false); }
  }
  async function submit() {
    if (!selection || !access || !hasOutboundRecovery(adapter) || !adapter.createOutbound || !adapter.listOutboundOptions || active.current || blocked || otherBlocked()) return;
    const turn = generation.current, { before, page: originalPage, option } = selection;
    active.current = true; setRunning(true); onBlocking(true); setError("");
    let persisted = false;
    try {
      const units = outboundUnits(selection.quantity), fraction = (selection.quantity.split(".")[1] ?? "").length;
      if (units <= 0n || units > outboundUnits(option.outboundable_qty) || fraction > option.quantity_scale || (!option.allow_fraction && units % 1000n !== 0n)) throw new Error("出库数量超过本笔余量或不符合物料精度");
      const quantity = `${units / 1000n}.${String(units % 1000n).padStart(3, "0")}`;
      const ids = [...selection.serialIds].sort(), usesSerials = ["serial", "lot_and_serial"].includes(option.tracking_mode);
      if (new Set(ids).size !== ids.length || ids.some(id => !option.serials.some(s => s.serial_id === id)) || (usesSerials ? units !== BigInt(ids.length) * 1000n : ids.length !== 0)) throw new Error("请选择与出库数量一致的原拣货 SN");
      const reason = selection.reason.trim(); if (!reason || reason.length > 500 || /[\x00-\x1f\x7f]/.test(reason)) throw new Error("请填写出库备注，最多 500 字");
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      const freshAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version || !freshAccess.can_read || !freshAccess.can_read_allocation_options || !same(access, freshAccess)) throw new Error("身份或出库授权已变化，请重新进入页面");
      const fresh = validateMaterialRequestDetail(await adapter.detailNoReplay(before.request_id), before.request_id);
      const freshPage = validateOutboundPage(await adapter.listOutboundOptions(before.request_id, originalPage.request_line_id));
      if (!matches(freshPage, fresh, originalPage.request_line_id) || !same(fresh, before)
          || !same(freshPage.items.find(row => row.pick_id === option.pick_id), option)) throw new Error("原拣货余量、余额或 SN 已变化，请重新选择");
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他写入正在进行或结果待核验，已停止出库提交");
      const headers = new Headers(mutationHeaders("material-request-outbound").headers), trace = headers.get("X-Request-ID") ?? "", key = headers.get("Idempotency-Key") ?? "";
      if (!trace || !key) throw new Error("无法生成出库请求坐标");
      const input: OutboundInput = { expected_request_version: before.request_version, pick_id: option.pick_id, target_stock_account_id: option.target_stock_account_id, outbound_qty: quantity, reason, source_balance_version: option.source_balance_version, source_ledger_cursor: option.source_ledger_cursor, serial_ids: ids };
      const sentinel: OutboundSentinel = { v: 1, kind: "outbound", trace, person_id: access.person_id, authorization_version: access.authorization_version,
        request_id: before.request_id, request_line_id: originalPage.request_line_id, revision_id: originalPage.revision_id, revision_no: originalPage.revision_no,
        allocation_id: option.allocation_id, source_stock_account_id: option.source_stock_account_id, target_stock_account_id: option.target_stock_account_id, state_axes: before.states, input };
      store.persist(sentinel); const saved = store.read(); if (saved.kind !== "valid" || !same(saved.value, sentinel)) throw new Error("出库坐标未能可靠保存，已停止提交");
      persisted = true;
      const result = validateOutboundResult(await adapter.createOutbound(before.request_id, input, { "X-Request-ID": trace, "Idempotency-Key": key }));
      matchOutboundResult(sentinel, result);
      if (turn !== generation.current) return;
      const outcome = await recoverOutbound(adapter, store, sentinel, () => turn === generation.current);
      if (turn !== generation.current) return;
      setSelection(null); setPage(null); onDetail(outcome.detail); onBlocking(false);
      setMessage(`出库过账 ${result.posting_no} 已保存并核验，数量 ${quantity} 已转入在途。请继续登记承运交接与运单。`);
    } catch (caught) { if (turn === generation.current) setError(`${showError(caught)}${persisted ? "。结果核验前请勿再次提交。" : ""}`); }
    finally { active.current = false; if (turn === generation.current) { setRunning(false); onBlocking(store.read().kind !== "missing"); } }
  }
  const disabled = running || blocked || otherWriteBusy;
  return <section className="opening-detail-section" aria-label="库存出库">
    <header><div><h3>出库确认</h3><p>逐笔确认实物已离开来源位置，库存转入原资产维度的在途账户。发运、签收和个人仓入库分别登记。</p></div></header>
    {error && <div className="alert alert-error">{error}</div>}
    {message && <div className="alert alert-info" role="status">{message}</div>}
    {blocked && <div className="alert alert-warning">出库操作结果待核验，暂停其他写入。<Button disabled={running || stored.kind !== "valid"} onClick={() => void recover()}>核验原出库操作</Button></div>}
    {detail && access?.can_read_allocation_options && canWrite && ["approved", "partially_approved"].includes(detail.states.request_status) && ["pending_pick", "picked", "outbound"].includes(detail.states.outbound_status) && <div className="form-actions">
      {detail.lines.filter(line => ["approved", "partially_approved"].includes(line.status)).map(line => <Button key={line.request_line_id} disabled={disabled || loading} onClick={() => void load(line.request_line_id)}>查看明细 {line.line_no} 可出库明细</Button>)}
    </div>}
    {page && <Modal title="可出库的拣货明细" wide onClose={() => { if (!running) setPage(null); }}>
      {page.items.length ? <div className="table-wrap"><table><thead><tr><th>出库单 / 原拣货单</th><th>位置 / 物料</th><th>已拣货 / 已出库</th><th>可出库</th><th>操作</th></tr></thead><tbody>
        {page.items.map(option => <tr key={option.pick_id}><td>{option.outbound_no}<br />{option.pick_no}</td><td>{option.location_name} / {option.sku_code} · {option.material_name}<br />{({ new: "新品", used: "旧件", damaged: "损坏", scrapped: "报废" })[option.condition_code]} · 批次 {option.lot_no ?? "无"}</td><td>{option.picked_qty} / {option.outbound_qty}</td><td>{option.outboundable_qty}</td><td><Button disabled={disabled} onClick={() => {
          if (!detail || !matches(page, detail, page.request_line_id) || active.current || otherBlocked() || store.read().kind !== "missing") return;
          setSelection({ before: detail, page, option, quantity: option.outboundable_qty, reason: "", serialIds: [] }); setPage(null); setError("");
        }}>选择出库</Button></td></tr>)}
      </tbody></table></div> : <p>当前明细没有可出库的明细。</p>}
    </Modal>}
    {selection && <Modal title="确认实物出库" onClose={() => { if (!running && !blocked) setSelection(null); }}>
      <div className="form-stack"><p>{selection.option.outbound_no} · {selection.option.pick_no} · {selection.option.location_name} · {selection.option.material_name} · {({ new: "新品", used: "旧件", damaged: "损坏", scrapped: "报废" })[selection.option.condition_code]} · 批次 {selection.option.lot_no ?? "无"}</p>
        <p>本笔当前可出库 {selection.option.outboundable_qty}</p>
        {error && <div className="alert alert-error">{error}</div>}
        <Field label="出库数量"><input aria-label="出库数量" inputMode="decimal" disabled={disabled} value={selection.quantity} onChange={event => setSelection({ ...selection, quantity: event.target.value })} /></Field>
        <Field label="出库备注"><input aria-label="出库备注" maxLength={500} disabled={disabled} value={selection.reason} onChange={event => setSelection({ ...selection, reason: event.target.value })} /></Field>
        {["serial", "lot_and_serial"].includes(selection.option.tracking_mode) && <fieldset className="reservation-serial-options" disabled={disabled}><legend>选择出库 SN（须与数量一致）</legend>{selection.option.serials.map(serial => <label className="reservation-serial-option" key={serial.serial_id}>
          <input type="checkbox" aria-label={`出库 SN ${serial.serial_no}`} checked={selection.serialIds.includes(serial.serial_id)} onChange={event => setSelection({ ...selection, serialIds: event.target.checked ? [...selection.serialIds, serial.serial_id] : selection.serialIds.filter(id => id !== serial.serial_id) })} /><span>{serial.serial_no}</span>
        </label>)}</fieldset>}
        <p>仅在实物已离开来源位置时确认。此次过账保留资产总量，不代表已交运、已签收或已入个人仓。</p>
        <div className="form-actions"><Button tone="secondary" disabled={disabled} onClick={() => setSelection(null)}>返回检查</Button><Button disabled={disabled} onClick={() => void submit()}>{running ? "正在核验结果" : "确认出库"}</Button></div>
      </div>
    </Modal>}
  </section>;
}
