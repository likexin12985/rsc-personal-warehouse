import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { closeInputFingerprint, validateCloseInput, validateClosureState, validateClosureResult, type ClosureState } from "./materialRequestClosure";
import { validateMaterialRequestCompletion } from "./materialRequestCompletion";
import { createClosureStore, hasClosureRecovery, recoverClosure, type ClosureStore } from "./materialRequestClosureRecovery";
import { Button, Field, showError } from "./ui";

type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null; access: FormalMaterialRequestAccess | null;
  store?: ClosureStore; onBlocking?: (value: boolean) => void; onDetail?: (value: MaterialRequestDetail) => void;
  onClosed?: (value: boolean) => void; otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
export default function FormalMaterialRequestClosurePanel(props: Props) {
  if (!props.detail || !props.access || !props.adapter.closureState) return null;
  return <ClosurePanel key={`${props.detail.request_id}:${props.detail.request_version}:${JSON.stringify(props.access)}`}
    {...props} detail={props.detail} access={props.access} />;
}
function ClosurePanel(props: Props & { detail: MaterialRequestDetail; access: FormalMaterialRequestAccess }) {
  const { adapter, detail, access, onDetail } = props;
  const [store] = useState(() => props.store ?? createClosureStore());
  const [state, setState] = useState<ClosureState | null>(null), [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(() => store.read().kind !== "missing"), [error, setError] = useState("");
  const generation = useRef(0), running = useRef(false), current = useRef(props); current.current = props;
  const closed = state?.business_status === "closed";
  const finalApproval = ["approved", "partially_approved", "cancelled"].includes(detail.states.request_status);
  const blocked = loading || pending || busy || !state || closed;
  const otherBlocked = () => !!current.current.otherWriteBusy || !!current.current.otherWriteBlocked?.();
  useLayoutEffect(() => { current.current.onBlocking?.(blocked); }, [blocked]);
  useLayoutEffect(() => { current.current.onClosed?.(closed); }, [closed]);
  useEffect(() => {
    const turn = ++generation.current;
    void (async () => {
      try {
        const result = validateClosureState(await adapter.closureState!(detail.request_id), detail);
        if (turn !== generation.current) return;
        setState(result);
      } catch (e) { if (turn === generation.current) setError(showError(e)); }
      finally { if (turn === generation.current) setLoading(false); }
    })();
    return () => { generation.current += 1; current.current.onBlocking?.(store.read().kind !== "missing"); };
    // Request/version/identity are fixed by the component key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);
  async function recover(turn: number) {
    const record = store.read();
    if (record.kind !== "valid" || record.value.request_id !== detail.request_id || !hasClosureRecovery(adapter)) throw new Error("原关闭请求无法读取，请保留浏览器数据并在原需求中核验");
    const result = await recoverClosure(adapter, store, record.value, () => turn === generation.current);
    if (turn !== generation.current) return;
    setState(result.state); setPending(false); onDetail?.(result.detail);
  }
  async function verify() {
    if (running.current) return;
    const turn = generation.current; running.current = true; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      if (store.read().kind !== "missing") await recover(turn);
      else {
        const result = validateClosureState(await adapter.closureState!(detail.request_id), detail);
        if (turn === generation.current) setState(result);
      }
    } catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  async function submit() {
    if (running.current || blocked || otherBlocked() || !state?.close_permitted || !adapter.closeRequest || !adapter.completionQuantities || !hasClosureRecovery(adapter) || store.read().kind !== "missing") return;
    const turn = generation.current; running.current = true; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      const input = validateCloseInput({ expected_request_version: detail.request_version, reason: reason.trim() });
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) throw new Error("登录身份或权限已变化，请刷新后核验");
      const fresh = validateClosureState(await adapter.closureState(detail.request_id), detail);
      const coverage = validateMaterialRequestCompletion(await adapter.completionQuantities(detail.request_id), detail);
      if (turn !== generation.current) return;
      if (fresh.business_status !== "open" || !fresh.close_permitted || !coverage.quantity_coverage_complete || coverage.pending_inbound_orders) throw new Error("需求尚不能关闭，请先完成全部批准明细的入账或取消");
      const fingerprint = await closeInputFingerprint(input);
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他操作尚未完成，请先核验");
      const headers = new Headers(mutationHeaders("material-request-close").headers);
      const key = headers.get("Idempotency-Key")!, trace = headers.get("X-Request-ID")!;
      store.persist({ v: 1, trace, key, person_id: identity.person_id, authorization_version: identity.authorization_version,
        request_id: detail.request_id, input, fingerprint });
      setPending(true);
      validateClosureResult(await adapter.closeRequest(detail.request_id, input, { "Idempotency-Key": key, "X-Request-ID": trace }), detail);
      if (turn !== generation.current) return;
      await recover(turn);
    } catch (e) { if (turn === generation.current) { setPending(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  return <section className="opening-detail-section" aria-label="业务关闭">
    <header><div><h3>业务关闭</h3><p>全部批准明细入账或取消，并处理完未结履约后，由总部或区域负责人确认关闭。</p></div>
      <Button tone="secondary" disabled={busy || loading} onClick={() => void verify()}>{pending ? "只读核验原关闭" : "刷新关闭状态"}</Button></header>
    {loading && <p role="status">正在读取关闭记录…</p>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pending && <div className="alert alert-warning" role="status">关闭结果待核验，原请求已保留，暂时停止其他写入。请只读核验，不要重复提交。</div>}
    {closed && <div className="alert alert-info" role="status"><strong>业务已关闭</strong><p>关闭时间：{new Date(state.closure!.closed_at).toLocaleString("zh-CN")}</p><p>审批、物流签收、OAM收货和通知仍分别显示各自状态。</p></div>}
    {state?.business_status === "open" && <>
      {!state.close_permitted && <p>业务尚未关闭；当前账号没有此需求的关闭权限。</p>}
      {!finalApproval && <p>当前需求尚未进入最终批准或取消阶段。</p>}
      {state.close_permitted && finalApproval && <><Field label="关闭说明"><textarea aria-label="关闭说明" maxLength={500} value={reason}
        disabled={blocked || props.otherWriteBusy} onChange={e => setReason(e.target.value)} /></Field>
        <Button disabled={blocked || props.otherWriteBusy || !reason.trim() || !hasClosureRecovery(adapter) || !adapter.closeRequest} onClick={() => void submit()}>核验并关闭业务</Button></>}
    </>}
  </section>;
}
