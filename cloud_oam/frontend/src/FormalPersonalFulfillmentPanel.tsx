import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import type { FormalMaterialRequestAdapter, FormalMaterialRequestAccess } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import FormalFileUploadField, { type FormalFileUploadClient } from "./FormalFileUploadField";
import { buildReceipt, canonical, commandHash, commandResult, conditions, demand, inboundInput, inboundPage, receiptCandidate, receivingPage, tracked, units, type Condition, type InboundItem, type InboundPage, type PersonalCommand, type ReceiptCandidate, type ReceiptDraft, type ReceivingPage } from "./myFulfillmentContract";
import { personalAuthority, recoverPersonal, type PersonalStore } from "./myFulfillmentRecovery";
import { Button, Field, showError } from "./ui";
type Props = { adapter: FormalMaterialRequestAdapter; detail: MaterialRequestDetail; access: FormalMaterialRequestAccess; store: PersonalStore;
  uploadClient?: FormalFileUploadClient; onDetail: (detail: MaterialRequestDetail) => void; onBlocking: (value: boolean) => void;
  otherWriteBusy?: boolean; otherWriteBlocked?: () => boolean };
const emptyDraft = (): ReceiptDraft => ({ accepted: "", rejected: "", condition: "normal", serials: {}, evidenceFileId: null });
const candidateSignature = (candidate: ReceiptCandidate) => { const { checked_at: _, ...rest } = candidate; return canonical(rest); };
export default function FormalPersonalFulfillmentPanel(props: Props) {
  if (!props.adapter.personalFulfillment) return null;
  return <PersonalPanel key={`${props.detail.request_id}:${props.detail.request_version}:${canonical(props.access)}`} {...props} />;
}
function PersonalPanel(props: Props) {
  const { adapter, detail, access, store } = props, api = adapter.personalFulfillment!;
  const [packages, setPackages] = useState<ReceivingPage | null>(null), [inbounds, setInbounds] = useState<InboundPage | null>(null);
  const [packageCursors, setPackageCursors] = useState<(string | null)[]>([null]), [inboundCursors, setInboundCursors] = useState<(string | null)[]>([null]);
  const [candidate, setCandidate] = useState<ReceiptCandidate | null>(null), [drafts, setDrafts] = useState<Record<string, ReceiptDraft>>({});
  const [review, setReview] = useState<PersonalCommand | null>(null), [scan, setScan] = useState("");
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [pending, setPending] = useState(() => store.read().kind !== "missing"), [uploads, setUploads] = useState<Record<string, boolean>>({});
  const live = useRef(0), running = useRef(false), current = useRef(props); current.current = props;
  const uploadPending = Object.values(uploads).some(Boolean), blocked = busy || pending || uploadPending;
  const locked = blocked || loading || !!props.otherWriteBusy;
  const otherBlocked = () => !!current.current.otherWriteBusy || !!current.current.otherWriteBlocked?.();
  useLayoutEffect(() => { current.current.onBlocking(blocked); }, [blocked]);
  async function authority() { const result = await personalAuthority(adapter, access.person_id); demand(result.identity.authorization_version === access.authorization_version, "权限版本已变化，请刷新"); return result; }
  async function load(turn: number, pAfter: string | null, iAfter: string | null) {
    if (detail.request_version < 1) return;
    const before = await authority();
    const [p, i] = await Promise.all([api.packages(detail.request_id, pAfter), api.inbounds(detail.request_id, iAfter)]);
    const checkedP = receivingPage(p, detail.request_id, access.person_id, pAfter), checkedI = inboundPage(i, detail.request_id, access.person_id, iAfter);
    demand(checkedP.packages.length <= 5 && checkedI.items.length <= 5 && checkedP.request_version === detail.request_version && checkedI.request_version === detail.request_version, "需求版本已变化，请刷新需求详情");
    demand(canonical(before) === canonical(await authority()), "读取期间权限发生变化"); if (turn !== live.current) return;
    setPackages(checkedP); setInbounds(checkedI);
  }
  useEffect(() => {
    const turn = ++live.current; setLoading(true);
    void load(turn, null, null).catch(e => { if (turn === live.current) setError(showError(e)); }).finally(() => { if (turn === live.current) setLoading(false); });
    return () => { live.current += 1; current.current.onBlocking(store.read().kind !== "missing"); };
    // The wrapper fixes the request version and identity for this instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store]);
  async function action(work: (turn: number) => Promise<void>, readOnly = false) {
    if (running.current || (!readOnly && (locked || otherBlocked()))) return;
    const turn = live.current; running.current = true; setBusy(true); setError(""); current.current.onBlocking(true);
    try { await work(turn); } catch (e) { if (turn === live.current) { setPending(store.read().kind !== "missing"); setError(showError(e)); } }
    finally { if (turn === live.current) { running.current = false; setBusy(false); } }
  }
  async function refresh(p = packageCursors, i = inboundCursors) {
    await action(async turn => { setCandidate(null); setDrafts({}); setReview(null); setUploads({}); setPackages(null); setInbounds(null); await load(turn, p[p.length - 1], i[i.length - 1]); if (turn === live.current) { setPackageCursors(p); setInboundCursors(i); } }, true);
  }
  async function choosePackage(shipmentId: string) {
    await action(async turn => {
      const before = await authority(); const checked = receiptCandidate(await api.receiptCandidate(detail.request_id, shipmentId), detail.request_id, shipmentId, access.person_id);
      demand(checked.request_version === detail.request_version && canonical(before) === canonical(await authority()), "待验收明细或权限已变化，请刷新");
      if (turn !== live.current) return; setCandidate(checked); setDrafts({}); setReview(null); setUploads({}); setScan("");
    });
  }
  function update(lineId: string, patch: Partial<ReceiptDraft>) { setReview(null); setDrafts(previous => ({ ...previous, [lineId]: { ...(previous[lineId] ?? emptyDraft()), ...patch } })); }
  async function recover(turn: number) {
    const saved = store.read(); demand(saved.kind === "valid" && saved.value.request_id === detail.request_id, "请打开原需求并使用原身份核验；不要清理浏览器数据");
    const result = await recoverPersonal(adapter, api, store, saved.value, () => turn === live.current);
    if (turn !== live.current) return; setPending(false); setReview(null); current.current.onDetail(result.detail);
  }
  async function confirm() {
    await action(async turn => {
      demand(review && store.read().kind === "missing", "原请求尚未核验完成"); const before = await authority();
      let command: PersonalCommand;
      if (review.kind === "receipt") {
        demand(candidate); const fresh = receiptCandidate(await api.receiptCandidate(detail.request_id, candidate.shipment_id), detail.request_id, candidate.shipment_id, access.person_id);
        demand(candidateSignature(fresh) === candidateSignature(candidate), "包裹待验收数量或权限已变化，请重新预览"); command = { kind: "receipt", input: buildReceipt(fresh, drafts, review.input.received_at) };
      } else {
        const fresh = inboundPage(await api.inbounds(detail.request_id, inboundCursors[inboundCursors.length - 1]), detail.request_id, access.person_id, inboundCursors[inboundCursors.length - 1]);
        const row = fresh.items.find(x => x.receipt_id === review.input.receipt_id), previous = inbounds?.items.find(x => x.receipt_id === review.input.receipt_id);
        demand(fresh.can_post && fresh.request_version === detail.request_version && row?.status === "pending" && row.detail && canonical(row) === canonical(previous), "本人验收记录或入账权限已变化，请重新预览");
        command = { kind: "inbound", input: inboundInput({ expected_request_version: fresh.request_version, receipt_id: row.receipt_id, receipt_request_hash: row.detail.receipt_request_hash }) };
      }
      demand(canonical(command) === canonical(review) && canonical(before) === canonical(await authority()), "提交前身份、权限或内容发生变化");
      const fingerprint = await commandHash(detail.request_id, access.person_id, command);
      if (turn !== live.current) return; demand(!otherBlocked(), "其他操作尚未完成核验");
      const headers = new Headers(mutationHeaders(`my-${command.kind}`).headers), key = headers.get("Idempotency-Key")!, trace = headers.get("X-Request-ID")!;
      store.persist({ v: 1, request_id: detail.request_id, person_id: access.person_id, authorization_version: before.identity.authorization_version, key, trace, fingerprint, command }); setPending(true);
      const output = await api.submit(detail.request_id, command, key, trace); await commandResult(output, detail.request_id, access.person_id, command, fingerprint);
      if (turn === live.current) await recover(turn);
    });
  }
  function previewReceipt() { try { demand(candidate); setReview({ kind: "receipt", input: buildReceipt(candidate, drafts, new Date().toISOString()) }); setError(""); } catch (e) { setError(showError(e)); } }
  function previewInbound(row: InboundItem) { try { demand(inbounds?.can_post && row.status === "pending" && row.detail); setReview({ kind: "inbound", input: inboundInput({ expected_request_version: inbounds.request_version, receipt_id: row.receipt_id, receipt_request_hash: row.detail.receipt_request_hash }) }); setError(""); } catch (e) { setError(showError(e)); } }
  function scanAccepted() { try { demand(candidate); const matches = candidate.lines.flatMap(line => line.remaining_serials.filter(s => s.qr_code === scan.trim() || s.serial_no === scan.trim()).map(s => ({ line, serial: s }))); demand(matches.length === 1, "扫描结果不唯一或不属于本包裹的待验收SN"); const hit = matches[0]; update(hit.line.shipment_line_id, { serials: { ...drafts[hit.line.shipment_line_id]?.serials, [hit.serial.serial_id]: "accepted" } }); setScan(""); setError(""); } catch (e) { setError(showError(e)); } }
  const pageButtons = (kind: "packages" | "inbounds") => {
    const cursors = kind === "packages" ? packageCursors : inboundCursors, next = kind === "packages" ? packages?.next_after_id : inbounds?.next_after_id;
    const go = (values: (string | null)[]) => kind === "packages" ? refresh(values, inboundCursors) : refresh(packageCursors, values);
    return <div className="form-actions"><Button tone="secondary" disabled={locked || cursors.length < 2} onClick={() => void go(cursors.slice(0, -1))}>上一页</Button><span>第 {cursors.length} 页</span><Button tone="secondary" disabled={locked || !next} onClick={() => void go([...cursors, next!])}>下一页</Button></div>;
  };
  return <section className="opening-detail-section personal-fulfillment" aria-label="本人收货与入账">
    <header><div><h3>本人收货与入账</h3><p>只显示发给本人的包裹。验收与个人仓入账分别确认，拒收及异常单独保留。</p></div><Button tone="secondary" disabled={busy || uploadPending} onClick={() => void refresh([null], [null])}>刷新本人记录</Button></header>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pending && <div className="alert alert-warning" role="status"><p>原收货或入账结果待核验，请保留浏览器数据；不要重复提交。</p><Button disabled={busy || loading} onClick={() => void action(recover, true)}>只读核验原收货或入账</Button></div>}
    {detail.request_version < 1 && <p>需求提交并发运后，在此查看本人包裹。</p>}
    {loading && <p role="status">正在核验本人包裹与个人仓入账…</p>}
    {packages && <section aria-label="本人包裹"><h4>本人包裹</h4>{!packages.packages.length && <p>暂无发给本人的包裹。</p>}{packages.packages.map(p => <article key={p.shipment_id} className="personal-package"><strong>{p.shipment_no}</strong><p>{p.carrier} · {p.tracking_no}</p><p>收货位置：{p.target_location_name}</p>{p.lines.map(l => <p key={l.shipment_line_id}>{l.sku_code} · {l.material_name}：发运 {l.shipped_qty}，合格 {l.accepted_qty}，拒收 {l.rejected_qty}，待验收 {l.unconfirmed_qty} {l.base_unit}</p>)}<Button tone="secondary" disabled={locked} onClick={() => void choosePackage(p.shipment_id)}>核对包裹 {p.shipment_no}</Button></article>)}{pageButtons("packages")}</section>}
    {candidate && <section aria-label="本人验收表单"><h4>验收 {candidate.shipment_no}</h4>{!candidate.can_receive && <p>当前包裹已完成验收、尚未交运或缺少验收权限；请以最新本人记录为准。</p>}
      {candidate.lines.some(tracked) && <div className="form-actions"><Field label="扫描SN或二维码"><input aria-label="扫描SN或二维码" value={scan} disabled={locked || !candidate.can_receive} onChange={e => setScan(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); scanAccepted(); } }} /></Field><Button tone="secondary" disabled={locked || !scan.trim() || !candidate.can_receive} onClick={scanAccepted}>匹配并标记合格</Button></div>}
      {candidate.lines.map(line => { const draft = drafts[line.shipment_line_id] ?? emptyDraft(); return <fieldset disabled={busy || pending || loading || !!props.otherWriteBusy || !candidate.can_receive} key={line.shipment_line_id}><legend>{line.sku_code} · {line.material_name}</legend><p>待验收 {line.unconfirmed_qty} {line.base_unit}{line.lot_no ? ` · 批次 ${line.lot_no}` : ""}</p>
        <Field label="验收条件"><select aria-label={`验收条件 ${line.sku_code}`} value={draft.condition} onChange={e => { setUploads(before => ({ ...before, [line.shipment_line_id]: false })); update(line.shipment_line_id, { condition: e.target.value as Condition, evidenceFileId: null }); }}>{Object.entries(conditions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
        {tracked(line) ? <div className="personal-serials">{line.remaining_serials.map(serial => <Field key={serial.serial_id} label={serial.serial_no}><select aria-label={`SN ${serial.serial_no}`} value={draft.serials[serial.serial_id] ?? ""} onChange={e => { const serials = { ...draft.serials }; if (!e.target.value) delete serials[serial.serial_id]; else serials[serial.serial_id] = e.target.value as "accepted" | "rejected"; update(line.shipment_line_id, { serials }); }}><option value="">本次不验收</option><option value="accepted">合格</option><option value="rejected">拒收</option></select></Field>)}</div> : <div className="form-grid"><Field label="本次合格数量"><input aria-label={`合格数量 ${line.sku_code}`} inputMode="decimal" value={draft.accepted} onChange={e => update(line.shipment_line_id, { accepted: e.target.value })} /></Field><Field label="本次拒收数量"><input aria-label={`拒收数量 ${line.sku_code}`} inputMode="decimal" value={draft.rejected} onChange={e => update(line.shipment_line_id, { rejected: e.target.value })} /></Field></div>}
        {draft.condition !== "normal" && <FormalFileUploadField purpose="receipt_exception_evidence" bindingKey={`${detail.request_id}:${candidate.shipment_id}:${line.shipment_line_id}:${draft.condition}`} label="异常验收凭证" client={props.uploadClient} disabled={busy || pending || !!props.otherWriteBusy} onAvailableChange={files => update(line.shipment_line_id, { evidenceFileId: files[0]?.file_id ?? null })} onBlockingChange={value => setUploads(before => ({ ...before, [line.shipment_line_id]: value }))} />}
      </fieldset>; })}<Button disabled={locked || !candidate.can_receive} onClick={previewReceipt}>预览本次验收</Button></section>}
    {inbounds && <section aria-label="本人入账记录"><h4>本人入账记录</h4>{!inbounds.items.length && <p>暂无本人验收记录。</p>}{inbounds.items.map(row => <article key={row.receipt_id} className="personal-package"><strong>{row.detail?.receipt_no ?? "验收待核验"}</strong><p>{row.message}</p>{row.detail && <><p>包裹 {row.detail.shipment_no} · {row.detail.target_location_name}</p>{row.detail.lines.map(line => <p key={line.receipt_line_id}>{line.sku_code} · {line.material_name} · 验收条件：{conditions[line.condition]}：合格 {line.accepted_qty}，拒收 {line.rejected_qty} {line.base_unit}{line.accepted_serials.length ? ` · 合格SN：${line.accepted_serials.map(s => s.serial_no).join("、")}` : ""}{line.rejected_serials.length ? ` · 拒收SN（不入账）：${line.rejected_serials.map(s => s.serial_no).join("、")}` : ""}</p>)}{row.status === "posted" && <p>已入账：{row.detail.inbound_no}</p>}</>}{row.status === "pending" && <Button disabled={locked || !inbounds.can_post} onClick={() => previewInbound(row)}>核对入账 {row.detail?.receipt_no}</Button>}</article>)}{pageButtons("inbounds")}</section>}
    {review && <section aria-label="本人履约确认" className="personal-command-review"><h4>{review.kind === "receipt" ? "确认本次验收" : "确认个人仓入账"}</h4>{review.kind === "receipt" ? review.input.lines.map(line => <p key={line.shipment_line_id}>{candidate?.lines.find(x => x.shipment_line_id === line.shipment_line_id)?.material_name}：合格 {line.accepted_qty}，拒收 {line.rejected_qty}；{conditions[line.condition]}</p>) : <p>按所选验收单的合格数量和SN入账，不修改原验收结果。</p>}<p>{review.kind === "receipt" ? "确认验收后仍需单独确认个人仓入账。" : "确认后生成独立库存流水，已入账记录不会重复入账。"}</p><div className="form-actions"><Button disabled={locked} onClick={() => void confirm()}>{review.kind === "receipt" ? "确认验收" : "确认本人入账"}</Button><Button tone="secondary" disabled={busy || pending} onClick={() => setReview(null)}>返回修改</Button></div></section>}
  </section>;
}
