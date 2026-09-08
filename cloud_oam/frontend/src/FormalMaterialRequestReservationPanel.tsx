import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAccess, type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail } from "./formalMaterialRequests";
import { validateMaterialRequestReservationMutationResult } from "./formalMaterialRequestReservationCommandStatus";
import { type MaterialRequestReservationOption, type MaterialRequestReservationOptionPage, reservationQuantityUnits as units, reservationUsesSerials, validateMaterialRequestReservationOptionPage } from "./formalMaterialRequestReservationOptions";
import { type ReservationRecoveryStore, type ReservationSentinel, axesExceptReservationMatch, recoverReservationCommand, reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { Button, Field, Modal, showError } from "./ui";

type RecoveryAdapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter, "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay" | "reservationCommandStatusNoReplay">>;
type WriteAdapter = RecoveryAdapter & Required<Pick<FormalMaterialRequestAdapter, "createReservation" | "listReservationOptions">>;
type Selection = Readonly<{ before: MaterialRequestDetail; page: MaterialRequestReservationOptionPage; option: MaterialRequestReservationOption; quantity: string; serialIds: readonly string[] }>;
function hasRecovery(adapter: FormalMaterialRequestAdapter): adapter is RecoveryAdapter {
  return [adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay, adapter.detailNoReplay, adapter.reservationCommandStatusNoReplay].every((method) => typeof method === "function");
}
function hasWrite(adapter: FormalMaterialRequestAdapter): adapter is WriteAdapter {
  return hasRecovery(adapter) && typeof adapter.createReservation === "function" && typeof adapter.listReservationOptions === "function";
}
function quantity(value: string, option: MaterialRequestReservationOption): string {
  const amount = units(value); const [whole, fraction = ""] = value.split(".");
  if (amount <= 0n || amount > units(option.reservable_qty)) throw new Error("预留数量必须大于零且不超过当前可预留数量");
  if (fraction.length > option.quantity_scale) throw new Error(`该物料数量最多保留 ${option.quantity_scale} 位小数`);
  return `${whole}.${fraction.padEnd(3, "0")}`;
}
function pageMatches(page: MaterialRequestReservationOptionPage, detail: MaterialRequestDetail, lineId: string): boolean {
  const line = detail.lines.find((item) => item.request_line_id === lineId);
  return page.request_id === detail.request_id && page.request_line_id === lineId && page.request_version === detail.request_version
    && page.current_revision_id === detail.current_revision_id && page.current_revision_no === detail.current_revision_no
    && Boolean(line && line.revision_id === page.current_revision_id && line.revision_no === page.current_revision_no
      && line.material_id === page.material_id && ["approved", "partially_approved"].includes(line.status));
}
export default function FormalMaterialRequestReservationPanel({ adapter, access, detail, store, otherWriteBusy, otherWriteBlocked, onBlocking, onDetail }: {
  adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail | null;
  store: ReservationRecoveryStore; otherWriteBusy: boolean; otherWriteBlocked?: () => boolean;
  onBlocking: (blocked: boolean) => void; onDetail: (detail: MaterialRequestDetail) => void;
}) {
  const [page, setPage] = useState<MaterialRequestReservationOptionPage | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const active = useRef(false);
  const stored = store.read(); const blocked = stored.kind !== "missing";
  const accessSignature = JSON.stringify(access);
  const currentContext = useRef({ adapter, access, requestId: detail?.request_id });
  // Invalidate an in-flight continuation as soon as a changed route or identity renders.
  if (currentContext.current.adapter !== adapter || !same(currentContext.current.access, access) || currentContext.current.requestId !== detail?.request_id) {
    generation.current += 1; currentContext.current = { adapter, access, requestId: detail?.request_id };
  }
  const otherBlocked = () => otherWriteBusy || Boolean(otherWriteBlocked?.());
  async function recover() {
    if (active.current) return;
    const original = store.read();
    if (original.kind !== "valid" || !hasRecovery(adapter)) { setError("预留恢复记录或只读核验通道不可用，写入保持暂停"); onBlocking(true); return; }
    const turn = generation.current; active.current = true; setRunning(true); onBlocking(true); setError("");
    try {
      const outcome = await recoverReservationCommand(adapter, store, original.value, () => turn === generation.current);
      if (turn !== generation.current) return;
      setSelection(null); setPage(null); onDetail(outcome.detail); onBlocking(false);
      setMessage(`预留单 ${outcome.command.reservation_no} 已核验，数量 ${outcome.command.reserved_qty}；库存流水 ${outcome.command.reserve_transaction_no}。`);
    } catch (caught) { if (turn === generation.current) setError(showError(caught)); }
    finally { active.current = false; if (turn === generation.current) setRunning(false); }
  }
  useEffect(() => {
    const current = store.read(); setSelection(null); setPage(null); setLoading(false); setRunning(false);
    onBlocking(current.kind !== "missing");
    if (current.kind === "valid" && access && !active.current) void recover();
    else if (current.kind === "corrupt" || current.kind === "unavailable") setError("预留恢复存储不可用或包含旧记录，写入保持暂停；不得丢弃原请求坐标");
    return () => { generation.current += 1; onBlocking(store.read().kind !== "missing"); };
    // A recovery remains bound to the original identity and route; it never submits a POST.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store, accessSignature, detail?.request_id]);
  async function loadOptions(lineId: string) {
    if (!detail || !access?.can_read_allocation_options || !hasWrite(adapter) || active.current || loading || blocked || otherBlocked()) return;
    const turn = generation.current; setLoading(true); setError("");
    try {
      const result = validateMaterialRequestReservationOptionPage(await adapter.listReservationOptions(detail.request_id, lineId));
      if (!pageMatches(result, detail, lineId)) throw new Error("预留候选与需求、明细或修订版本不一致，请刷新详情");
      if (turn === generation.current) setPage(result);
    } catch (caught) { if (turn === generation.current) setError(showError(caught)); }
    finally { if (turn === generation.current) setLoading(false); }
  }
  function choose(option: MaterialRequestReservationOption) {
    if (!page || !detail || active.current || otherBlocked() || store.read().kind !== "missing") return;
    if (!pageMatches(page, detail, page.request_line_id)) { setError("需求已变化，请重新读取预留候选"); return; }
    setSelection({ before: detail, page, option, quantity: option.reservable_qty.includes(".") ? option.reservable_qty.replace(/\.?0+$/, "") : option.reservable_qty, serialIds: [] });
    setPage(null); setError(""); setMessage("");
  }
  async function submit() {
    if (!selection || !access || !hasWrite(adapter) || active.current || otherBlocked() || store.read().kind !== "missing") return;
    const turn = generation.current; const { before, option: original, page: originalPage } = selection;
    active.current = true; setRunning(true); setError(""); onBlocking(true);
    let persisted = false;
    try {
      const reservedQty = quantity(selection.quantity, original);
      const serialIds = [...selection.serialIds].sort();
      if (new Set(serialIds).size !== serialIds.length || serialIds.some((id) => !original.serial_options.some((candidate) => candidate.serial_id === id))
          || (reservationUsesSerials(original) ? reservedQty !== `${serialIds.length}.000` : serialIds.length !== 0)) throw new Error("请从当前候选勾选与预留数量一致的 SN");
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      const freshAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version
          || !freshAccess.can_read || !freshAccess.can_read_allocation_options || !same(access, freshAccess)) throw new Error("登录身份或预留权限已变化，请重新进入页面");
      const fresh = validateMaterialRequestDetail(await adapter.detailNoReplay(before.request_id), before.request_id);
      if (!same(fresh, before)) throw new Error("需求详情已变化，请重新读取并选择预留候选");
      const freshPage = validateMaterialRequestReservationOptionPage(await adapter.listReservationOptions(before.request_id, originalPage.request_line_id));
      const freshOption = freshPage.items.find((item) => item.allocation_id === original.allocation_id);
      if (!pageMatches(freshPage, fresh, originalPage.request_line_id) || freshPage.ledger_cursor !== originalPage.ledger_cursor || !same(freshOption, original)) throw new Error("分配余量、货源余额或 SN 候选已变化，请重新选择");
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他写入正在进行或结果待核验，已停止预留提交");
      const headers = new Headers(mutationHeaders("material-request-reservation").headers);
      const trace = headers.get("X-Request-ID") || ""; const idempotencyKey = headers.get("Idempotency-Key") || "";
      if (!trace || !idempotencyKey) throw new Error("无法生成预留请求坐标");
      const sentinel: ReservationSentinel = { v: 2, kind: "material_request_reservation", x_request_id: trace,
        person_id: access.person_id, authorization_version: access.authorization_version, request_id: before.request_id,
        request_line_id: originalPage.request_line_id, allocation_id: original.allocation_id, request_version: before.request_version,
        revision_id: before.current_revision_id!, revision_no: before.current_revision_no, reserved_qty: reservedQty,
        source_stock_account_id: original.stock_account_id, source_balance_version: original.balance_version, source_ledger_cursor: original.ledger_cursor,
        serial_ids: Object.freeze(serialIds), state_axes: before.states };
      store.persist(sentinel);
      const saved = store.read();
      if (saved.kind !== "valid" || !same(saved.value, sentinel)) throw new Error("预留请求坐标保存后回读不一致，已停止提交");
      persisted = true;
      const result = validateMaterialRequestReservationMutationResult(await adapter.createReservation(before.request_id, {
        expected_request_version: before.request_version, request_line_id: originalPage.request_line_id, allocation_id: original.allocation_id,
        reserved_qty: reservedQty, source_balance_version: original.balance_version, source_ledger_cursor: original.ledger_cursor, serial_ids: serialIds,
      }, { "X-Request-ID": trace, "Idempotency-Key": idempotencyKey }));
      if (result.request_id !== before.request_id || result.request_line_id !== sentinel.request_line_id || result.allocation_id !== sentinel.allocation_id
          || result.request_version !== before.request_version + 1 || result.current_request_version !== result.request_version
          || result.revision_id !== before.current_revision_id || result.revision_no !== before.current_revision_no
          || result.reserved_qty !== reservedQty || result.source_stock_account_id !== original.stock_account_id
          || result.source_balance_version !== original.balance_version || result.source_ledger_cursor !== original.ledger_cursor
          || !same([...result.serial_ids].sort(), serialIds) || !axesExceptReservationMatch(before.states, result.state_axes)) throw new Error("预留响应与原意图或独立状态轴不一致，继续保留原请求坐标");
      const reread = validateMaterialRequestDetail(await adapter.detailNoReplay(before.request_id), before.request_id);
      if (reread.request_version !== result.request_version || reread.current_revision_id !== result.revision_id || reread.current_revision_no !== result.revision_no
          || !same(reread.states, result.state_axes) || !same(before.lines, reread.lines)
          || !same(before.supply_tasks, reread.supply_tasks) || !same(before.approval_history, reread.approval_history)) throw new Error("预留事实与详情精确回读不一致，继续保留原请求坐标");
      const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
      if (!same(afterIdentity, identity) || !same(afterAccess, freshAccess) || turn !== generation.current) throw new Error("核验期间身份、权限或页面已变化，继续保留原请求坐标");
      const current = store.read();
      if (current.kind !== "valid" || !same(current.value, sentinel)) throw new Error("预留原请求坐标已变化，禁止清理");
      store.clear(trace); onBlocking(false); setSelection(null); onDetail(reread);
      setMessage(`预留单 ${result.reservation_no} 已保存并精确回读，数量 ${reservedQty}；库存流水 ${result.reserve_transaction_no}。`);
    } catch (caught) { if (turn === generation.current) setError(`${showError(caught)}${persisted ? "。结果核验完成前，请勿再次提交。" : ""}`); }
    finally {
      active.current = false;
      if (turn === generation.current) { setRunning(false); onBlocking(store.read().kind !== "missing"); }
    }
  }
  const disabled = running || blocked || otherWriteBusy;
  return <section className="opening-detail-section" aria-label="库存预留">
    <header><div><h3>库存预留 / 占用</h3><p>从已分配货源选择可用库存；确认后生成独立占用事实和库存流水。</p></div></header>
    {error && <div className="alert alert-error">{error}</div>}
    {message && <div className="alert alert-info" role="status">{message}</div>}
    {blocked && <div className="alert alert-warning">预留操作结果待核验，当前页面暂停其他写入。
      <Button disabled={running || stored.kind !== "valid"} onClick={() => void recover()}>{running ? "正在核验" : "核验原预留操作"}</Button>
    </div>}
    {detail && <p>当前占用状态：{({ not_reserved: "未占用", pending: "待完成占用", reserved: "已占用", partially_released: "部分释放", released: "已释放", fulfilled: "已履约" } as Record<string, string>)[detail.states.reservation_status]}。出库、发运、物流签收、OAM 收货、个人仓入库、通知及对账分别跟踪。</p>}
    {detail && access?.can_read_allocation_options && hasWrite(adapter) && <div className="table-wrap"><table>
      <thead><tr><th>明细</th><th>已批准数量</th><th>操作</th></tr></thead><tbody>
        {detail.lines.filter((line) => ["approved", "partially_approved"].includes(line.status)).map((line) => <tr key={line.request_line_id}><td>{line.line_no}</td><td>{line.final_approved_qty}</td><td><Button disabled={disabled || loading} onClick={() => void loadOptions(line.request_line_id)}>查看可预留库存</Button></td></tr>)}
      </tbody></table></div>}
    {page && <Modal title="可预留库存候选" wide onClose={() => { if (!running) setPage(null); }}>
      <p>库存流水游标 {page.ledger_cursor} · 投影时间 {page.projected_at || "—"}</p>
      {page.items.length ? <div className="table-wrap"><table><thead><tr><th>分配单</th><th>货源</th><th>物料 / 批次</th><th>分配 / 已预留</th><th>可预留</th><th>操作</th></tr></thead><tbody>
        {page.items.map((item) => <tr key={item.allocation_id}><td>{item.allocation_no}</td><td>{item.owner_org_name} / {item.location_name}</td><td>{item.sku_code} · {item.material_name} / {item.lot_no || "无批次"}</td><td>{item.allocated_qty} / {item.reserved_qty}</td><td>{item.reservable_qty} {item.base_unit}</td><td><Button disabled={disabled} onClick={() => choose(item)}>选择并预留</Button></td></tr>)}
      </tbody></table></div> : <p>当前没有满足条件的已分配可预留库存。</p>}
    </Modal>}
    {selection && <Modal title="确认库存预留" onClose={() => { if (!running && !blocked) setSelection(null); }}>
      <div className="form-stack"><p>{selection.before.request_no} · {selection.option.allocation_no} · {selection.option.owner_org_name} / {selection.option.location_name}</p>
        <p>当前可预留 {selection.option.reservable_qty} {selection.option.base_unit}</p>
        {error && <div className="alert alert-error">{error}</div>}
        <Field label="预留数量"><input aria-label="预留数量" inputMode="decimal" disabled={disabled} value={selection.quantity} onChange={(event) => setSelection({ ...selection, quantity: event.target.value })} /></Field>
        {reservationUsesSerials(selection.option) && <fieldset disabled={disabled}><legend>选择预留 SN（须与数量一致）</legend>{selection.option.serial_options.map((serial) => <label key={serial.serial_id}>
          <input type="checkbox" aria-label={`预留 SN ${serial.serial_no}`} checked={selection.serialIds.includes(serial.serial_id)} onChange={(event) => setSelection({ ...selection, serialIds: event.target.checked ? [...selection.serialIds, serial.serial_id] : selection.serialIds.filter((id) => id !== serial.serial_id) })} />{serial.serial_no} · {serial.qr_code}
        </label>)}</fieldset>}
        <div className="alert alert-warning">本次只确认库存占用；出库、发运、签收、收货和个人仓入库继续分别处理。</div>
        <div className="form-actions"><Button tone="secondary" disabled={disabled} onClick={() => setSelection(null)}>返回检查</Button><Button disabled={disabled} onClick={() => void submit()}>{running ? "正在精确回读" : "确认预留"}</Button></div>
      </div>
    </Modal>}
  </section>;
}
