import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAccess, type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { type MaterialRequestDetail, validateMaterialRequestDetail } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type ShipmentOption, type ShipmentInput, type ShipmentResult, type ShipmentOptions, type LogisticsEventResult, shipmentLineSelection, validateShipmentInput, validateShipmentOptions, validateShipmentResult } from "./materialRequestShipment";
import { type ShipmentStore, type ShipmentSentinel, hasShipmentRecovery, recoverShipment, matchShipmentResult, shipmentRequestHash } from "./materialRequestShipmentRecovery";
import { createLogisticsStore, hasLogisticsRecovery, recoverLogistics, type LogisticsStore } from "./materialRequestLogisticsRecovery";
import { Button, Field, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail | null;
  store: ShipmentStore; logisticsStore?: LogisticsStore; otherWriteBusy: boolean; otherWriteBlocked: () => boolean; onBlocking: (blocked: boolean) => void; onDetail: (detail: MaterialRequestDetail) => void };
type Draft = { quantity: string; serialIds: string[] };
const labels = { pickup: "揽收", transit: "运输中", signed: "物流签收", exception: "物流异常" };
function boundPage(raw: unknown, detail: MaterialRequestDetail) {
  const page = validateShipmentOptions(raw);
  if (page.request_id !== detail.request_id || page.request_version !== detail.request_version) throw new Error("发运候选与需求版本不一致，请刷新详情");
  return page;
}
export default function FormalMaterialRequestShipmentPanel({ adapter, access, detail, store, logisticsStore: providedLogisticsStore, otherWriteBusy, otherWriteBlocked, onBlocking, onDetail }: Props) {
  const [logisticsStore] = useState<LogisticsStore>(() => providedLogisticsStore ?? createLogisticsStore());
  const [page, setPage] = useState<ShipmentOptions | null>(null), [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [shipments, setShipments] = useState<ShipmentResult[]>([]), [events, setEvents] = useState<Record<string, LogisticsEventResult[]>>({});
  const [eventType, setEventType] = useState<keyof typeof labels>("signed"), [eventSource, setEventSource] = useState("carrier");
  const [evidenceFile, setEvidenceFile] = useState(""), [externalRef, setExternalRef] = useState("");
  const [carrier, setCarrier] = useState(""), [tracking, setTracking] = useState(""), [target, setTarget] = useState(""), [person, setPerson] = useState("");
  const [running, setRunning] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
  const [, redraw] = useState(0);
  const active = useRef(false), generation = useRef(0), context = useRef({ adapter, access, store, requestId: detail?.request_id, version: detail?.request_version });
  if (context.current.adapter !== adapter || context.current.store !== store || !same(context.current.access, access)
      || context.current.requestId !== detail?.request_id || context.current.version !== detail?.request_version) {
    generation.current += 1;
    context.current = { adapter, access, store, requestId: detail?.request_id, version: detail?.request_version };
  }
  const saved = store.read(), blocked = saved.kind !== "missing", logisticsSaved = logisticsStore.read(), logisticsBlocked = logisticsSaved.kind !== "missing";
  const capable = hasShipmentRecovery(adapter) && !!adapter.createShipment && !!adapter.listShipmentOptions && !!adapter.listShipments;
  const otherBlocked = () => otherWriteBusy || otherWriteBlocked();
  const disabled = running || blocked || otherWriteBusy || !capable;
  async function load(current: MaterialRequestDetail, turn: number) {
    if (!adapter.listShipmentOptions || !adapter.listShipments) return;
    setLoading(true);
    try {
      const [raw, history] = await Promise.all([adapter.listShipmentOptions(current.request_id), adapter.listShipments(current.request_id)]);
      const next = boundPage(raw, current), checked = history.map(validateShipmentResult);
      if (checked.some(row => row.request_id !== current.request_id) || new Set(checked.map(row => row.shipment_id)).size !== checked.length) throw new Error("发运历史与当前需求不一致");
      if (turn !== generation.current) return;
      setPage(next); setShipments(checked); setDrafts({});
      if (adapter.listLogisticsEvents) {
        const rows = await Promise.all(checked.map(async row => {
          const es = await adapter.listLogisticsEvents!(current.request_id, row.shipment_id);
          if (es.some(e => e.shipment_id !== row.shipment_id)) throw new Error("物流历史与包裹不一致");
          return [row.shipment_id, [...es]] as const;
        }));
        if (turn === generation.current) setEvents(Object.fromEntries(rows.map(([id, es]) => [id, [...es]])));
      }
    } catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) setLoading(false); }
  }
  const accessSignature = JSON.stringify(access);
  useEffect(() => {
    setPage(null); setDrafts({}); setShipments([]); setEvents({}); setError(""); setMessage("");
    setCarrier(""); setTracking(""); setTarget(""); setPerson(""); setRunning(false); setLoading(false);
    onBlocking(store.read().kind !== "missing");
    if (detail && access?.can_read_allocation_options) void load(detail, generation.current);
    return () => { generation.current += 1; };
    // All writes preserve the original context; changed props invalidate late responses.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store, accessSignature, detail?.request_id, detail?.request_version]);
  useEffect(() => {
    let live = true;
    const pending = logisticsStore.read();
    if (!detail || pending.kind !== "valid" || pending.value.request_id !== detail.request_id.toLowerCase() || !hasLogisticsRecovery(adapter)) return () => { live = false; };
    recoverLogistics(adapter, logisticsStore, pending.value).then(({ event }) => {
      if (live) { setEvents(prev => ({ ...prev, [event.shipment_id]: [...(prev[event.shipment_id] || []).filter(row => row.event_id !== event.event_id), event] })); setMessage(`已通过只读回读确认物流事件：${labels[event.event_type as keyof typeof labels] ?? event.event_type}`); redraw(v => v + 1); }
    }).catch(e => { if (live) setError("物流事件结果待核验，已禁止再次提交。" + showError(e)); });
    return () => { live = false; };
  }, [adapter, detail?.request_id, logisticsStore]);
  useEffect(() => {
    const changed = () => { redraw(v => v + 1); onBlocking(active.current || store.read().kind !== "missing"); };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [store, onBlocking]);
  async function recover() {
    const current = store.read();
    if (active.current || current.kind !== "valid" || !hasShipmentRecovery(adapter)) return;
    const turn = generation.current; active.current = true; setRunning(true); onBlocking(true); setError("");
    try {
      const outcome = await recoverShipment(adapter, store, current.value, () => turn === generation.current);
      if (turn !== generation.current) return;
      setPage(null); setDrafts({}); setTracking(""); onDetail(outcome.detail);
      setMessage(`已核验发运 ${outcome.command.shipment_no}，运单 ${outcome.command.tracking_no}。收货和个人仓入账分别处理。`);
      void load(outcome.detail, turn);
    } catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { active.current = false; if (turn === generation.current) { setRunning(false); onBlocking(store.read().kind !== "missing"); } }
  }
  async function submit(option: ShipmentOption) {
    if (!detail || !access || !page || !capable || active.current || store.read().kind !== "missing" || otherBlocked()
        || !hasShipmentRecovery(adapter) || !adapter.createShipment || !adapter.listShipmentOptions) return;
    const turn = generation.current, before = detail;
    active.current = true; setRunning(true); onBlocking(true); setError(""); setMessage("");
    let persisted = false;
    try {
      const draft = drafts[option.posting_id] ?? { quantity: option.shippable_qty, serialIds: [] };
      const line = shipmentLineSelection(option, draft.quantity, draft.serialIds);
      const input: ShipmentInput = validateShipmentInput({ expected_request_version: before.request_version, target_location_id: target.trim().toLowerCase(), target_person_id: person.trim().toLowerCase() || null,
        carrier: carrier.trim(), tracking_no: tracking.trim(), shipped_at: new Date().toISOString(), lines: [line] });
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      const freshAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version || !same(access, freshAccess)
          || !freshAccess.can_read || !freshAccess.can_read_allocation_options) throw new Error("登录身份或发运授权已变化，请重新进入页面");
      const fresh = validateMaterialRequestDetail(await adapter.detailNoReplay(before.request_id), before.request_id);
      const next = boundPage(await adapter.listShipmentOptions(before.request_id), fresh);
      if (fresh.request_version !== before.request_version || !same(next.items.find(row => row.posting_id === option.posting_id), option)) throw new Error("可发运余量或 SN 已变化，请重新选择");
      await shipmentRequestHash(before.request_id, input); // Verify local hashing support before the only POST.
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他操作正在提交或等待核验，发运已暂停");
      const headers = new Headers(mutationHeaders("material-request-shipment").headers);
      const trace = headers.get("X-Request-ID") ?? "", key = headers.get("Idempotency-Key") ?? "";
      const sentinel: ShipmentSentinel = { v: 1, kind: "shipment", trace, key, person_id: access.person_id, authorization_version: access.authorization_version, request_id: before.request_id, input };
      store.persist(sentinel); persisted = true;
      const result = validateShipmentResult(await adapter.createShipment(before.request_id, input, { "X-Request-ID": trace, "Idempotency-Key": key }));
      matchShipmentResult(sentinel, result);
      if (turn !== generation.current) return;
      const outcome = await recoverShipment(adapter, store, sentinel, () => turn === generation.current, result);
      if (turn !== generation.current) return;
      setPage(null); setDrafts({}); setTracking(""); onDetail(outcome.detail);
      setMessage(`本包 ${result.shipment_no} 已登记并核验，数量 ${line.shipped_qty}。可继续处理剩余数量。`);
      void load(outcome.detail, turn);
    } catch (e) { if (turn === generation.current) setError(`${showError(e)}${persisted ? "。核验完成前请勿再次提交。" : ""}`); }
    finally { active.current = false; if (turn === generation.current) { setRunning(false); onBlocking(store.read().kind !== "missing"); } }
  }
  async function registerEvent(shipmentId: string) {
    if (!detail || !adapter.createLogisticsEvent || !hasLogisticsRecovery(adapter) || active.current || blocked || logisticsBlocked || otherBlocked()) return;
    const turn = generation.current; active.current = true; setRunning(true); onBlocking(true); setError("");
    try {
      const h = new Headers(mutationHeaders("material-request-logistics-event").headers);
      const input = { event_type: eventType, event_at: new Date().toISOString(), source: eventSource, evidence_file_id: evidenceFile || null, external_ref: externalRef || null } as const;
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      const freshAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
      if (identity.person_id !== access?.person_id || identity.authorization_version !== access?.authorization_version || !same(freshAccess, access)) throw new Error("登录身份或物流事件授权已变化，请重新进入页面");
      const trace = h.get("X-Request-ID")!, key = h.get("Idempotency-Key")!;
      logisticsStore.persist({ v: 1, kind: "logistics-event", trace, key, person_id: identity.person_id, authorization_version: identity.authorization_version, request_id: detail.request_id, shipment_id: shipmentId, input });
      const x = await adapter.createLogisticsEvent(detail.request_id, shipmentId, input, { "X-Request-ID": trace, "Idempotency-Key": key });
      if (x.shipment_id !== shipmentId) throw new Error("物流事件包裹绑定不一致");
      logisticsStore.clear(trace);
      if (turn === generation.current) { setEvents(prev => ({ ...prev, [shipmentId]: [...(prev[shipmentId] || []), x] })); setMessage(`已登记物流事件：${labels[eventType]}`); }
    } catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { active.current = false; if (turn === generation.current) { setRunning(false); onBlocking(store.read().kind !== "missing"); } }
  }
  return <section className="opening-detail-section" aria-label="发运与分包">
    <header><div><h3>发运与分包</h3><p>按实际包裹填写数量、SN 和运单；未发完的出库余量可继续分包。登记发运不再次扣库存。</p></div></header>
    {error && <div className="alert alert-error">{error}</div>}{message && <div className="alert alert-info" role="status">{message}</div>}
    {blocked && <div className="alert alert-warning">发运结果待核验，暂停其他写入；刷新页面后仍保留原请求。<Button disabled={running || saved.kind !== "valid" || !hasShipmentRecovery(adapter)} onClick={() => void recover()}>只读核验原发运</Button>{saved.kind !== "valid" && <p>核验存储不可用，不能提交新的发运。</p>}</div>}
    {detail && access?.can_read_allocation_options && <>
      <fieldset disabled={disabled} className="form-grid">
        <Field label="目标位置 ID"><input aria-label="目标位置 ID" value={target} onChange={e => setTarget(e.target.value)} /></Field>
        <Field label="目标人员 ID（可选）"><input aria-label="目标人员 ID（可选）" value={person} onChange={e => setPerson(e.target.value)} /></Field>
        <Field label="承运商"><input aria-label="承运商" value={carrier} onChange={e => setCarrier(e.target.value)} /></Field>
        <Field label="本包运单号"><input aria-label="本包运单号" value={tracking} onChange={e => setTracking(e.target.value)} /></Field>
      </fieldset>
      <Button disabled={disabled || loading} onClick={() => { setPage(null); void load(detail, generation.current); }}>刷新可发运数量</Button>
      {loading && <p>正在读取发运记录…</p>}
      {page && (page.items.length === 0 ? <p>当前没有可登记发运的出库余量。</p> : <div className="table-wrap"><table><thead><tr><th>原出库 / 拣货</th><th>已发 / 可发</th><th>本包数量与 SN</th><th>操作</th></tr></thead><tbody>
        {page.items.map(option => { const draft = drafts[option.posting_id] ?? { quantity: option.shippable_qty, serialIds: [] }; return <tr key={option.posting_id}>
          <td>{option.posting_no}<br />拣货 {option.pick_id}</td><td>{option.shipped_qty} / {option.shippable_qty}</td>
          <td><input aria-label={`本包数量 ${option.posting_no}`} inputMode="decimal" disabled={disabled} value={draft.quantity} onChange={e => setDrafts(prev => ({ ...prev, [option.posting_id]: { ...draft, quantity: e.target.value } }))} />
            {option.serials.length > 0 && <fieldset disabled={disabled}><legend>选择本包 SN（与数量一致）</legend>{option.serials.map(s => <label key={s.serial_id} className="reservation-serial-option"><input type="checkbox" aria-label={`本包 SN ${s.serial_no}`} checked={draft.serialIds.includes(s.serial_id)} onChange={e => setDrafts(prev => ({ ...prev, [option.posting_id]: { ...draft, serialIds: e.target.checked ? [...draft.serialIds, s.serial_id] : draft.serialIds.filter(id => id !== s.serial_id) } }))} />{s.serial_no}</label>)}</fieldset>}</td>
          <td><Button disabled={disabled || !target || !carrier || !tracking} onClick={() => void submit(option)}>登记本包发运</Button></td>
        </tr>; })}
      </tbody></table></div>)}
      {shipments.length > 0 && <div className="table-wrap"><h4>已登记包裹</h4><table><thead><tr><th>发运单</th><th>承运商 / 运单</th><th>数量</th><th>交运时间</th></tr></thead><tbody>{shipments.map(s => <tr key={s.shipment_id}><td>{s.shipment_no}</td><td>{s.carrier} / {s.tracking_no}</td><td>{s.lines.map(l => l.shipped_qty).join(" / ")}</td><td>{s.shipped_at}</td></tr>)}</tbody></table>
        <h4>物流事件历史</h4><fieldset className="form-grid" disabled={disabled || logisticsBlocked}><Field label="事件类型"><select aria-label="事件类型" value={eventType} onChange={e => setEventType(e.target.value as keyof typeof labels)}>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field><Field label="事件来源"><input aria-label="事件来源" value={eventSource} onChange={e => setEventSource(e.target.value)} /></Field><Field label="证据文件 ID（可选）"><input aria-label="证据文件 ID（可选）" value={evidenceFile} onChange={e => setEvidenceFile(e.target.value)} /></Field><Field label="外部引用（可选）"><input aria-label="外部引用（可选）" value={externalRef} onChange={e => setExternalRef(e.target.value)} /></Field></fieldset>
        <table><thead><tr><th>发运单</th><th>事件</th><th>时间</th><th>来源</th></tr></thead><tbody>{shipments.flatMap(s => (events[s.shipment_id] ?? []).map(e => <tr key={e.event_id}><td>{s.shipment_no}</td><td>{labels[e.event_type as keyof typeof labels] ?? e.event_type}</td><td>{e.event_at}</td><td>{e.source}</td></tr>))}</tbody></table>
        {logisticsBlocked && <div className="alert alert-warning">物流事件结果待核验，已禁止再次提交；刷新后只读核验原事件。</div>}{shipments.map(s => adapter.createLogisticsEvent && <Button key={s.shipment_id} disabled={disabled || logisticsBlocked || !eventSource || !hasLogisticsRecovery(adapter)} onClick={() => void registerEvent(s.shipment_id)}>登记 {s.shipment_no} 物流事件</Button>)}
      </div>}
    </>}
  </section>;
}
