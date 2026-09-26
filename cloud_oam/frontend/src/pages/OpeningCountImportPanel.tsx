import { useEffect, useMemo, useRef, useState } from "react";
import type { OpeningStocktakeTaskDetail } from "../formalOpeningStocktake";
import { createOpeningImportWorkflow, type OpeningImportActor, type OpeningImportWorkflow,
  type OpeningImportWorkflowResult, type OpeningImportManagementResult, type OpeningImportSealResult } from "../openingCountImportWorkflow";
import { OPENING_IMPORT_SEALABLE_PHASES, type OpeningImportRecordRead } from "../openingCountImportRecoveryStore";
import type { OpeningImportDownload, OpeningImportStatus, OpeningImportManagementReview } from "../openingCountImportClient";
import { Button } from "../ui";

const LABELS = { queued: "等待预校验", prevalidating: "正在预校验", awaiting_confirmation: "预校验通过，等待确认",
  running: "正在确认", succeeded: "实盘计数已提交", failed: "导入未完成", cancelled: "已取消" };
const REASONS: Record<string, string> = { opening_import_context_changed: "原任务、范围或权限已变化。",
  opening_import_source_invalid: "文件无法按盘点模板读取，请修正 XLSX。", opening_import_cancelled: "导入已取消。",
  opening_import_prevalidation_failed: "预校验未通过，请下载错误报告后修正。" };

export default function OpeningCountImportPanel({ detail, actor, onFinished, onBlockedChange, canCount = true, canManage = false, onManagementFinished, onSealFinished, workflow: supplied }: {
  detail: OpeningStocktakeTaskDetail; actor: OpeningImportActor;
  onFinished: (status: OpeningImportStatus) => void; onBlockedChange?: (blocked: boolean) => void;
  canCount?: boolean; canManage?: boolean;
  onManagementFinished?: (review: OpeningImportManagementReview) => void;
  onSealFinished?: () => void;
  workflow?: OpeningImportWorkflow;
}) {
  const context = `${detail.task_id}:${actor.person_id}:${actor.authorization_version}:${canCount}:${canManage}`;
  const contextRef = useRef(context); contextRef.current = context;
  const mounted = useRef(true), running = useRef(false);
  const callbacks = useRef({ onFinished, onBlockedChange, onManagementFinished, onSealFinished }); callbacks.current = { onFinished, onBlockedChange, onManagementFinished, onSealFinished };
  const workflow = useMemo(() => supplied ?? createOpeningImportWorkflow(actor, {
    isCurrent: () => mounted.current && contextRef.current === context,
  }), [supplied, context]);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [record, setRecord] = useState<OpeningImportRecordRead>({ kind: "unavailable" });
  const [file, setFile] = useState<File | null>(null), [scopeId, setScopeId] = useState("");
  const [result, setResult] = useState<OpeningImportWorkflowResult | null>(null);
  const [management, setManagement] = useState<OpeningImportManagementResult | null>(null);
  const [managementReviewed, setManagementReviewed] = useState(false);
  const [seal, setSeal] = useState<OpeningImportSealResult | null>(null), [sealReviewed, setSealReviewed] = useState(false);
  const [download, setDownload] = useState<OpeningImportDownload | null>(null);
  const [reviewed, setReviewed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const sameActor = record.kind === "valid" && record.value.actor_person_id === actor.person_id
    && record.value.actor_authorization_version === actor.authorization_version;
  const visible = canCount && sameActor && result?.review.actor_person_id === actor.person_id
    && result.review.authorization_version === actor.authorization_version
    && result.record.job_id === record.value.job_id ? result : null;

  const managed = canManage && record.kind === "valid" && management
    && management.review.reviewer_person_id === actor.person_id
    && management.review.reviewer_authorization_version === actor.authorization_version
    && JSON.stringify(management.record) === JSON.stringify(record.value) ? management : null;
  const mayInspect = canManage && record.kind === "valid" && record.value.file_id !== null
    && ["job_requested", "job_bound", "confirmation_requested", "cancellation_requested", "seal_requested", "seal_conflict"].includes(record.value.phase);
  const maySeal = canManage && record.kind === "valid"
    && (OPENING_IMPORT_SEALABLE_PHASES as readonly string[]).includes(record.value.phase);
  const sealPending = record.kind === "valid" && record.value.phase === "seal_requested";
  const sealed = canManage && sealPending && seal
    && seal.proof.reviewer_person_id === actor.person_id && seal.proof.reviewer_authorization_version === actor.authorization_version
    && JSON.stringify(seal.record) === JSON.stringify(record.value) ? seal : null;

  function refresh() {
    const next = workflow.store.read(detail.task_id);
    setRecord(next);
    callbacks.current.onBlockedChange?.(running.current || next.kind !== "missing");
  }
  useEffect(() => {
    mounted.current = true;
    let active = true;
    setEnabled(null); setBusy(false); setResult(null); setManagement(null); setManagementReviewed(false); setSeal(null); setSealReviewed(false); setDownload(null); setFile(null); setScopeId(""); setReviewed(false); setError("");
    refresh();
    workflow.available().then(value => { if (active) setEnabled(value); })
      .catch(() => { if (active) setError("导入能力暂不可用，原请求记录已保留。"); });
    const changed = () => { if (active) { setResult(null); setManagement(null); setManagementReviewed(false); setSeal(null); setSealReviewed(false); setDownload(null); setReviewed(false); refresh(); } };
    window.addEventListener("storage", changed);
    return () => { active = false; mounted.current = false; window.removeEventListener("storage", changed); };
  }, [workflow, context]);
  useEffect(() => {
    if (!download) return;
    const remaining = Date.parse(download.download.expires_at) - Date.now();
    if (remaining <= 0) { setDownload(null); return; }
    const timer = window.setTimeout(() => setDownload(null), remaining);
    return () => window.clearTimeout(timer);
  }, [download]);
  async function perform(work: () => Promise<void>, localDraftOnly = false) {
    if (running.current || (!localDraftOnly && enabled !== true)) return;
    running.current = true; setBusy(true); setError(""); setReviewed(false); setDownload(null); setManagement(null); setManagementReviewed(false); setSeal(null); setSealReviewed(false);
    callbacks.current.onBlockedChange?.(true);
    const expected = context;
    try { await work(); }
    catch { if (mounted.current && contextRef.current === expected) {
      const latest = workflow.store.read(detail.task_id);
      setResult(null); setError(latest.kind === "valid" && latest.value.phase === "seal_requested"
        ? "本次停止结果尚未核验，原记录继续保留。请核验原停止结果；如任务已受理，请使用管理核验原导入。不要重复提交。"
        : "本次操作结果尚未核验。请恢复原导入；不要重复申请或重新确认。若权限已变化，请联系管理员核验原任务。");
    } }
    finally { running.current = false; if (mounted.current && contextRef.current === expected) { setBusy(false); refresh(); } }
  }
  function present(value: OpeningImportWorkflowResult) {
    if (mounted.current && contextRef.current === context) setResult(value);
  }
  const preUpload = sameActor && ["prepared", "intent_requested", "source_bound"].includes(record.value.phase);
  const needsFile = record.kind === "missing" || preUpload;
  const scopes = detail.scopes.filter(scope => scope.assigned_to_me && scope.completion_status === "pending");
  const mayStart = canCount && detail.status === "counting" && detail.current_round?.status === "counting"
    && detail.allowed_actions.includes("count") && scopes.length > 0;
  const terminal = !!visible && ["succeeded", "failed", "cancelled"].includes(visible.review.result.status);
  const canConfirm = visible?.record.phase === "job_bound" && visible.review.can_confirm && !terminal;
  const canCancel = visible && !terminal && ["job_bound", "confirmation_requested", "seal_conflict"].includes(visible.record.phase);
  const reviewedRound = visible && detail.current_round?.round_id === visible.review.round_id
    && detail.current_round.round_no > 0 ? `第 ${detail.current_round.round_no} 轮` : visible?.review.round_id;
  const reviewedScope = visible && detail.scopes.find(scope => scope.scope_id === visible.review.scope_id);
  if (enabled === false && record.kind === "missing") return null;

  return <section className="content-section opening-import-panel" aria-label="期初盘点 Excel 导入">
    <div className="content-title"><div><h3>期初盘点 Excel 导入</h3>
      <p>先上传并预校验，再核对确认。确认只提交实盘计数，后续复核与期初入账另行执行。</p></div></div>
    <div className="opening-import-body">
    {enabled === false && <p role="status">导入当前未启用，已有恢复记录继续保留。</p>}
    {record.kind === "valid" && !sameActor && <p role="alert">{canManage ? "原导入属于不同身份或旧权限版本，请核对原导入记录。" : "原导入属于不同身份或权限版本，请联系管理员核验；不能覆盖原记录。"}</p>}
    {["corrupt", "unavailable"].includes(record.kind) && <p role="alert">本地恢复记录不可用，已停止导入。</p>}
    {record.kind === "missing" && mayStart && <label className="field"><span>导入盘点范围</span>
      <select aria-label="导入盘点范围" value={scopeId} disabled={busy || enabled !== true} onChange={event => setScopeId(event.target.value)}>
        <option value="">请选择本人负责的范围</option>
        {scopes.map(scope => <option key={scope.scope_id} value={scope.scope_id}>范围 {scope.scope_no} · {scope.location_id}</option>)}
      </select></label>}
    {canCount && needsFile && (mayStart || sameActor) && <label className="field"><span>{preUpload ? "重新选择原 XLSX 文件" : "选择 XLSX 文件（最多 8 MiB）"}</span>
      <input key={record.kind === "valid" ? record.value.phase : record.kind} aria-label="导入 XLSX 文件" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        disabled={busy || enabled !== true} onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>}
    {sameActor && !sealPending && <p>已有导入记录。刷新或关闭页面后，请继续恢复原导入；上传结果未知时不会重新上传。</p>}
    {sealPending && !sealed && <p>原导入已请求永久停止，记录继续保留。由当前有权的负责人核验停止结果；已经受理的任务仍按原任务状态处理。</p>}
    {record.kind === "valid" && record.value.phase === "seal_conflict" && <p>原任务已受理，永久停止未生效。原申请人可恢复并取消这个任务；结果核验前继续保留记录。</p>}
    {sameActor && record.value.phase === "prepared" && <p>本地草稿尚未发出上传请求，可以放弃后重新选择文件。</p>}
    {visible && <div role="status">
      <strong>{LABELS[visible.review.result.status]}</strong>
      <dl className="detail-grid">
        <div><dt>源文件</dt><dd>{visible.review.original_filename}</dd></div>
        <div><dt>原轮次</dt><dd>{reviewedRound}</dd></div>
        <div><dt>原范围</dt><dd>{reviewedScope ? `范围 ${reviewedScope.scope_no}` : visible.review.scope_id}</dd></div>
        <div><dt>预检行数</dt><dd>{visible.review.result.row_count ?? "尚未完成"}</dd></div>
        <div><dt>错误项</dt><dd>{visible.review.result.error_count}</dd></div>
      </dl>
      <details><summary>文件核对信息</summary><p>{visible.review.size_bytes} 字节</p><p className="mono">SHA-256：{visible.review.source_sha256}</p><p>导入任务：{visible.review.job_id}</p><p>原轮次：{visible.review.round_id}</p><p>原范围：{visible.review.scope_id}</p></details>
      {visible.review.result.failure_code && <p>{REASONS[visible.review.result.failure_code]}</p>}
      {visible.review.result.status === "succeeded" && <p>实盘计数提交已核验。尚不代表区域复核、总部复核或期初入账完成。</p>}
      {visible.record.phase === "confirmation_requested" && !terminal && <p>原确认结果待核验，请刷新原任务；不会重复确认。</p>}
    </div>}
    {managed && <div role="status">
      <strong>管理核验：{LABELS[managed.review.status]}</strong>
      <p>{managed.review.terminal_verified
        ? "服务端已核验原导入的终态与审计记录。确认后仅解除本设备的导入阻塞，不删除服务端记录。"
        : sealPending ? "原导入已受理，永久停止未生效。可以关联这个原任务，由原申请人恢复并取消；记录继续保留。"
          : "原导入仍在处理中，保留恢复记录。不能清除或重新提交。"}</p>
      {managed.review.status === "succeeded" && <p>原实盘计数已提交；区域复核、总部复核与期初入账仍需独立完成。</p>}
      <details><summary>原导入核验信息</summary>
        <p>原申请人：{managed.review.actor_person_id}</p><p>导入任务：{managed.review.job_id}</p>
        <p>原轮次：{managed.review.round_id}</p><p>原范围：{managed.review.scope_id}</p>
        <p>{managed.review.size_bytes} 字节</p><p className="mono">SHA-256：{managed.review.source_sha256}</p>
        {managed.review.terminal_audit_id && <p>终态审计：{managed.review.terminal_audit_id}</p>}
      </details>
      {managed.review.terminal_verified && <label className="reservation-serial-option">
        <input type="checkbox" checked={managementReviewed} disabled={busy}
          onChange={event => setManagementReviewed(event.target.checked)} />
        <span>我已核对原导入终态，确认解除本设备的导入阻塞</span>
      </label>}
      {sealPending && !managed.review.terminal_verified && <label className="reservation-serial-option">
        <input type="checkbox" checked={managementReviewed} disabled={busy} onChange={event => setManagementReviewed(event.target.checked)} />
        <span>我已核对已受理的原任务，保留记录供原申请人取消</span>
      </label>}
    </div>}
    {maySeal && <div>
      <p>如不再继续这次导入，可请求永久停止。已受理的任务会保留，需另行核验；已上传的文件也会保留。</p>
      <details><summary>待停止的原导入</summary>
        <p>原申请人：{record.value.actor_person_id}</p><p>原轮次：{record.value.round_id}</p><p>原范围：{record.value.scope_id}</p>
        <p>{record.value.size_bytes} 字节</p><p className="mono">SHA-256：{record.value.source_sha256}</p>
      </details>
      <label className="reservation-serial-option"><input type="checkbox" checked={sealReviewed} disabled={busy || enabled !== true}
        onChange={event => setSealReviewed(event.target.checked)} /><span>我已核对原导入，确认永久停止这次请求</span></label>
    </div>}
    {sealed && <div role="status">
      <strong>原导入已永久停止</strong>
      <p>服务端已核实这次请求未被受理并阻止其后续执行。文件可能仍然存在；本操作不产生实盘计数或库存入账。</p>
      <details><summary>停止结果核验信息</summary>
        <p>原申请人：{sealed.proof.actor_person_id}</p><p>原轮次：{sealed.proof.round_id}</p><p>原范围：{sealed.proof.scope_id}</p>
        <p>{sealed.proof.size_bytes} 字节</p><p className="mono">SHA-256：{sealed.proof.source_sha256}</p>
        <p>终结记录：{sealed.proof.seal_id}</p><p>终态审计：{sealed.proof.terminal_audit_id}</p>
      </details>
      <label className="reservation-serial-option"><input type="checkbox" checked={sealReviewed} disabled={busy}
        onChange={event => setSealReviewed(event.target.checked)} /><span>我已核对停止结果，确认解除本设备的导入阻塞</span></label>
    </div>}
    {canConfirm && <label className="reservation-serial-option"><input type="checkbox" checked={reviewed} disabled={busy}
      onChange={event => setReviewed(event.target.checked)} /><span>我已核对源文件、原轮次、范围和预检行数，确认提交实盘计数</span></label>}
    <div className="section-actions">
      {maySeal && <Button tone="secondary" disabled={busy || enabled !== true || !sealReviewed}
        onClick={() => void perform(async () => {
          const value = await workflow.requestSeal(detail.task_id, record.value);
          if (mounted.current && contextRef.current === context) setSeal(value);
        })}>永久停止原导入</Button>}
      {canManage && sealPending && <Button tone="secondary" disabled={busy || enabled !== true}
        onClick={() => void perform(async () => {
          const value = await workflow.inspectSeal(detail.task_id);
          if (mounted.current && contextRef.current === context) setSeal(value);
        })}>核验原停止结果</Button>}
      {sealed && <Button tone="secondary" disabled={busy || enabled !== true || !sealReviewed}
        onClick={() => void perform(async () => {
          await workflow.finishSeal(detail.task_id, sealed);
          if (mounted.current && contextRef.current === context) {
            setResult(null); setFile(null); setScopeId(""); callbacks.current.onSealFinished?.();
          }
        })}>复核停止结果并解除本地阻塞</Button>}
      {mayInspect && <Button tone="secondary" disabled={busy || enabled !== true}
        onClick={() => void perform(async () => {
          const value = await workflow.inspectManagement(detail.task_id);
          if (mounted.current && contextRef.current === context) setManagement(value);
        })}>管理核验原导入</Button>}
      {managed?.review.terminal_verified && <Button tone="secondary" disabled={busy || enabled !== true || !managementReviewed}
        onClick={() => void perform(async () => {
          const value = await workflow.finishManagement(detail.task_id, managed);
          if (mounted.current && contextRef.current === context) {
            setResult(null); setFile(null); setScopeId("");
            callbacks.current.onManagementFinished?.(value);
          }
        })}>复核终态并解除本地阻塞</Button>}
      {managed && sealPending && !managed.review.terminal_verified && <Button tone="secondary" disabled={busy || enabled !== true || !managementReviewed}
        onClick={() => void perform(async () => { await workflow.bindAcceptedSeal(detail.task_id, managed); })}>关联已受理的原任务</Button>}
      {sameActor && record.value.phase === "prepared" && <Button tone="secondary" disabled={busy}
        onClick={() => void perform(async () => {
          await workflow.discardPrepared(detail.task_id);
          if (mounted.current && contextRef.current === context) {
            setFile(null); setScopeId(""); setResult(null);
          }
        }, true)}>放弃未发送草稿</Button>}
      {record.kind === "missing" && mayStart && <Button disabled={busy || enabled !== true || !file || !scopeId}
        onClick={() => void perform(async () => { present(await workflow.start(detail.task_id, detail.current_round!.round_id, scopeId, file!)); })}>上传并预校验</Button>}
      {canCount && sameActor && !sealPending && <Button tone="secondary" disabled={busy || enabled !== true || (preUpload && !file)}
        onClick={() => void perform(async () => { present(await workflow.resume(detail.task_id, file ?? undefined)); })}>{visible ? "刷新原导入状态" : "恢复原导入"}</Button>}
      {canConfirm && <Button disabled={busy || enabled !== true || !reviewed}
        onClick={() => void perform(async () => { present(await workflow.decide(detail.task_id, visible!.review, "confirm")); })}>确认导入计数</Button>}
      {canCancel && <Button tone="secondary" disabled={busy || enabled !== true}
        onClick={() => void perform(async () => { present(await workflow.decide(detail.task_id, visible!.review, "cancel")); })}>取消本次导入</Button>}
      {visible?.review.result.error_file_available && <Button tone="secondary" disabled={busy || enabled !== true}
        onClick={() => void perform(async () => {
          const value = await workflow.errorDownload(detail.task_id);
          if (mounted.current && contextRef.current === context) setDownload(value);
        })}>获取错误报告</Button>}
      {terminal && <Button tone="secondary" disabled={busy || enabled !== true} onClick={() => void perform(async () => {
        const status = await workflow.finish(detail.task_id);
        if (mounted.current && contextRef.current === context) callbacks.current.onFinished(status);
      })}>已核验，返回盘点任务</Button>}
    </div>
    {download && sameActor && download.job_id === record.value.job_id && Date.parse(download.download.expires_at) > Date.now()
      && <p><a href={download.download.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">下载错误报告</a>（短时有效）</p>}
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    </div>
  </section>;
}
