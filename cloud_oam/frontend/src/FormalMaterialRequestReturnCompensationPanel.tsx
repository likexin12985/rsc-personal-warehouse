import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import { type FormalMaterialRequestAdapter, type FormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type ReturnCompensationCandidates, validateReturnCompensationCandidates, validateReturnCompensation,
  returnCompensationInputFromCandidate, returnCompensationFingerprint } from "./materialRequestReturnCompensation";
import { createReturnCompensationStore, hasReturnCompensationRecovery, recoverReturnCompensation, type ReturnCompensationStore } from "./materialRequestReturnCompensationRecovery";
import { Button, Field, showError } from "./ui";
type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail | null; access: FormalMaterialRequestAccess | null;
  store?: ReturnCompensationStore; onBlocking?: (value: boolean) => void; onDetail?: (detail: MaterialRequestDetail) => void;
  otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
export default function FormalMaterialRequestReturnCompensationPanel(props: Props) {
  if (!props.detail || !props.access || !props.adapter.returnCompensationCandidates || props.detail.request_version < 1) return null;
  return <Panel key={`${props.detail.request_id}:${props.detail.request_version}:${JSON.stringify(props.access)}`} {...props} detail={props.detail} access={props.access} />;
}
function Panel(props: Props & { detail: MaterialRequestDetail; access: FormalMaterialRequestAccess }) {
  const { adapter, detail, access } = props;
  const [store] = useState(() => props.store ?? createReturnCompensationStore());
  const [sources, setSources] = useState<ReturnCompensationCandidates | null>(null);
  const [selected, setSelected] = useState(""), [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [pending, setPending] = useState(() => store.read().kind !== "missing");
  const generation = useRef(0), running = useRef(false), current = useRef(props); current.current = props;
  const blocked = loading || busy || pending || !sources;
  const otherBlocked = () => !!current.current.otherWriteBusy || !!current.current.otherWriteBlocked?.();
  useLayoutEffect(() => { current.current.onBlocking?.(blocked); }, [blocked]);
  async function load(turn: number) {
    const value = validateReturnCompensationCandidates(await adapter.returnCompensationCandidates!(detail.request_id), detail);
    if (turn !== generation.current) return;
    setSources(value); setSelected("");
  }
  useEffect(() => {
    const turn = ++generation.current;
    void load(turn).catch(e => { if (turn === generation.current) setError(showError(e)); })
      .finally(() => { if (turn === generation.current) setLoading(false); });
    return () => { generation.current += 1; current.current.onBlocking?.(store.read().kind !== "missing"); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);
  async function recover(turn: number) {
    const record = store.read();
    if (record.kind !== "valid" || record.value.request_id !== detail.request_id || !hasReturnCompensationRecovery(adapter)) throw new Error("请在原需求中只读核验补偿，保留浏览器记录");
    const result = await recoverReturnCompensation(adapter, store, record.value, () => turn === generation.current);
    if (turn === generation.current) { setPending(false); setSources(result.candidates); setSelected(""); setReason(""); current.current.onDetail?.(result.detail); }
  }
  async function verify() {
    if (running.current) return;
    running.current = true; const turn = generation.current; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try { if (store.read().kind !== "missing") await recover(turn); else await load(turn); }
    catch (e) { if (turn === generation.current) setError(showError(e)); }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  async function submit() {
    const source = sources?.items.find(row => row.inbound_id === selected);
    if (!source || blocked || otherBlocked() || running.current || !adapter.compensateReturned || !hasReturnCompensationRecovery(adapter) || store.read().kind !== "missing") return;
    running.current = true; const turn = generation.current; setBusy(true); setError(""); current.current.onBlocking?.(true);
    try {
      const input = returnCompensationInputFromCandidate(source, detail, reason.trim());
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) throw new Error("身份变化，请刷新后核对退回来源");
      const fresh = validateReturnCompensationCandidates(await adapter.returnCompensationCandidates(detail.request_id), detail);
      const selectedSource = fresh.items.find(row => row.inbound_id === selected);
      if (!selectedSource || JSON.stringify(returnCompensationInputFromCandidate(selectedSource, detail, reason.trim())) !== JSON.stringify(input)) throw new Error("退回来源或权限已变化，请刷新后核对");
      const fingerprint = await returnCompensationFingerprint(input);
      if (turn !== generation.current) return;
      if (otherBlocked()) throw new Error("其他操作尚未核验完成");
      const headers = new Headers(mutationHeaders("material-request-return-compensation").headers);
      const key = headers.get("Idempotency-Key")!, trace = headers.get("X-Request-ID")!;
      store.persist({ v: 1, key, trace, person_id: identity.person_id, authorization_version: identity.authorization_version,
        request_id: detail.request_id, input, fingerprint }); setPending(true);
      validateReturnCompensation(await adapter.compensateReturned(detail.request_id, input, { "Idempotency-Key": key, "X-Request-ID": trace }));
      if (turn === generation.current) await recover(turn);
    } catch (e) { if (turn === generation.current) { setPending(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === generation.current) { running.current = false; setBusy(false); } }
  }
  return <section className="opening-detail-section" aria-label="已退回物资补偿">
    <header><div><h3>已退回物资补偿</h3><p>来源仓实际入账后，由原申请人确认不再补发。库存保留在来源仓，业务关闭另行确认。</p></div>
      <Button tone="secondary" disabled={busy || loading} onClick={() => void verify()}>{pending ? "只读核验原补偿" : "刷新退回来源"}</Button></header>
    {loading && <p role="status">正在核验退回入账…</p>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pending && <p role="status">原补偿请求已保留，核验完成前不要重复提交。</p>}
    {sources && !sources.items.length && <p>当前没有已核验的来源仓退回入账。</p>}
    {sources?.items.map(row => <p key={row.inbound_id}>{row.sku_code} · {row.material_name}：{row.quantity}，{row.compensation ? "已补偿取消" : "已入账，待补偿"}</p>)}
    {!!sources?.items.some(row => row.compensate_permitted) && <>
      <Field label="选择退回入账"><select aria-label="选择退回入账" value={selected} disabled={blocked || props.otherWriteBusy} onChange={event => setSelected(event.target.value)}>
        <option value="">请选择已入账记录</option>{sources.items.filter(row => row.compensate_permitted).map(row => <option key={row.inbound_id} value={row.inbound_id}>{row.sku_code} · {row.material_name} · {row.quantity} · {new Date(row.posted_at).toLocaleString("zh-CN")}</option>)}
      </select></Field>
      <Field label="补偿原因"><textarea aria-label="补偿原因" value={reason} maxLength={500} disabled={blocked || props.otherWriteBusy} onChange={event => setReason(event.target.value)} /></Field>
      <Button disabled={blocked || props.otherWriteBusy || !selected || !reason.trim() || !adapter.compensateReturned || !hasReturnCompensationRecovery(adapter)} onClick={() => void submit()}>确认本笔不再补发</Button>
    </>}
  </section>;
}
