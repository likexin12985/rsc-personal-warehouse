import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { cancellationInputFromRemainder, remainingCancelFingerprint, validateRemainingCancellation, validateRemainingCancellationState, type RemainingCancellationState } from "./materialRequestRemainingCancellation";
import { validateVersionedMaterialRequestRemainder, type VersionedMaterialRequestRemainder } from "./materialRequestRemainder";
import { createRemainingCancellationStore, hasRemainingCancellationRecovery, recoverRemainingCancellation, type RemainingCancellationStore } from "./materialRequestRemainingCancellationRecovery";
import { Button, Field, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null; access: FormalMaterialRequestAccess | null;
  store?: RemainingCancellationStore; onBlocking?: (value: boolean) => void; onCancelled?: (value: boolean) => void;
  onDetail?: (value: MaterialRequestDetail) => void; otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
export default function FormalMaterialRequestRemainingCancellationPanel(props: Props) {
  if (!props.detail || !props.access || !props.adapter.remainingCancellationState) return null;
  return <Panel key={`${props.detail.request_id}:${props.detail.request_version}:${JSON.stringify(props.access)}`}
    {...props} detail={props.detail} access={props.access} />;
}
function Panel(props: Props & { detail: MaterialRequestDetail; access: FormalMaterialRequestAccess }) {
  const { adapter, detail, access } = props;
  const [store] = useState(() => props.store ?? createRemainingCancellationStore());
  const [state, setState] = useState<RemainingCancellationState | null>(null);
  const [assessment, setAssessment] = useState<VersionedMaterialRequestRemainder | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [reason, setReason] = useState("");
  const [pending, setPending] = useState(() => store.read().kind !== "missing"), [error, setError] = useState("");
  const generation = useRef(0), running = useRef(false), current = useRef(props); current.current = props;
  const blocked = loading || busy || pending || !state;
  const cancelled = !!state?.cancellation;
  const otherBlocked = () => !!current.current.otherWriteBusy || !!current.current.otherWriteBlocked?.();
  useLayoutEffect(() => { current.current.onBlocking?.(blocked); }, [blocked]);
  useLayoutEffect(() => { current.current.onCancelled?.(cancelled); }, [cancelled]);
  async function load(turn: number) {
    const observed = validateRemainingCancellationState(await adapter.remainingCancellationState!(detail.request_id), detail);
    if (turn !== generation.current) return;
    setState(observed); setAssessment(null);
    if (observed.cancel_permitted && adapter.remainingFulfillment) {
      const value = validateVersionedMaterialRequestRemainder(await adapter.remainingFulfillment(detail.request_id), detail);
      if (turn === generation.current) setAssessment(value);
    }
  }
  useEffect(() => {
    const turn = ++generation.current;
    void load(turn).catch(e => { if (turn === generation.current) setError(showError(e)); })
      .finally(() => { if (turn === generation.current) setLoading(false); });
    return () => { generation.current += 1; current.current.onBlocking?.(store.read().kind !== "missing"); current.current.onCancelled?.(false); };
    // Identity, request and version are fixed by the component key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);
  async function recover(turn: number) {
    const record = store.read();
    if (record.kind !== "valid" || record.value.request_id !== detail.request_id || !hasRemainingCancellationRecovery(adapter)) throw new Error("请在原需求中核验取消请求，保留浏览器数据");
    const result = await recoverRemainingCancellation(adapter, store, record.value, () => turn === generation.current);
    if (turn !== generation.current) return;
    setState(result.state); setPending(false); setAssessment(null); current.current.onDetail?.(result.detail);
  }
  async function verify() {
    if (running.current) return;
    const turn = generation.current; running.current = true; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      if (store.read().kind !== "missing") await recover(turn);
      else { setState(null); setAssessment(null); await load(turn); }
    } catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  let preview: ReturnType<typeof cancellationInputFromRemainder> | null = null, unavailable = "";
  if (assessment) {
    try { preview = cancellationInputFromRemainder(assessment, detail, reason.trim() || "核对剩余数量"); }
    catch (e) { unavailable = showError(e); }
  }
  async function submit() {
    if (running.current || blocked || cancelled || otherBlocked() || !preview || !state?.cancel_permitted
      || !adapter.cancelRemaining || !adapter.remainingFulfillment || !hasRemainingCancellationRecovery(adapter) || store.read().kind !== "missing") return;
    const turn = generation.current; running.current = true; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      const expected = cancellationInputFromRemainder(assessment, detail, reason.trim());
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) throw new Error("身份或权限变化，请刷新后核对");
      const fresh = validateRemainingCancellationState(await adapter.remainingCancellationState!(detail.request_id), detail);
      const input = cancellationInputFromRemainder(await adapter.remainingFulfillment(detail.request_id), detail, reason.trim());
      if (!fresh.cancel_permitted || fresh.cancellation || JSON.stringify(input) !== JSON.stringify(expected)) throw new Error("待取消数量或权限已变化，请刷新后重新核对");
      const fingerprint = await remainingCancelFingerprint(input);
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他操作尚未核验完成");
      const headers = new Headers(mutationHeaders("material-request-cancel-remaining").headers);
      const key = headers.get("Idempotency-Key")!, trace = headers.get("X-Request-ID")!;
      store.persist({ v: 1, trace, key, person_id: identity.person_id, authorization_version: identity.authorization_version,
        request_id: detail.request_id, input, fingerprint }); setPending(true);
      validateRemainingCancellation(await adapter.cancelRemaining(detail.request_id, input, { "Idempotency-Key": key, "X-Request-ID": trace }), detail);
      if (turn === generation.current) await recover(turn);
    } catch (e) { if (turn === generation.current) { setPending(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  return <section className="opening-detail-section" aria-label="取消剩余需求">
    <header><div><h3>取消剩余需求</h3><p>由原申请人取消全部未履约剩余数量。已入账物资保留，业务关闭另行确认。</p></div>
      <Button tone="secondary" disabled={busy || loading} onClick={() => void verify()}>{pending ? "只读核验原取消" : "刷新取消状态"}</Button></header>
    {loading && <p role="status">正在读取取消记录…</p>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pending && <div className="alert alert-warning" role="status">取消结果待核验，原请求已保留。请只读查询，不要重复提交。</div>}
    {cancelled && <div className="alert alert-info" role="status"><strong>剩余需求已取消</strong>
      <p>取消时间：{new Date(state!.cancellation!.cancelled_at).toLocaleString("zh-CN")}</p>
      {state!.cancellation!.lines.map(line => <p key={line.request_line_id}>明细 {detail.lines.find(item => item.request_line_id === line.request_line_id)?.line_no}：已取消 {line.cancelled_qty}</p>)}</div>}
    {state && !cancelled && !state.cancel_permitted && <p>当前账号或需求状态不允许取消剩余数量。</p>}
    {state?.cancel_permitted && !cancelled && <>
      {unavailable && <p role="status">{unavailable}</p>}
      {preview && <><div aria-label="待取消数量">{preview.lines.map(line => <p key={line.request_line_id}>明细 {detail.lines.find(item => item.request_line_id === line.request_line_id)?.line_no}：待取消 {line.cancelled_qty}</p>)}</div>
        <Field label="取消原因"><textarea aria-label="取消原因" value={reason} maxLength={500} disabled={blocked || props.otherWriteBusy} onChange={event => setReason(event.target.value)} /></Field>
        <Button disabled={blocked || props.otherWriteBusy || !reason.trim() || !hasRemainingCancellationRecovery(adapter) || !adapter.cancelRemaining} onClick={() => void submit()}>确认取消全部剩余需求</Button></>}
    </>}
  </section>;
}
