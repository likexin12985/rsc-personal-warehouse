import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail } from "./formalMaterialRequests";
import { type InboundOrderResult, type ReceiptResult, type ShipmentResult, validateInboundOrderInput, validateInboundOrderResult, validateInboundPostingResult, validateReceiptResult, validateShipmentResult } from "./materialRequestShipment";
import { createInboundStore, hasInboundRecovery, recoverInbound, type InboundStore } from "./materialRequestInboundRecovery";
import { receiptStatusLabel, inboundStatusLabel } from "./materialRequestFulfillmentLabels";
import { Button, Field, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null; access?: FormalMaterialRequestAccess | null;
  store?: InboundStore; onDetail?: (detail: MaterialRequestDetail) => void; onBlocking?: (blocked: boolean) => void;
  otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };

function checkedFacts(orders: readonly InboundOrderResult[], receipts: readonly ReceiptResult[], shipments: readonly ShipmentResult[], requestId: string) {
  const shipped = shipments.map(validateShipmentResult), accepted = receipts.map(validateReceiptResult), inbound = orders.map(validateInboundOrderResult);
  if (shipped.some(row => row.request_id !== requestId) || new Set(shipped.map(row => row.shipment_id)).size !== shipped.length
      || new Set(accepted.map(row => row.receipt_id)).size !== accepted.length || new Set(inbound.map(row => row.inbound_order_id)).size !== inbound.length
      || new Set(inbound.map(row => row.receipt_id)).size !== inbound.length) throw new Error("入账来源记录不一致，请重新读取");
  for (const row of accepted) if (!shipped.some(item => item.shipment_id === row.shipment_id)) throw new Error("收货记录不属于当前发运");
  for (const row of inbound) {
    const receipt = accepted.find(item => item.receipt_id === row.receipt_id);
    const shipment = shipped.find(item => item.shipment_id === receipt?.shipment_id);
    if (!shipment || row.target_person_id !== shipment.target_person_id || row.target_location_id !== shipment.target_location_id)
      throw new Error("入账目标与原发运绑定不一致");
    if (!["pending", "posted"].includes(row.status) || (row.status === "posted") !== (row.posting_transaction_id !== null))
      throw new Error("入账状态与库存事实不一致");
  }
  return { inbound, accepted, shipped };
}

export default function FormalMaterialRequestInboundPanel(props: Props) {
  if (!props.detail) return null;
  return <InboundPanel key={`${props.detail.request_id}:${props.detail.request_version}:${JSON.stringify(props.access)}`} {...props} detail={props.detail} />;
}

function InboundPanel({ adapter, detail, access, store: providedStore, onDetail, onBlocking, otherWriteBusy = false, otherWriteBlocked }: Props & { detail: MaterialRequestDetail }) {
  const [store] = useState(() => providedStore ?? createInboundStore());
  const [receipt, setReceipt] = useState(""), [order, setOrder] = useState<InboundOrderResult | null>(null);
  const [history, setHistory] = useState<InboundOrderResult[]>([]), [receipts, setReceipts] = useState<ReceiptResult[]>([]), [shipments, setShipments] = useState<ShipmentResult[]>([]);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [blocked, setBlocked] = useState(() => store.read().kind !== "missing");
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const generation = useRef(0), running = useRef(false);
  const external = useRef({ otherWriteBusy, otherWriteBlocked }); external.current = { otherWriteBusy, otherWriteBlocked };
  const anotherWritePending = () => external.current.otherWriteBusy || !!external.current.otherWriteBlocked?.();
  const reportBlocking = () => onBlocking?.(running.current || store.read().kind !== "missing");
  const capable = hasInboundRecovery(adapter) && !!adapter.detailNoReplay && !!adapter.listReceipts && !!adapter.listShipments;
  const shipment = shipments.find(row => row.shipment_id === receipts.find(item => item.receipt_id === receipt)?.shipment_id);
  const location = shipment?.target_location_id ?? "", person = shipment?.target_person_id ?? "";

  async function loadFacts() {
    if (!adapter.listInboundOrders || !adapter.listReceipts || !adapter.listShipments) throw new Error("入账查询暂不可用");
    const [orders, accepted, shipped] = await Promise.all([adapter.listInboundOrders(detail.request_id), adapter.listReceipts(detail.request_id), adapter.listShipments(detail.request_id)]);
    return checkedFacts(orders, accepted, shipped, detail.request_id);
  }

  async function recover(turn: number) {
    const pending = store.read();
    if (pending.kind !== "valid" || pending.value.request_id !== detail.request_id || !hasInboundRecovery(adapter) || !adapter.detailNoReplay)
      throw new Error("原入账记录需要核验，请保留记录并在对应需求中处理");
    const { order: found, detail: currentDetail } = await recoverInbound(adapter, store, pending.value, () => turn === generation.current);
    if (turn !== generation.current) return;
    setOrder(found); setReceipt(found.receipt_id); setHistory(previous => [found, ...previous.filter(row => row.inbound_order_id !== found.inbound_order_id)]);
    setBlocked(false);
    setMessage(found.status === "posted" ? "已通过只读回读确认个人仓入账" : "已通过只读回读确认待入账单，请单独确认库存入账");
    onDetail?.(currentDetail);
  }

  useEffect(() => {
    const turn = ++generation.current; reportBlocking();
    setLoading(true); setHistory([]); setReceipts([]); setShipments([]); setOrder(null); setReceipt(""); setError("");
    void (async () => {
      try {
        const facts = await loadFacts(); if (turn !== generation.current) return;
        setHistory(facts.inbound); setReceipts(facts.accepted); setShipments(facts.shipped);
        if (store.read().kind !== "missing") {
          running.current = true; setBlocked(true); reportBlocking();
          await recover(turn);
        }
      } catch (e) { if (turn === generation.current) setError(showError(e)); }
      finally { if (turn === generation.current) { running.current = false; setLoading(false); reportBlocking(); } }
    })();
    return () => { generation.current += 1; running.current = false; onBlocking?.(store.read().kind !== "missing"); };
    // Request, version and displayed access are bound by the component key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);

  function selectOrder(value: InboundOrderResult) {
    if (running.current || blocked || store.read().kind !== "missing" || anotherWritePending()) return;
    setOrder(value); setReceipt(value.receipt_id); setError("");
  }
  async function verify() {
    if (running.current) return;
    const turn = generation.current; running.current = true; reportBlocking(); setBusy(true); setError("");
    try { await recover(turn); }
    catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); reportBlocking(); } }
  }
  async function submit(kind: "inbound-create" | "inbound-post") {
    if (running.current || loading || blocked || anotherWritePending() || store.read().kind !== "missing" || !capable || !access?.can_read
        || !receipt || !location || !person || (kind === "inbound-create" ? !adapter.createInboundOrder : !adapter.postInboundOrder || !order || order.status !== "pending")) return;
    const turn = generation.current; running.current = true; reportBlocking(); setBusy(true); setError("");
    try {
      const input = validateInboundOrderInput({ expected_request_version: detail.request_version, receipt_id: receipt, target_location_id: location, target_person_id: person });
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
      if (turn !== generation.current) return;
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) throw new Error("登录身份或权限已变化，请刷新后重新选择");
      const facts = await loadFacts(); if (turn !== generation.current) return;
      const accepted = facts.accepted.find(row => row.receipt_id === receipt);
      const target = facts.shipped.find(row => row.shipment_id === accepted?.shipment_id);
      if (!accepted || !["accepted", "exception"].includes(accepted.status) || target?.target_location_id !== location || target?.target_person_id !== person)
        throw new Error("收货或入账目标已变化，请重新读取");
      const existing = facts.inbound.find(row => row.receipt_id === receipt);
      if (kind === "inbound-create" && existing) throw new Error("此收货已有入账单，请从历史继续处理");
      if (kind === "inbound-post" && (!existing || existing.inbound_order_id !== order!.inbound_order_id || existing.status !== "pending"))
        throw new Error("原入账单状态已变化，请重新读取");
      if (anotherWritePending()) throw new Error("其他操作尚未完成，请核验后再入账");
      const headers = new Headers(mutationHeaders("material-request-" + kind).headers), trace = headers.get("X-Request-ID")!, key = headers.get("Idempotency-Key")!;
      store.persist({ v: 1, kind, trace, key: kind === "inbound-create" ? null : key, person_id: identity.person_id,
        authorization_version: identity.authorization_version, request_id: detail.request_id, expected_version: detail.request_version,
        receipt_id: receipt, target_location_id: location, target_person_id: person, ...(kind === "inbound-post" ? { inbound_order_id: order!.inbound_order_id } : {}) });
      setBlocked(true);
      if (kind === "inbound-create") validateInboundOrderResult(await adapter.createInboundOrder!(detail.request_id, input, { "X-Request-ID": trace }));
      else validateInboundPostingResult(await adapter.postInboundOrder!(detail.request_id, order!.inbound_order_id, { "X-Request-ID": trace, "Idempotency-Key": key }));
      if (turn !== generation.current) return;
      await recover(turn);
    } catch (e) { if (turn === generation.current) { setBlocked(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); reportBlocking(); } }
  }
  if (!adapter.createInboundOrder && !adapter.postInboundOrder) return null;
  const disabled = busy || loading || blocked || otherWriteBusy || !capable || !access?.can_read;
  return <section className="opening-detail-section" aria-label="个人仓入账">
    <header><div><h3>个人仓入账</h3><p>收货验收与库存入账分开确认；目标位置和人员来自发运记录。</p></div></header>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {message && <div className="alert alert-info" role="status">{message}</div>}
    {blocked && <div className="alert alert-warning">入账结果待核验，已禁止再次提交。<Button disabled={busy || loading} onClick={() => void verify()}>只读核验原入账</Button></div>}
    {!capable && <p>入账查询或结果核验通道暂不可用，已停止提交。</p>}
    <fieldset className="form-grid" disabled={disabled}>
      <Field label="收货单"><select aria-label="收货单" value={receipt} onChange={e => { setReceipt(e.target.value); setOrder(null); }}>
        <option value="">请选择已验收收货单</option>{receipts.map(row => <option key={row.receipt_id} value={row.receipt_id} disabled={!["accepted", "exception"].includes(row.status)}>{row.receipt_no} / {receiptStatusLabel(row.status)}</option>)}
      </select></Field>
      <Field label="目标位置 ID"><input aria-label="目标位置 ID" value={location} readOnly /></Field>
      <Field label="目标人员 ID"><input aria-label="目标人员 ID" value={person} readOnly /></Field>
    </fieldset>
    <Button disabled={disabled || !receipt || !location || !person || history.some(row => row.receipt_id === receipt)} onClick={() => void submit("inbound-create")}>创建待入账单</Button>
    {order?.status === "pending" && <Button disabled={disabled} onClick={() => void submit("inbound-post")}>确认个人仓入账</Button>}
    {history.length > 0 && <div className="table-wrap"><h4>个人仓入账历史</h4><table><thead><tr><th>入账单</th><th>收货单</th><th>状态</th><th>库存事务</th><th>操作</th></tr></thead><tbody>
      {history.map(row => <tr key={row.inbound_order_id}><td>{row.inbound_no}</td><td>{receipts.find(item => item.receipt_id === row.receipt_id)?.receipt_no ?? row.receipt_id}</td><td>{inboundStatusLabel(row.status)}</td><td className="mono">{row.posting_transaction_id ?? "—"}</td><td>{row.status === "pending" && <Button disabled={disabled} onClick={() => selectOrder(row)}>继续处理</Button>}</td></tr>)}
    </tbody></table></div>}
  </section>;
}
