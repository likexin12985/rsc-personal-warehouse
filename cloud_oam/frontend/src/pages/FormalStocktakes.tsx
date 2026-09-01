import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, ClipboardCheck, Plus, RefreshCw } from "lucide-react";
import type {
  FormalStocktakeAccess,
  FormalStocktakeAdapter,
  StocktakeAssigneeOption,
  StocktakeLocationOption,
  StocktakeRegionOption,
} from "../formalStocktakeAdapter";
import {
  createFormalStocktakeIntentRegistry,
  fixedQuantityText,
  formalStocktakeLabels,
  type FormalStocktakeCommandInput,
  type FormalStocktakeDetail,
  type FormalStocktakeDifference,
  type FormalStocktakeIntent,
  type FormalStocktakeRound,
  type FormalStocktakeScope,
  type StocktakeAccountCountInput,
  type StocktakeObservationInput,
} from "../formalStocktakes";
import {
  formalStocktakeRetryState,
  isFormalStocktakeWriteUncertain,
} from "../formalStocktakeAdapter";
import FormalFileUploadField, {
  type AvailableFormalFile,
  type FormalFileUploadClient,
  defaultFormalFileUploadClient,
} from "../FormalFileUploadField";
import { Button, Empty, Field, Loading, SectionHeader, showError } from "../ui";

type Props = Readonly<{
  adapter: FormalStocktakeAdapter;
  fileUploadClient?: FormalFileUploadClient;
}>;

const AXIS_LABELS = {
  count_status: "计数",
  difference_status: "差异",
  region_review_status: "区域复核",
  headquarters_review_status: "总部复核",
  recount_status: "复盘",
  posting_status: "过账",
  reconciliation_status: "内部对账",
  closure_status: "关闭",
} as const;

const VALUE_LABELS: Record<string, string> = {
  not_started: "未开始", counting: "进行中", submitted: "已提交",
  not_ready: "未就绪", not_evaluated: "待生成", evaluated: "已生成",
  hidden_for_blind_counter: "盲盘隐藏", pending: "待处理", approve: "已通过",
  recount: "要求复盘", reject: "已驳回", not_required: "无需复盘", required: "待开复盘",
  not_posted: "未过账", recorded: "已记录", not_reconciled: "未对账", stale: "对账已过期",
  open: "未关闭", closed: "已关闭",
};

function currentRound(detail: FormalStocktakeDetail): FormalStocktakeRound | null {
  return detail.rounds.find((round) => round.round_no === detail.current_round_no) ?? null;
}

function parseUuidList(value: string): string[] {
  const rows = value.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
  if (new Set(rows.map((item) => item.toLowerCase())).size !== rows.length) throw new Error("file_id 或 SN 标识不能重复");
  return rows;
}

function StatusAxes({ detail }: { detail: FormalStocktakeDetail }) {
  return <div className="formal-stocktake-axes" aria-label="盘点独立状态轴">
    {Object.entries(AXIS_LABELS).map(([field, label]) => <div key={field}>
      <span>{label}</span>
      <strong>{VALUE_LABELS[String(detail.state_axes[field as keyof typeof detail.state_axes])] || String(detail.state_axes[field as keyof typeof detail.state_axes])}</strong>
    </div>)}
  </div>;
}

function ReviewForm({
  stage,
  round,
  detail,
  disabled,
  onSubmit,
}: {
  stage: "region" | "headquarters";
  round: FormalStocktakeRound;
  detail: FormalStocktakeDetail;
  disabled: boolean;
  onSubmit: (input: FormalStocktakeCommandInput) => void;
}) {
  const [decision, setDecision] = useState<"approve" | "recount" | "reject">("approve");
  const [comment, setComment] = useState("");
  const [items, setItems] = useState<Record<string, { decision: "accept_for_posting" | "pending_verification" | "no_adjustment" | "recount" | "reject"; comment: string }>>(() => Object.fromEntries(round.visible_differences.map((difference) => [difference.difference_id, { decision: difference.posting_blocked_by_pending_verification ? "pending_verification" : "accept_for_posting", comment: "" }])));
  const action = stage === "region" ? "review_region" : "review_headquarters";
  return <section className="formal-stocktake-command-card" aria-label={stage === "region" ? "区域复核表单" : "总部复核表单"}>
    <h3>{stage === "region" ? "区域复核" : "蔚来总部复核"}</h3>
    <p>逐项决定不会与另一复核阶段合并；待核验观察不得标记为可过账。</p>
    {round.visible_differences.map((difference) => <div className="formal-stocktake-review-item" key={difference.difference_id}>
      <div><strong>差异 {difference.difference_no}</strong><span>{formalStocktakeLabels.difference[difference.difference_type]} · {difference.difference_qty}</span></div>
      <select aria-label={`差异 ${difference.difference_no} 决定`} value={items[difference.difference_id]?.decision} disabled={disabled} onChange={(event) => setItems((current) => ({ ...current, [difference.difference_id]: { ...current[difference.difference_id], decision: event.target.value as typeof items[string]["decision"] } }))}>
        <option value="accept_for_posting">接受用于过账</option><option value="pending_verification">等待核验</option><option value="no_adjustment">不调整</option><option value="recount">复盘</option><option value="reject">驳回</option>
      </select>
      <input aria-label={`差异 ${difference.difference_no} 意见`} value={items[difference.difference_id]?.comment || ""} disabled={disabled} onChange={(event) => setItems((current) => ({ ...current, [difference.difference_id]: { ...current[difference.difference_id], comment: event.target.value.trimStart() } }))} placeholder="逐项意见（可选）" />
    </div>)}
    <div className="form-grid two">
      <Field label="阶段结论"><select value={decision} disabled={disabled} onChange={(event) => setDecision(event.target.value as typeof decision)}><option value="approve">通过</option><option value="recount">要求复盘</option><option value="reject">驳回</option></select></Field>
      <Field label="阶段意见" hint="要求复盘或驳回时必填"><input value={comment} disabled={disabled} onChange={(event) => setComment(event.target.value.trimStart())} /></Field>
    </div>
    <Button disabled={disabled || (decision !== "approve" && !comment.trim())} onClick={() => onSubmit({ action, taskId: detail.task_id, roundId: round.round_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version, decision, items: round.visible_differences.map((difference) => ({ difference_id: difference.difference_id, decision: items[difference.difference_id]?.decision || "pending_verification", comment: (items[difference.difference_id]?.comment || "").trim() })), comment: comment.trim() } })}>提交{stage === "region" ? "区域" : "总部"}复核</Button>
  </section>;
}

function CountForm({ detail, round, scope, action, disabled, fileUploadClient, uploadBindingKey, onCancel, onSubmit }: {
  detail: FormalStocktakeDetail;
  round: FormalStocktakeRound;
  scope: FormalStocktakeScope;
  action: "submit_initial_count" | "submit_recount_count";
  disabled: boolean;
  fileUploadClient: FormalFileUploadClient;
  uploadBindingKey: string;
  onCancel: () => void;
  onSubmit: (input: FormalStocktakeCommandInput, evidenceFiles: readonly AvailableFormalFile[]) => void;
}) {
  const [accountQty, setAccountQty] = useState<Record<string, string>>({});
  const [accountSerials, setAccountSerials] = useState<Record<string, string>>({});
  const [observations, setObservations] = useState<StocktakeObservationInput[]>([]);
  const [identifier, setIdentifier] = useState("");
  const [quantity, setQuantity] = useState("");
  const [condition, setCondition] = useState<StocktakeObservationInput["condition_code"]>("new");
  const [availability, setAvailability] = useState<StocktakeObservationInput["availability_bucket"]>("available");
  const [lotNo, setLotNo] = useState("");
  const [serialNo, setSerialNo] = useState("");
  const [remark, setRemark] = useState("");
  const [evidenceFiles, setEvidenceFiles] = useState<readonly AvailableFormalFile[]>([]);
  const [uploadBlocking, setUploadBlocking] = useState(false);
  const countMode = detail.blind_count ? "blind" : "open";

  function addObservation() {
    const next: StocktakeObservationInput = {
      material_id: null,
      material_identifier_raw: identifier.trim(),
      material_identifier_type: "sku_code",
      condition_code: condition,
      availability_bucket: availability,
      counted_qty: fixedQuantityText(quantity, true),
      lot_id: null,
      lot_no_raw: lotNo.trim() || null,
      serial_id: null,
      serial_no_raw: serialNo.trim() || null,
      serial_identifier_type: serialNo.trim() ? "serial_no" : null,
      count_method: "manual",
      reason_code: null,
      remark: remark.trim(),
    };
    if (!next.material_identifier_raw) throw new Error("请填写现场物料标识");
    setObservations((rows) => [...rows, next]);
    setIdentifier(""); setQuantity(""); setLotNo(""); setSerialNo(""); setRemark("");
  }

  function submit(zero: boolean) {
    if (uploadBlocking) throw new Error("盘点证据尚未完成 available 严格确认，已停止计数写入");
    const accountCounts: StocktakeAccountCountInput[] = zero ? [] : scope.snapshot_accounts.map((account) => {
      const counted = accountQty[account.stock_account_id];
      if (!counted) throw new Error(`请填写账户 ${account.stock_account_id} 的实盘数量`);
      return { stock_account_id: account.stock_account_id, counted_qty: fixedQuantityText(counted), count_method: "manual", serial_ids: parseUuidList(accountSerials[account.stock_account_id] || ""), book_qty_confirmation: countMode === "open" ? account.book_qty : null, reason_code: null, remark: "" };
    });
    onSubmit({ action, taskId: detail.task_id, roundId: round.round_id, scopeId: scope.scope_id, expectedTaskVersion: detail.version, body: { count_mode: countMode, account_counts: accountCounts, physical_observations: zero ? [] : observations, evidence_file_ids: evidenceFiles.map((file) => file.file_id), zero_confirmed: zero } }, evidenceFiles);
  }

  return <section className="formal-stocktake-command-card" aria-label="盘点计数表单">
    <div className="content-title"><div><h3>{action === "submit_initial_count" ? "提交初盘计数" : "提交复盘计数"}</h3><p>{countMode === "blind" ? "盲盘：账面数保持隐藏，只记录现场事实。" : "明盘：每个账面账户必须回传账面数确认与实盘数。"}</p></div><Button tone="quiet" onClick={onCancel}>取消</Button></div>
    {scope.snapshot_accounts.map((account) => <div className="formal-stocktake-account-row" key={account.stock_account_id}>
      <div><strong>{account.material_id}</strong><span>账面 {account.book_qty} · {account.condition_code}/{account.availability_bucket}</span></div>
      <input aria-label={`账户 ${account.stock_account_id} 实盘数量`} inputMode="decimal" value={accountQty[account.stock_account_id] || ""} onChange={(event) => setAccountQty((current) => ({ ...current, [account.stock_account_id]: event.target.value }))} placeholder="实盘数量" disabled={disabled} />
      <input aria-label={`账户 ${account.stock_account_id} SN 标识`} value={accountSerials[account.stock_account_id] || ""} onChange={(event) => setAccountSerials((current) => ({ ...current, [account.stock_account_id]: event.target.value }))} placeholder="正式 serial_id，逗号分隔" disabled={disabled} />
    </div>)}
    <h4>现场实物观察</h4>
    <div className="form-grid three">
      <Field label="物料标识"><input aria-label="现场物料标识" value={identifier} onChange={(event) => setIdentifier(event.target.value)} placeholder="SKU/二维码原文" disabled={disabled} /></Field>
      <Field label="实盘数量"><input aria-label="现场实盘数量" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} disabled={disabled} /></Field>
      <Field label="成色"><select value={condition} onChange={(event) => setCondition(event.target.value as typeof condition)} disabled={disabled}><option value="new">新件</option><option value="used">旧件</option><option value="damaged">损坏</option><option value="scrapped">报废</option></select></Field>
      <Field label="库存状态"><select value={availability} onChange={(event) => setAvailability(event.target.value as typeof availability)} disabled={disabled}><option value="available">可用</option><option value="reserved">已占用</option><option value="picking">拣货中</option><option value="outbound">已出库</option><option value="in_transit">在途</option><option value="arrived_pending">到达待入库</option><option value="frozen">冻结</option><option value="return_pending">退回待处理</option><option value="scrap_pending">报废待处理</option></select></Field>
      <Field label="批次号（可选）"><input value={lotNo} onChange={(event) => setLotNo(event.target.value)} disabled={disabled} /></Field>
      <Field label="SN（可选）"><input value={serialNo} onChange={(event) => setSerialNo(event.target.value)} disabled={disabled} /></Field>
      <Field label="备注"><input value={remark} onChange={(event) => setRemark(event.target.value)} disabled={disabled} /></Field>
    </div>
    <Button tone="secondary" disabled={disabled || !identifier.trim() || !quantity.trim()} onClick={() => { try { addObservation(); } catch (error) { window.alert(showError(error)); } }} icon={<Plus size={16} />}>加入观察行</Button>
    {observations.map((row, index) => <div className="formal-stocktake-observation-row" key={`${row.material_identifier_raw}-${index}`}><span>{row.material_identifier_raw}</span><strong>{row.counted_qty}</strong><Button tone="quiet" onClick={() => setObservations((current) => current.filter((_, rowIndex) => rowIndex !== index))}>移除</Button></div>)}
    <FormalFileUploadField
      purpose="stocktake_evidence"
      bindingKey={uploadBindingKey}
      label="选择并上传盘点证据"
      client={fileUploadClient}
      multiple
      disabled={disabled}
      onAvailableChange={setEvidenceFiles}
      onBlockingChange={setUploadBlocking}
    />
    <div className="form-actions">
      <Button disabled={disabled || uploadBlocking || (!scope.snapshot_accounts.length && !observations.length)} onClick={() => { try { submit(false); } catch (error) { window.alert(showError(error)); } }}>提交并封存范围</Button>
      <Button tone="secondary" disabled={disabled || uploadBlocking || !!observations.length || Object.values(accountQty).some(Boolean)} onClick={() => { try { submit(true); } catch (error) { window.alert(showError(error)); } }}>确认本范围零库存</Button>
    </div>
  </section>;
}

export default function FormalStocktakesPage({ adapter, fileUploadClient = defaultFormalFileUploadClient }: Props) {
  const registry = useRef(createFormalStocktakeIntentRegistry());
  const countEvidenceClaims = useRef(new Map<string, string>());
  const uploadIdentity = useRef("");
  const [access, setAccess] = useState<FormalStocktakeAccess | null>(null);
  const [items, setItems] = useState<Awaited<ReturnType<FormalStocktakeAdapter["list"]>>["items"]>([]);
  const [detail, setDetail] = useState<FormalStocktakeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingMessage, setPendingMessage] = useState("");
  const [pendingRetryable, setPendingRetryable] = useState(false);
  const [countTarget, setCountTarget] = useState<{ scope: FormalStocktakeScope; round: FormalStocktakeRound; action: "submit_initial_count" | "submit_recount_count" } | null>(null);
  const [personalBlind, setPersonalBlind] = useState(true);
  const [personalFreeze, setPersonalFreeze] = useState<"hard" | "cutoff_replay">("cutoff_replay");
  const [personalNote, setPersonalNote] = useState("");
  const [regions, setRegions] = useState<readonly StocktakeRegionOption[]>([]);
  const [locations, setLocations] = useState<readonly StocktakeLocationOption[]>([]);
  const [assignees, setAssignees] = useState<readonly StocktakeAssigneeOption[]>([]);
  const [regionId, setRegionId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [assigneeId, setAssigneeId] = useState("");
  const [managedType, setManagedType] = useState<"full" | "sample" | "ad_hoc" | "termination">("ad_hoc");
  const [managedBlind, setManagedBlind] = useState(true);
  const [managedNote, setManagedNote] = useState("");
  const [managedScopes, setManagedScopes] = useState<{ location: StocktakeLocationOption; assignee: StocktakeAssigneeOption }[]>([]);
  const [recountScopes, setRecountScopes] = useState<string[]>([]);
  const [recountAssigneeOptions, setRecountAssigneeOptions] = useState<Record<string, readonly StocktakeAssigneeOption[]>>({});
  const [recountAssigneeUserIds, setRecountAssigneeUserIds] = useState<Record<string, string>>({});
  const [recountLoadingScopeId, setRecountLoadingScopeId] = useState("");
  const [recountReason, setRecountReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const nextAccess = await adapter.loadAccess();
      if (!nextAccess.can_read) throw new Error("当前授权不包含正式盘点只读权限");
      const nextUploadIdentity = `${nextAccess.person_id}:${nextAccess.authorization_version}`;
      if (uploadIdentity.current && uploadIdentity.current !== nextUploadIdentity) {
        countEvidenceClaims.current.clear();
        setCountTarget(null);
      }
      uploadIdentity.current = nextUploadIdentity;
      const page = await adapter.list(null);
      setAccess(nextAccess); setItems(page.items);
      if (detail && page.items.some((item) => item.task_id === detail.task_id)) setDetail(await adapter.detail(detail.task_id));
      if (nextAccess.can_manage) setRegions(await loadAllRegions());
    } catch (loadError) {
      countEvidenceClaims.current.clear(); uploadIdentity.current = ""; setCountTarget(null);
      setAccess(null); setItems([]); setDetail(null); setError(showError(loadError));
    } finally { setLoading(false); }
  }, [adapter, detail?.task_id]);

  useEffect(() => {
    void load();
    return () => { countEvidenceClaims.current.clear(); uploadIdentity.current = ""; };
  }, [adapter]);

  async function open(taskId: string) {
    setError(""); setBusy(true); setCountTarget(null);
    if (detail?.task_id !== taskId) countEvidenceClaims.current.clear();
    setRecountScopes([]); setRecountAssigneeOptions({}); setRecountAssigneeUserIds({}); setRecountReason("");
    try { setDetail(await adapter.detail(taskId)); } catch (openError) { setDetail(null); setError(showError(openError)); } finally { setBusy(false); }
  }

  async function run(input: FormalStocktakeCommandInput) {
    setBusy(true); setError(""); setPendingMessage(""); setPendingRetryable(false);
    let intent: FormalStocktakeIntent | null = null;
    try {
      intent = registry.current.current() ?? registry.current.begin(input);
      const completed = await adapter.execute(intent);
      registry.current.complete(intent);
      setDetail(completed.detail); setCountTarget(null); setPendingMessage(""); setPendingRetryable(false);
      const page = await adapter.list(null); setItems(page.items);
    } catch (writeError) {
      if (intent && !isFormalStocktakeWriteUncertain(writeError)) registry.current.complete(intent);
      const retry = formalStocktakeRetryState(writeError);
      setPendingRetryable(isFormalStocktakeWriteUncertain(writeError) && retry === "retryable");
      setPendingMessage(isFormalStocktakeWriteUncertain(writeError)
        ? retry === "retryable"
          ? "上一笔写请求结果不确定；当前对象仍处于原动作可重放状态。再次确认只复用原 Idempotency-Key、X-Request-ID、路径和正文。"
          : "上一笔写请求结果不确定，精确回读不能确认原请求成功，也不能证明原前置状态仍成立。已停止新写，请按原坐标人工核验。"
        : "");
      setError(showError(writeError));
    } finally { setBusy(false); }
  }

  async function retryPending() {
    const intent = registry.current.current();
    if (!intent) return;
    setBusy(true); setError("");
    try {
      const completed = await adapter.execute(intent);
      registry.current.complete(intent); setDetail(completed.detail); setPendingMessage(""); setPendingRetryable(false); setItems((await adapter.list(null)).items);
    } catch (writeError) { const retryable = formalStocktakeRetryState(writeError) === "retryable"; setPendingRetryable(retryable); setError(showError(writeError)); setPendingMessage(retryable ? "原写意图仍可使用同一坐标重试。" : "精确回读未确认结果，已停止其他写动作。请人工核验原坐标。"); } finally { setBusy(false); }
  }

  function submitCountWithEvidence(
    input: FormalStocktakeCommandInput,
    evidenceFiles: readonly AvailableFormalFile[],
  ): void {
    if (input.action !== "submit_initial_count" && input.action !== "submit_recount_count") {
      throw new Error("盘点证据只能绑定初盘或复盘计数意图");
    }
    const signature = JSON.stringify({
      action: input.action,
      task_id: input.taskId,
      round_id: input.roundId,
      scope_id: input.scopeId,
      expected_task_version: input.expectedTaskVersion,
      body: input.body,
    });
    for (const file of evidenceFiles) {
      if (file.purpose !== "stocktake_evidence" || file.status !== "available") {
        throw new Error("盘点证据未完成正式 available 确认");
      }
      for (const key of [`sha256:${file.sha256}`, `file_id:${file.file_id}`]) {
        const claimed = countEvidenceClaims.current.get(key);
        if (claimed && claimed !== signature) {
          throw new Error("同一盘点证据只能用于一个范围计数写意图");
        }
      }
    }
    for (const file of evidenceFiles) {
      countEvidenceClaims.current.set(`sha256:${file.sha256}`, signature);
      countEvidenceClaims.current.set(`file_id:${file.file_id}`, signature);
    }
    void run(input);
  }

  const round = detail ? currentRound(detail) : null;
  const can = useMemo(() => ({
    start: !!access?.can_count && !!detail?.allowed_actions.includes("start"),
    initialDiff: !!access?.can_manage && !!round?.allowed_actions.includes("generate_initial_differences"),
    recountDiff: !!access?.can_manage && !!round?.allowed_actions.includes("generate_recount_differences"),
    region: !!access?.can_review_region && !!round?.allowed_actions.includes("review_region"),
    headquarters: !!access?.can_review_headquarters && !!round?.allowed_actions.includes("review_headquarters"),
    recount: !!access?.can_manage && !!round?.allowed_actions.includes("open_recount"),
    post: !!access?.can_post && !!detail?.allowed_actions.includes("post"),
    reconcile: !!access?.can_reconcile && !!detail?.allowed_actions.includes("reconcile"),
    close: !!access?.can_close && !!detail?.allowed_actions.includes("close"),
  }), [access, detail, round]);

  function confirmPostDifferences() {
    if (!detail || !can.post) return;
    if (!window.confirm("确认将已完成总部复核的盘点差异独立过账？本动作会生成不可变过账完成事实及必要库存流水；过账不会关闭任务，也不会发送通知或同步 OAM。")) return;
    void run({ action: "post", taskId: detail.task_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version } });
  }

  function confirmReconciliation() {
    if (!detail || !can.reconcile) return;
    if (!window.confirm("确认执行独立内部对账？本动作只封存当前账、物、不可变流水与 SN 对账证明，任务仍保持已过账；不会关闭任务、发送通知或同步 OAM。")) return;
    void run({ action: "reconcile", taskId: detail.task_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version } });
  }

  function confirmClose() {
    if (!detail || !can.close) return;
    if (!window.confirm("确认依据当前仍有效的内部对账证明关闭盘点？关闭是独立动作；若库存总账已变化将失败并要求重新对账，且本动作不会发送通知或同步 OAM。")) return;
    void run({ action: "close", taskId: detail.task_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version } });
  }

  async function selectRegion(next: string) {
    setRegionId(next); setLocationId(""); setAssigneeId(""); setLocations([]); setAssignees([]);
    if (next) setLocations(await loadAllLocations(next));
  }
  async function selectLocation(next: string) {
    setLocationId(next); setAssigneeId(""); setAssignees([]);
    if (regionId && next) setAssignees(await loadAllAssignees(regionId, next));
  }
  async function loadAllAssignees(region: string, location: string): Promise<readonly StocktakeAssigneeOption[]> {
    const rows: StocktakeAssigneeOption[] = [];
    const seen = new Set<string>();
    let cursor: string | null = null;
    for (let pageNo = 0; pageNo < 100; pageNo += 1) {
      const page = await adapter.listAssignees(region, location, cursor);
      if (page.items.some((item) => rows.some((known) => known.person_id === item.person_id || known.assignee_user_id === item.assignee_user_id))) throw new Error("盘点人员目录跨页重复，已失败关闭");
      rows.push(...page.items);
      if (!page.next_after_person_id) return Object.freeze(rows);
      if (seen.has(page.next_after_person_id)) throw new Error("盘点人员目录分页游标重复，已失败关闭");
      seen.add(page.next_after_person_id); cursor = page.next_after_person_id;
    }
    throw new Error("盘点人员目录分页超出安全上限，已失败关闭");
  }
  async function loadAllRegions(): Promise<readonly StocktakeRegionOption[]> {
    const rows: StocktakeRegionOption[] = [];
    const seen = new Set<string>();
    let cursor: string | null = null;
    for (let pageNo = 0; pageNo < 100; pageNo += 1) {
      const page = await adapter.listRegions(cursor);
      if (page.items.some((item) => rows.some((known) => known.region_org_id === item.region_org_id))) throw new Error("盘点区域目录跨页重复，已失败关闭");
      rows.push(...page.items);
      if (!page.next_after_id) return Object.freeze(rows);
      if (seen.has(page.next_after_id)) throw new Error("盘点区域目录分页游标重复，已失败关闭");
      seen.add(page.next_after_id); cursor = page.next_after_id;
    }
    throw new Error("盘点区域目录分页超出安全上限，已失败关闭");
  }
  async function loadAllLocations(region: string): Promise<readonly StocktakeLocationOption[]> {
    const rows: StocktakeLocationOption[] = [];
    const seen = new Set<string>();
    let cursor: string | null = null;
    for (let pageNo = 0; pageNo < 100; pageNo += 1) {
      const page = await adapter.listLocations(region, cursor);
      if (page.items.some((item) => rows.some((known) => known.location_id === item.location_id))) throw new Error("盘点库位目录跨页重复，已失败关闭");
      rows.push(...page.items);
      if (!page.next_after_id) return Object.freeze(rows);
      if (seen.has(page.next_after_id)) throw new Error("盘点库位目录分页游标重复，已失败关闭");
      seen.add(page.next_after_id); cursor = page.next_after_id;
    }
    throw new Error("盘点库位目录分页超出安全上限，已失败关闭");
  }
  function addManagedScope() {
    const location = locations.find((item) => item.location_id === locationId);
    const assignee = assignees.find((item) => item.person_id === assigneeId);
    if (!location || !assignee) return;
    if (managedScopes.some((item) => item.location.location_id === location.location_id)) { setError("同一整库范围不能重复添加"); return; }
    setManagedScopes((current) => [...current, { location, assignee }]);
  }
  async function toggleRecountScope(scope: FormalStocktakeScope, checked: boolean) {
    if (!checked) {
      setRecountScopes((current) => current.filter((id) => id !== scope.scope_id));
      setRecountAssigneeUserIds((current) => { const next = { ...current }; delete next[scope.scope_id]; return next; });
      return;
    }
    if (!detail) return;
    setRecountLoadingScopeId(scope.scope_id); setError("");
    try {
      const options = await loadAllAssignees(detail.region_org_id, scope.location_id);
      if (!options.length) throw new Error("该范围没有正式可分配盘点人员");
      setRecountAssigneeOptions((current) => ({ ...current, [scope.scope_id]: options }));
      setRecountAssigneeUserIds((current) => ({ ...current, [scope.scope_id]: options[0].assignee_user_id }));
      setRecountScopes((current) => current.includes(scope.scope_id) ? current : [...current, scope.scope_id]);
    } catch (loadError) {
      setError(showError(loadError));
    } finally { setRecountLoadingScopeId(""); }
  }
  function submitRecount() {
    if (!detail || !round || !recountReason.trim() || !recountScopes.length) return;
    try {
      const assignments = recountScopes.map((scopeId) => {
        const userId = recountAssigneeUserIds[scopeId];
        const selected = recountAssigneeOptions[scopeId]?.find((option) => option.assignee_user_id === userId);
        if (!selected) throw new Error("复盘人员选择已失效，请重新选择");
        return { scope_id: scopeId, assignee_user_id: selected.assignee_user_id };
      });
      void run({ action: "open_recount", taskId: detail.task_id, roundId: round.round_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version, assignments, reason: recountReason.trim() } });
    } catch (selectionError) { setError(showError(selectionError)); }
  }

  if (loading) return <Loading label="正在读取正式非期初盘点" />;
  return <section className="formal-stocktakes-page">
    <SectionHeader title="日常盘点" subtitle="正式非期初盘点；初盘、差异、两级复核、复盘、过账和关闭保持独立。" actions={<Button tone="secondary" icon={<RefreshCw size={16} />} onClick={() => void load()}>刷新</Button>} />
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {pendingMessage && <div className="alert alert-warning" role="alert">{pendingMessage}{pendingRetryable && registry.current.current() && <Button tone="secondary" disabled={busy} onClick={() => void retryPending()}>按原坐标重试</Button>}</div>}
    <div className="alert alert-info">本页只访问正式 `/v1/stocktakes`、受控 `/v1/stocktake-options` 和 `/access/context`。不会调用 legacy `/stocktakes`、`/media`、opening 路由或外部系统。差异过账是独立总部动作；`posted` 不等于 `closed`。</div>

    {access?.can_count && <section className="content-section formal-stocktake-create">
      <div className="content-title"><div><h2>个人自盘</h2><p>人员、区域和个人库位均由服务端身份派生。</p></div></div>
      <div className="form-grid three">
        <Field label="盘点方式"><select value={personalBlind ? "blind" : "open"} onChange={(event) => setPersonalBlind(event.target.value === "blind")} disabled={busy}><option value="blind">盲盘</option><option value="open">明盘</option></select></Field>
        <Field label="冻结方式"><select value={personalFreeze} onChange={(event) => setPersonalFreeze(event.target.value as typeof personalFreeze)} disabled={busy}><option value="cutoff_replay">截止游标回放</option><option value="hard">硬冻结</option></select></Field>
        <Field label="备注"><input value={personalNote} onChange={(event) => setPersonalNote(event.target.value)} disabled={busy} /></Field>
      </div>
      <Button disabled={busy || !!registry.current.current()} icon={<Plus size={16} />} onClick={() => void run({ action: "create_personal", body: { blind_count: personalBlind, freeze_mode: personalFreeze, note: personalNote.trim() } })}>创建个人自盘草稿</Button>
    </section>}

    {access?.can_manage && <section className="content-section formal-stocktake-create">
      <div className="content-title"><div><h2>托管盘点创建</h2><p>区域、库位和人员只来自服务端受控目录；不接受手填 UUID。</p></div></div>
      <div className="form-grid three">
        <Field label="区域"><select aria-label="托管盘点区域" value={regionId} onChange={(event) => void selectRegion(event.target.value)} disabled={busy}><option value="">请选择</option>{regions.map((item) => <option key={item.region_org_id} value={item.region_org_id}>{item.code} · {item.name}</option>)}</select></Field>
        <Field label="库位"><select aria-label="托管盘点库位" value={locationId} onChange={(event) => void selectLocation(event.target.value)} disabled={busy || !regionId}><option value="">请选择</option>{locations.map((item) => <option key={item.location_id} value={item.location_id}>{item.code} · {item.name}</option>)}</select></Field>
        <Field label="盘点人员"><select aria-label="托管盘点人员" value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} disabled={busy || !locationId}><option value="">请选择</option>{assignees.map((item) => <option key={item.person_id} value={item.person_id}>{item.employee_no} · {item.name}</option>)}</select></Field>
      </div>
      <Button tone="secondary" disabled={busy || !regionId || !locationId || !assigneeId} onClick={addManagedScope}>加入整库范围</Button>
      {managedScopes.map((item) => <div className="formal-stocktake-observation-row" key={item.location.location_id}><span>{item.location.code} · {item.location.name} → {item.assignee.name}</span><Button tone="quiet" onClick={() => setManagedScopes((rows) => rows.filter((row) => row.location.location_id !== item.location.location_id))}>移除</Button></div>)}
      <div className="form-grid three">
        <Field label="任务类型"><select value={managedType} onChange={(event) => setManagedType(event.target.value as typeof managedType)}><option value="ad_hoc">临时盘点</option><option value="sample">抽盘（整库范围）</option><option value="full">全盘</option><option value="termination">离职盘点</option></select></Field>
        <Field label="盘点方式"><select value={managedBlind ? "blind" : "open"} onChange={(event) => setManagedBlind(event.target.value === "blind")}><option value="blind">盲盘</option><option value="open">明盘</option></select></Field>
        <Field label="备注"><input value={managedNote} onChange={(event) => setManagedNote(event.target.value)} /></Field>
      </div>
      <Button disabled={busy || !regionId || !managedScopes.length || !!registry.current.current()} onClick={() => void run({ action: "create_managed", body: { task_type: managedType, region_org_id: regionId, blind_count: managedBlind, scopes: managedScopes.map(({ location, assignee }) => ({ owner_org_id: location.owner_org_id, location_id: location.location_id, assignee_person_id: assignee.person_id, scope_mode: "location_all", material_id: null, condition_code: null, availability_bucket: null, freeze_mode: "hard" })), deadline: null, note: managedNote.trim() } })}>创建托管盘点草稿</Button>
    </section>}

    <section className="content-section">
      <div className="content-title"><div><h2>可见任务</h2><p>只显示服务端按当前授权裁剪后的正式任务。</p></div></div>
      {!items.length ? <Empty title="暂无可见正式日常盘点" /> : <div className="formal-stocktake-task-list">{items.map((item) => <article key={item.task_id} className={detail?.task_id === item.task_id ? "selected" : ""}>
        <div><strong>{item.task_no}</strong><span>{formalStocktakeLabels.taskType[item.task_type]} · {formalStocktakeLabels.status[item.status]}</span></div>
        <div><span>{item.current_round_visible_completed_scope_count}/{item.visible_scope_count} 范围</span><span>v{item.version}</span></div>
        <Button tone="secondary" disabled={busy} onClick={() => void open(item.task_id)}>查看</Button>
      </article>)}</div>}
    </section>

    {detail && <section className="content-section formal-stocktake-detail" aria-label="日常盘点详情">
      <div className="content-title"><div><h2>{detail.task_no}</h2><p>{formalStocktakeLabels.taskType[detail.task_type]} · {detail.blind_count ? "盲盘" : "明盘"} · v{detail.version}</p></div><span className="status status-info">{formalStocktakeLabels.status[detail.status]}</span></div>
      <StatusAxes detail={detail} />
      <div className="alert alert-info">过账轴来自不可变过账完成事实和库存流水；关闭轴由独立关闭完成事实、closed 状态与时间共同确认。即使任务状态为 posted，也不能显示为已关闭。</div>
      {can.start && <div className="formal-stocktake-actions"><Button disabled={busy || !!registry.current.current()} icon={<CheckCircle2 size={16} />} onClick={() => void run({ action: "start", taskId: detail.task_id, expectedTaskVersion: detail.version, body: { expected_version: detail.version } })}>启动个人自盘</Button></div>}
      {can.post && <div className="formal-stocktake-actions"><Button disabled={busy || !!registry.current.current()} icon={<CheckCircle2 size={16} />} onClick={confirmPostDifferences}>确认差异过账</Button></div>}
      {can.reconcile && <div className="formal-stocktake-actions"><Button disabled={busy || !!registry.current.current()} icon={<ClipboardCheck size={16} />} onClick={confirmReconciliation}>执行独立内部对账</Button></div>}
      {can.close && <div className="formal-stocktake-actions"><Button disabled={busy || !!registry.current.current()} icon={<CheckCircle2 size={16} />} onClick={confirmClose}>依据当前对账证明关闭</Button></div>}
      <section className="opening-detail-section"><header><div><h3>盘点范围</h3><p>计数按钮同时要求 access permission 与范围 allowed_actions。</p></div></header>
        <div className="formal-stocktake-scope-grid">{detail.scopes.map((scope) => <article key={scope.scope_id}>
          <div><strong>范围 {scope.scope_no}</strong><span>{scope.assigned_to_me ? "分配给我" : "只读"}</span></div>
          <small>库位 {scope.location_id}</small><small>账面可见性：{scope.snapshot_visibility}</small>
          {round && scope.allowed_actions.includes("submit_initial_count") && access?.can_count && <Button disabled={busy || !!registry.current.current()} onClick={() => setCountTarget({ scope, round, action: "submit_initial_count" })}>录入初盘</Button>}
          {round && scope.allowed_actions.includes("submit_recount_count") && access?.can_count && <Button disabled={busy || !!registry.current.current()} onClick={() => setCountTarget({ scope, round, action: "submit_recount_count" })}>录入复盘</Button>}
        </article>)}</div>
      </section>
      {countTarget && <CountForm
        detail={detail}
        round={countTarget.round}
        scope={countTarget.scope}
        action={countTarget.action}
        disabled={busy || !!registry.current.current()}
        fileUploadClient={fileUploadClient}
        uploadBindingKey={`${access?.person_id || "no-person"}:${access?.authorization_version || 0}:${detail.task_id}:${countTarget.round.round_id}:${countTarget.scope.scope_id}:${countTarget.action}:${detail.version}`}
        onCancel={() => setCountTarget(null)}
        onSubmit={submitCountWithEvidence}
      />}
      {round && <section className="opening-detail-section"><header><div><h3>当前轮次与差异</h3><p>差异生成、区域复核和总部复核分别提交。</p></div></header>
        <div className="formal-stocktake-actions">
          {can.initialDiff && <Button disabled={busy || !!registry.current.current()} onClick={() => void run({ action: "generate_initial_differences", taskId: detail.task_id, roundId: round.round_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version } })}>生成初盘差异</Button>}
          {can.recountDiff && <Button disabled={busy || !!registry.current.current()} onClick={() => void run({ action: "generate_recount_differences", taskId: detail.task_id, roundId: round.round_id, expectedTaskVersion: detail.version, body: { expected_task_version: detail.version } })}>生成复盘差异</Button>}
        </div>
        {round.visible_differences.map((difference: FormalStocktakeDifference) => <div className="formal-stocktake-difference" key={difference.difference_id}><div><strong>差异 {difference.difference_no} · {formalStocktakeLabels.difference[difference.difference_type]}</strong><span>{difference.reason_text || "未填写原因"}</span></div><div><span>账面 {difference.book_qty}</span><span>实盘 {difference.counted_qty}</span><strong>{difference.difference_qty}</strong></div></div>)}
      </section>}
      {round && can.region && <ReviewForm stage="region" round={round} detail={detail} disabled={busy || !!registry.current.current()} onSubmit={(input) => void run(input)} />}
      {round && can.headquarters && <ReviewForm stage="headquarters" round={round} detail={detail} disabled={busy || !!registry.current.current()} onSubmit={(input) => void run(input)} />}
      {round && can.recount && <section className="formal-stocktake-command-card" aria-label="选择范围开复盘">
        <h3>选择范围开复盘</h3><p>范围与人员均从当前授权下的正式目录选择；托管创建使用 person_id，复盘只使用同一选项返回的 assignee_user_id。</p>
        {detail.scopes.map((scope) => <div className="formal-stocktake-check" key={scope.scope_id}><label><input type="checkbox" checked={recountScopes.includes(scope.scope_id)} disabled={busy || recountLoadingScopeId === scope.scope_id} onChange={(event) => void toggleRecountScope(scope, event.target.checked)} />范围 {scope.scope_no} · {scope.location_id}</label>{recountScopes.includes(scope.scope_id) && <Field label={`范围 ${scope.scope_no} 复盘人员`}><select aria-label={`范围 ${scope.scope_no} 复盘人员`} value={recountAssigneeUserIds[scope.scope_id] || ""} onChange={(event) => setRecountAssigneeUserIds((current) => ({ ...current, [scope.scope_id]: event.target.value }))}>{(recountAssigneeOptions[scope.scope_id] || []).map((option) => <option key={option.assignee_user_id} value={option.assignee_user_id}>{option.employee_no} · {option.name}</option>)}</select></Field>}</div>)}
        <Field label="复盘原因"><textarea value={recountReason} onChange={(event) => setRecountReason(event.target.value)} /></Field>
        <Button disabled={busy || !!registry.current.current() || !!recountLoadingScopeId || !recountScopes.length || !recountReason.trim() || recountScopes.some((scopeId) => !recountAssigneeOptions[scopeId]?.some((option) => option.assignee_user_id === recountAssigneeUserIds[scopeId]))} onClick={submitRecount}>按所选范围开复盘</Button>
      </section>}
      {detail.status === "posted" && detail.state_axes.reconciliation_status === "not_reconciled" && <div className="alert alert-warning">该任务已过账但尚未完成独立内部对账；不能关闭。</div>}
      {detail.status === "posted" && detail.state_axes.reconciliation_status === "stale" && <div className="alert alert-warning">已有内部对账证明已因库存总账变化失效；必须重新对账，不能沿用旧证明关闭。</div>}
      {!detail.allowed_actions.length && !detail.scopes.some((scope) => scope.allowed_actions.length) && <div className="alert alert-info">当前详情没有服务端授权的写动作；保持只读。</div>}
    </section>}
  </section>;
}
