import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type ReceiptResult, type ShipmentResult, validateReceiptInput, validateReceiptResult, validateShipmentResult, shipmentQuantity } from "./materialRequestShipment";
import { createReceiptStore, hasReceiptRecovery, recoverReceipt, type ReceiptStore } from "./materialRequestReceiptRecovery";
import { receiptAvailability, remainingReceiptQuantity, validateReceiptAvailability } from "./materialRequestReceiptAvailability";
import { receiptStatusLabel } from "./materialRequestFulfillmentLabels";
import { Button, Field, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null;
  access?: FormalMaterialRequestAccess | null; store?: ReceiptStore; onDetail?: (detail: MaterialRequestDetail) => void;
  onBlocking?: (blocked: boolean) => void; otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
const CONDITIONS = { normal: "正常", shortage: "短少", damaged: "破损", wrong_material: "错料", wrong_serial: "错 SN", rejected: "拒收" };

function checkedShipments(rows: readonly ShipmentResult[], requestId: string): ShipmentResult[] {
  const checked = rows.map(validateShipmentResult), ids = new Set<string>(), lines = new Set<string>();
  for (const row of checked) {
    if (row.request_id !== requestId || ids.has(row.shipment_id)) throw new Error("发运记录与当前需求不一致，请重新读取");
    ids.add(row.shipment_id);
    for (const line of row.lines) {
      if (lines.has(line.shipment_line_id)) throw new Error("发运明细重复，已停止收货");
      lines.add(line.shipment_line_id);
    }
  }
  return checked;
}

export default function FormalMaterialRequestReceiptPanel(props: Props) {
  const { detail, access } = props;
  if (!detail) return null;
  return <ReceiptPanel key={`${detail.request_id}:${detail.request_version}:${JSON.stringify(access)}`}
    {...props} detail={detail} />;
}

function ReceiptPanel({ adapter, detail, access, store: providedStore, onDetail, onBlocking, otherWriteBusy = false, otherWriteBlocked }: Props & { detail: MaterialRequestDetail }) {
  const [store] = useState(() => providedStore ?? createReceiptStore());
  const [line, setLine] = useState(""), [accepted, setAccepted] = useState(""), [rejected, setRejected] = useState("0.000");
  const [condition, setCondition] = useState("normal"), [serials, setSerials] = useState(""), [evidence, setEvidence] = useState("");
  const [history, setHistory] = useState<ReceiptResult[]>([]), [shipments, setShipments] = useState<ShipmentResult[]>([]);
  const [available, setAvailable] = useState<ReturnType<typeof receiptAvailability>>(new Map());
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [blocked, setBlocked] = useState(() => store.read().kind !== "missing");
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const generation = useRef(0), running = useRef(false);
  const externalBusy = useRef(otherWriteBusy); externalBusy.current = otherWriteBusy;
  const externalBlock = useRef(otherWriteBlocked); externalBlock.current = otherWriteBlocked;
  const anotherWritePending = () => externalBusy.current || !!externalBlock.current?.();
  const reportBlocking = () => onBlocking?.(running.current || store.read().kind !== "missing");
  const capable = !!adapter.createReceipt && !!adapter.listShipments && !!adapter.listReceipts && hasReceiptRecovery(adapter);
  const selected = shipments.find(row => row.lines.some(item => item.shipment_line_id === line));
  const receiver = selected?.target_person_id ?? "";

  async function recover(turn: number) {
    const pending = store.read();
    if (pending.kind !== "valid" || pending.value.request_id !== detail.request_id || !hasReceiptRecovery(adapter)) {
      throw new Error("原收货记录需要核验，请保留记录并在对应需求中处理");
    }
    const result = await recoverReceipt(adapter, store, pending.value, () => turn === generation.current);
    if (turn !== generation.current) return;
    setHistory(previous => [result.command, ...previous.filter(row => row.receipt_id !== result.command.receipt_id)]);
    setBlocked(false); setMessage(`已通过只读回读确认收货 ${result.command.receipt_no}`);
    onDetail?.(result.detail);
  }

  useEffect(() => {
    const turn = ++generation.current;
    reportBlocking();
    setLoading(true); setHistory([]); setShipments([]); setAvailable(new Map()); setLine(""); setError("");
    void (async () => {
      try {
        if (!adapter.listReceipts || !adapter.listShipments) throw new Error("收货查询暂不可用");
        const [receipts, shipped] = await Promise.all([adapter.listReceipts(detail.request_id), adapter.listShipments(detail.request_id)]);
        const checked = checkedShipments(shipped, detail.request_id);
        const receiptRows = receipts.map(validateReceiptResult);
        const availability = receiptAvailability(checked, receiptRows);
        if (turn !== generation.current) return;
        setHistory(receiptRows); setShipments(checked); setAvailable(availability);
        if (store.read().kind !== "missing") {
          setBlocked(true); running.current = true;
          try { await recover(turn); } finally { if (turn === generation.current) { running.current = false; reportBlocking(); } }
        }
      } catch (e) { if (turn === generation.current) setError(showError(e)); }
      finally { if (turn === generation.current) setLoading(false); }
    })();
    return () => { generation.current += 1; running.current = false; onBlocking?.(store.read().kind !== "missing"); };
    // Remounting binds request/version and the currently displayed identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);

  async function verify() {
    if (running.current) return;
    running.current = true; reportBlocking(); setBusy(true); setError(""); const turn = generation.current;
    try { await recover(turn); }
    catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); reportBlocking(); } }
  }

  async function submit() {
    if (anotherWritePending() || running.current || loading || blocked || store.read().kind !== "missing" || !capable || !selected || !receiver || !access?.can_read) return;
    running.current = true; reportBlocking(); setBusy(true); setError(""); const turn = generation.current;
    try {
      const input = validateReceiptInput({ expected_request_version: detail.request_version, receiver_person_id: receiver,
        received_at: new Date().toISOString(), lines: [{ shipment_line_id: line, accepted_qty: shipmentQuantity(accepted), rejected_qty: shipmentQuantity(rejected),
          condition, serial_ids: serials.split(",").map(value => value.trim()).filter(Boolean), exception_evidence_file_id: evidence || null }] });
      validateReceiptAvailability(input, available);
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
      if (turn !== generation.current) return;
      if (anotherWritePending()) throw new Error("其他操作尚未完成，请核验后再登记收货");
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) throw new Error("登录身份或权限已变化，请刷新后重新选择");
      const [freshShipments, freshReceipts] = await Promise.all([adapter.listShipments!(detail.request_id), adapter.listReceipts!(detail.request_id)]);
      const checked = checkedShipments(freshShipments, detail.request_id);
      const current = checked.find(row => row.lines.some(item => item.shipment_line_id === line));
      if (turn !== generation.current) return;
      if (!current || current.shipment_id !== selected.shipment_id || current.target_person_id !== receiver
          || current.target_location_id !== selected.target_location_id) throw new Error("包裹收货绑定已变化，请刷新后重新选择");
      if (anotherWritePending()) throw new Error("其他操作尚未完成，请核验后再登记收货");
      const freshAvailable = receiptAvailability(checked, freshReceipts.map(validateReceiptResult));
      setAvailable(freshAvailable);
      validateReceiptAvailability(input, freshAvailable);
      const headers = new Headers(mutationHeaders("material-request-receipt").headers);
      const trace = headers.get("X-Request-ID")!, key = headers.get("Idempotency-Key")!;
      store.persist({ v: 1, kind: "receipt", trace, key, person_id: identity.person_id,
        authorization_version: identity.authorization_version, request_id: detail.request_id, input });
      setBlocked(true);
      validateReceiptResult(await adapter.createReceipt!(detail.request_id, input, { "X-Request-ID": trace, "Idempotency-Key": key }));
      if (turn !== generation.current) return;
      await recover(turn);
    } catch (e) {
      if (turn === generation.current) { setBlocked(store.read().kind !== "missing"); setError(showError(e)); }
    } finally { if (turn === generation.current) { running.current = false; setBusy(false); reportBlocking(); } }
  }

  if (!adapter.createReceipt) return null;
  return <section className="opening-detail-section" aria-label="收货验收">
    <header><div><h3>收货验收</h3><p>选择包裹明细后自动带出发运指定的收货人；验收后仍需单独确认个人仓入账。</p></div></header>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {message && <div className="alert alert-info" role="status">{message}</div>}
    {!capable && <p>收货查询或结果核验通道暂不可用，已停止提交。</p>}
    {blocked && <div className="alert alert-warning">收货结果待核验，已禁止再次提交。
      <Button disabled={busy || loading} onClick={() => void verify()}>只读核验原收货</Button></div>}
    {loading && <p>正在读取发运与收货记录…</p>}
    <fieldset className="form-grid" disabled={otherWriteBusy || busy || loading || blocked || !capable}>
      <Field label="发运明细"><select aria-label="发运明细" value={line} onChange={e => { setLine(e.target.value); setAccepted(""); setRejected("0.000"); setSerials(""); setEvidence(""); setCondition("normal"); }}>
        <option value="">请选择已登记发运明细</option>
        {shipments.flatMap(shipment => shipment.lines.map((item, index) => <option key={item.shipment_line_id} value={item.shipment_line_id} disabled={!shipment.target_person_id || !available.get(item.shipment_line_id)?.remaining}>
          {shipment.shipment_no} / 明细 {index + 1} / 发运 {item.shipped_qty} / 可收 {remainingReceiptQuantity(available.get(item.shipment_line_id)?.remaining ?? 0n)}{!shipment.target_person_id ? "（未绑定收货人）" : ""}
        </option>))}</select></Field>
      <Field label="发运指定收货人"><input aria-label="发运指定收货人" value={receiver} readOnly placeholder="选择发运明细后自动带出" /></Field>
      <Field label="合格数量"><input aria-label="合格数量" inputMode="decimal" value={accepted} onChange={e => setAccepted(e.target.value)} /></Field>
      <Field label="拒收数量"><input aria-label="拒收数量" inputMode="decimal" value={rejected} onChange={e => setRejected(e.target.value)} /></Field>
      <Field label="验收条件"><select aria-label="验收条件" value={condition} onChange={e => setCondition(e.target.value)}>
        {Object.entries(CONDITIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></Field>
      <Field label="SN（逗号分隔，可选）"><input aria-label="SN（逗号分隔，可选）" value={serials} onChange={e => setSerials(e.target.value)} /></Field>
      <Field label="异常证据文件 ID（可选）"><input aria-label="异常证据文件 ID（可选）" value={evidence} onChange={e => setEvidence(e.target.value)} /></Field>
    </fieldset>
    <Button disabled={otherWriteBusy || busy || loading || blocked || !capable || !receiver || !line || !available.get(line)?.remaining || !accepted || !access?.can_read} onClick={() => void submit()}>登记收货验收</Button>
    {history.length > 0 && <div className="table-wrap"><h4>收货历史</h4><table><thead><tr><th>收货单</th><th>发运单</th><th>状态</th><th>异常数</th></tr></thead>
      <tbody>{history.map(row => <tr key={row.receipt_id}><td>{row.receipt_no}</td><td>{shipments.find(shipment => shipment.shipment_id === row.shipment_id)?.shipment_no ?? row.shipment_id}</td><td>{receiptStatusLabel(row.status)}</td><td>{row.exceptions.length}</td></tr>)}</tbody></table></div>}
  </section>;
}
