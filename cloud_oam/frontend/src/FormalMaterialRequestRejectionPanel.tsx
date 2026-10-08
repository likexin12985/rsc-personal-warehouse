import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type RejectionPage, type RejectionCommand, type ProgressAction, rejectionPage, rejectionCommand, rejectionFingerprint } from "./materialRequestRejection";
import { createRejectionStore, hasRejectionRecovery, recoverRejection, type RejectionStore } from "./materialRequestRejectionRecovery";
import { canonical, demand, units, quantity } from "./myFulfillmentContract";
import { Button, Field, showError } from "./ui";
type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null; access: FormalMaterialRequestAccess | null; store?: RejectionStore;
  onBlocking?: (value: boolean) => void; onDetail?: (detail: MaterialRequestDetail) => void; otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
const states = { registered: "已登记，待发出", cancelled: "登记已撤销", departed: "实物已发出，待承运交接", handed_over: "已交承运" };
const labels = { register: "登记拒收退回", cancel_registration: "撤销退回登记", depart: "确认实物发出", handover: "确认承运交接" };
export default function FormalMaterialRequestRejectionPanel(props: Props) {
  if (!props.detail || !props.access || !props.adapter.rejectionCandidates || props.detail.requester_person_id !== props.access.person_id || props.detail.request_version < 1) return null;
  return <Panel key={`${props.detail.request_id}:${props.detail.request_version}:${canonical(props.access)}`} {...props} detail={props.detail} access={props.access} />;
}
function Panel(props: Props & { detail: MaterialRequestDetail; access: FormalMaterialRequestAccess }) {
  const { adapter, detail, access } = props;
  const [store] = useState(() => props.store ?? createRejectionStore());
  const [page, setPage] = useState<RejectionPage | null>(null), [after, setAfter] = useState<string | null>(null);
  const [selected, setSelected] = useState(""), [action, setAction] = useState<"register" | ProgressAction>("register");
  const [reason, setReason] = useState(""), [qty, setQty] = useState(""), [serials, setSerials] = useState<string[]>([]);
  const [physical, setPhysical] = useState(""), [carrier, setCarrier] = useState(""), [tracking, setTracking] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [pending, setPending] = useState(() => store.read().kind !== "missing");
  const generation = useRef(0), running = useRef(false), current = useRef(props); current.current = props;
  const blocked = loading || busy || pending || !page;
  const otherBlocked = () => !!current.current.otherWriteBusy || !!current.current.otherWriteBlocked?.();
  useLayoutEffect(() => { current.current.onBlocking?.(blocked); }, [blocked]);
  const selectable = page?.items.flatMap(s => s.lines.map(l => ({ source: s, line: l, original: s.detail!.lines.find(d => d.receipt_line_id === l.receipt_line_id)! }))) ?? [];
  const picked = selectable.find(s => s.line.receipt_line_id === selected);
  const histories = selectable.flatMap(s => s.line.registrations.map(h => ({ ...s, history: h })));
  function resetSelection() { setSelected(""); setQty(""); setSerials([]); setReason(""); setPhysical(""); setCarrier(""); setTracking(""); }
  async function load(turn: number, cursor: string | null) {
    const value = rejectionPage(await adapter.rejectionCandidates!(detail.request_id, cursor), detail.request_id, cursor);
    demand(value.request_version === detail.request_version, "需求版本已变化，请重新打开详情");
    if (turn === generation.current) { setPage(value); setAfter(cursor); resetSelection(); }
  }
  useEffect(() => {
    const turn = ++generation.current;
    void load(turn, null).catch(e => { if (turn === generation.current) setError(showError(e)); })
      .finally(() => { if (turn === generation.current) setLoading(false); });
    return () => { generation.current += 1; current.current.onBlocking?.(store.read().kind !== "missing"); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);
  async function recover(turn: number) {
    const saved = store.read();
    demand(saved.kind === "valid" && saved.value.request_id === detail.request_id && hasRejectionRecovery(adapter), "请打开原需求只读核验退回，保留浏览器记录");
    const result = await recoverRejection(adapter, store, saved.value, () => turn === generation.current);
    if (turn === generation.current) { setPending(false); setPage(result.page); setAfter(null); resetSelection(); current.current.onDetail?.(result.detail); }
  }
  async function refresh(cursor: string | null = after) {
    if (running.current) return;
    running.current = true; const turn = generation.current; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try { if (store.read().kind !== "missing") await recover(turn); else await load(turn, cursor); }
    catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  function commandFrom(sourcePage: RejectionPage): RejectionCommand {
    const common = { expected_request_version: detail.request_version, reason: reason.trim() };
    if (action === "register") {
      const source = sourcePage.items.find(s => s.lines.some(l => l.receipt_line_id === selected));
      const line = source?.lines.find(l => l.receipt_line_id === selected), original = source?.detail?.lines.find(l => l.receipt_line_id === selected);
      demand(source?.detail && line?.register_permitted && original, "该拒收来源已变化或当前无登记权限");
      const tracked = ["serial", "lot_and_serial"].includes(original.tracking_mode);
      const amount = tracked ? `${serials.length}.000` : quantity(qty);
      demand(units(amount) > 0n && units(amount) <= units(line.available_qty) && serials.every(sid => line.available_serials.some(s => s.serial_id === sid)), "退回数量或SN超过当前可登记范围");
      return rejectionCommand({ kind: "register", input: { ...common, receipt_id: source.receipt_id, receipt_line_id: selected,
        receipt_request_hash: source.detail.receipt_request_hash, quantity: amount, serial_ids: tracked ? serials : [] } });
    }
    const history = sourcePage.items.flatMap(s => s.lines.flatMap(l => l.registrations)).find(h => h.registration.return_id === selected);
    demand(history && history.permitted_actions.includes(action), "当前退回状态或动作权限已变化");
    const previous = action === "handover" ? history.progress.events[0] : null;
    return rejectionCommand({ kind: "progress", return_id: history.registration.return_id, input: { ...common, action,
      registration_request_hash: history.registration.request_hash, previous_event_id: previous?.event_id ?? null, previous_request_hash: previous?.request_hash ?? null,
      physical_at: action === "cancel_registration" ? null : new Date(physical).toISOString(),
      carrier: action === "handover" ? carrier.trim() : null, tracking_no: action === "handover" ? tracking.trim() : null } });
  }
  async function submit() {
    if (blocked || otherBlocked() || running.current || !adapter.recordRejection || !hasRejectionRecovery(adapter) || store.read().kind !== "missing") return;
    running.current = true; const turn = generation.current; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      const command = commandFrom(page!);
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      demand(identity.person_id === access.person_id && identity.authorization_version === access.authorization_version, "身份变化，请重新核对");
      const fresh = rejectionPage(await adapter.rejectionCandidates(detail.request_id, after), detail.request_id, after);
      demand(fresh.request_version === detail.request_version && canonical(commandFrom(fresh)) === canonical(command), "拒收来源或进展已变化");
      const fingerprint = await rejectionFingerprint(command);
      if (turn !== generation.current) return;
      demand(!otherBlocked(), "其他操作尚未核验完成");
      const headers = new Headers(mutationHeaders("material-request-rejection-return").headers);
      const key = headers.get("Idempotency-Key")!, trace = headers.get("X-Request-ID")!;
      store.persist({ v: 1, key, trace, person_id: identity.person_id, authorization_version: identity.authorization_version, request_id: detail.request_id, command, fingerprint }); setPending(true);
      await adapter.recordRejection(detail.request_id, command, { "Idempotency-Key": key, "X-Request-ID": trace });
      if (turn === generation.current) await recover(turn);
    } catch (e) { if (turn === generation.current) { setPending(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  return <section className="opening-detail-section" aria-label="拒收退回">
    <header><div><h3>拒收退回</h3><p>按原拒收记录登记退回，分别确认实物发出和承运交接。来源仓验收、入账另行办理。</p></div>
      <Button tone="secondary" disabled={busy || loading} onClick={() => void refresh()}>{pending ? "只读核验原退回" : "刷新拒收退回"}</Button></header>
    {loading && <p role="status">正在核验原拒收记录…</p>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pending && <p role="status">原退回请求已保留，核验完成前不要重复提交。</p>}
    {page && !page.items.length && <p>当前没有本人验收记录。</p>}
    {page?.items.map(s => <div key={s.receipt_id}>{s.status === "blocked" ? <p>{s.message}</p> : <>
      <h4>{s.detail?.receipt_no}</h4>
      {s.lines.length === 0 && <p>本次验收没有拒收物资。</p>}
      {s.lines.map(l => { const original = s.detail!.lines.find(d => d.receipt_line_id === l.receipt_line_id)!; return <div key={l.receipt_line_id}>
        <p>{original.sku_code} · {original.material_name}，拒收 {original.rejected_qty}，可登记 {l.available_qty} {original.base_unit}</p>
        {l.registrations.map(h => <p key={h.registration.return_id}>{h.registration.return_no} · {h.registration.quantity} · {states[h.progress.status]}{h.progress.events.at(-1)?.tracking_no && ` · ${h.progress.events.at(-1)?.carrier} ${h.progress.events.at(-1)?.tracking_no}`}</p>)}
      </div>; })}</>}</div>)}
    <Field label="退回操作"><select aria-label="退回操作" disabled={blocked || props.otherWriteBusy} value={action} onChange={e => { setAction(e.target.value as typeof action); resetSelection(); }}>
      {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label="选择拒收或退回记录"><select aria-label="选择拒收或退回记录" disabled={blocked || props.otherWriteBusy} value={selected} onChange={e => { setSelected(e.target.value); setQty(""); setSerials([]); }}>
      <option value="">请选择</option>
      {action === "register" ? selectable.filter(s => s.line.register_permitted).map(s => <option key={s.line.receipt_line_id} value={s.line.receipt_line_id}>{s.source.detail?.receipt_no} · {s.original.sku_code} · {s.original.material_name} · 可退 {s.line.available_qty}</option>)
        : histories.filter(s => s.history.permitted_actions.includes(action)).map(s => <option key={s.history.registration.return_id} value={s.history.registration.return_id}>{s.history.registration.return_no} · {s.original.material_name} · {s.history.registration.quantity}</option>)}
    </select></Field>
    {action === "register" && picked && (picked.line.available_serials.length ? <fieldset disabled={blocked || props.otherWriteBusy}><legend>选择本次退回 SN</legend>{picked.line.available_serials.map(s => <label key={s.serial_id}><input type="checkbox" checked={serials.includes(s.serial_id)} onChange={e => setSerials(old => e.target.checked ? [...old, s.serial_id] : old.filter(id => id !== s.serial_id))} />{s.serial_no}</label>)}</fieldset>
      : <Field label="退回数量"><input aria-label="退回数量" inputMode="decimal" disabled={blocked || props.otherWriteBusy} value={qty} onChange={e => setQty(e.target.value)} /></Field>)}
    {(action === "depart" || action === "handover") && <Field label="实际发生时间"><input aria-label="实际发生时间" type="datetime-local" disabled={blocked || props.otherWriteBusy} value={physical} onChange={e => setPhysical(e.target.value)} /></Field>}
    {action === "handover" && <><Field label="承运商"><input aria-label="承运商" disabled={blocked || props.otherWriteBusy} value={carrier} onChange={e => setCarrier(e.target.value)} /></Field><Field label="运单号"><input aria-label="运单号" disabled={blocked || props.otherWriteBusy} value={tracking} onChange={e => setTracking(e.target.value)} /></Field></>}
    <Field label="退回操作说明"><input aria-label="退回操作说明" maxLength={500} disabled={blocked || props.otherWriteBusy} value={reason} onChange={e => setReason(e.target.value)} /></Field>
    <Button disabled={blocked || props.otherWriteBusy || !selected || !reason.trim()} onClick={() => void submit()}>{labels[action]}</Button>
    {after && <Button tone="secondary" disabled={blocked} onClick={() => void refresh(null)}>回到首批记录</Button>}
    {page?.next_after_id && <Button tone="secondary" disabled={blocked} onClick={() => void refresh(page.next_after_id)}>下一批验收记录</Button>}
  </section>;
}
