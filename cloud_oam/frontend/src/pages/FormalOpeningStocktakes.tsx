import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ClipboardCheck,
  Eye,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";

import {
  closeOpeningStocktake,
  loadFormalOpeningStocktakeDetail,
  loadFormalOpeningStocktakes,
  openOpeningRecount,
  openingReviewItemPolicy,
  postOpeningStocktake,
  recordOpeningObservationDisposition,
  submitOpeningHeadquartersReview,
  submitOpeningRegionReview,
  submitOpeningScopeCount,
  type OpeningAllowedAction,
  type OpeningObservationDisposition,
  type OpeningPhysicalObservationInput,
  type OpeningReviewItemInput,
  type OpeningReviewTopDecision,
  type OpeningStocktakeDifference,
  type OpeningStocktakeTaskDetail,
  type OpeningStocktakeTaskSummary,
} from "../formalOpeningStocktake";
import {
  Button,
  Empty,
  Field,
  Loading,
  Modal,
  SectionHeader,
  formatDate,
  showError,
} from "../ui";
import {
  openingRecountAssigneeContext, openingRecountAssigneeContextKey,
  type OpeningRecountActor, type OpeningRecountAssigneeContext, type OpeningRecountAssigneeSelection,
} from "../formalOpeningRecountAssignees";
import OpeningRecountAssigneePicker from "./OpeningRecountAssigneePicker";

const STATUS_LABELS: Record<string, string> = {
  counting: "计数中",
  submitted: "已提交",
  region_review: "区域复核",
  hq_review: "总部复核",
  approved: "已审批",
  recount_required: "待复盘",
  posted: "已过账",
  closed: "已关闭",
  approve: "通过",
  recount: "要求复盘",
  reject: "驳回",
};

const ACTION_LABELS: Record<OpeningAllowedAction, string> = {
  count: "提交计数",
  review_region: "区域复核",
  review_headquarters: "总部复核",
  open_recount: "发起复盘",
  post: "过账",
  close: "关闭",
};

const DISPOSITION_LABELS: Record<OpeningObservationDisposition, string> = {
  resolved_existing_master: "绑定正式主数据",
  pending_verification: "保留待核实",
  requires_recount: "要求复盘",
};

type DialogState =
  | Readonly<{ kind: "count"; scopeId: string }>
  | Readonly<{ kind: "review_region" | "review_headquarters" }>
  | Readonly<{ kind: "open_recount" }>
  | Readonly<{
    kind: "observation_disposition";
    observationId: string;
    disposition: OpeningObservationDisposition;
  }>;

type ObservationDraft = {
  key: number;
  materialIdentifier: string;
  identifierType: "sku_code" | "qr_code" | "external_code" | "unknown";
  conditionCode: "new" | "used" | "damaged" | "scrapped";
  availabilityBucket: OpeningPhysicalObservationInput["availability_bucket"];
  countedQty: string;
  lotNo: string;
  serialNo: string;
  remark: string;
};

function newObservation(key: number): ObservationDraft {
  return {
    key,
    materialIdentifier: "",
    identifierType: "sku_code",
    conditionCode: "new",
    availabilityBucket: "available",
    countedQty: "",
    lotNo: "",
    serialNo: "",
    remark: "",
  };
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status;
}

function actionLabel(action: OpeningAllowedAction): string {
  return ACTION_LABELS[action];
}

function evidenceLabel(detail: OpeningStocktakeTaskDetail): string {
  if (detail.evidence_status === "sealed") return "证据已封存";
  if (detail.evidence_status === "counting_hidden") return "盲盘计数中（数量隐藏）";
  return "尚未开始";
}

function compatibleReviewDecisions(
  detail: OpeningStocktakeTaskDetail,
  kind: "review_region" | "review_headquarters",
): OpeningReviewTopDecision[] {
  const candidates: OpeningReviewTopDecision[] = kind === "review_region"
    ? ["approve", "recount", "reject"]
    : ["approve", "reject"];
  return candidates.filter((decision) => {
    try {
      detail.differences.forEach((difference) => {
        openingReviewItemPolicy(detail, difference, decision);
      });
      return true;
    } catch {
      return false;
    }
  });
}

function Lifecycle({ detail }: { detail: OpeningStocktakeTaskDetail }) {
  const region = detail.reviews.find((row) => row.stage === "region");
  const headquarters = detail.reviews.find((row) => row.stage === "headquarters");
  const counted = detail.current_round?.status === "submitted"
    || detail.current_round?.status === "superseded";
  const posted = detail.status === "posted" || detail.status === "closed";
  const closed = detail.status === "closed";
  return <div className="opening-lifecycle" aria-label="盘点独立状态">
    <div><span>计数</span><strong>{counted ? "已封存" : "未封存"}</strong></div>
    <div><span>区域复核</span><strong>{region ? statusLabel(region.decision) : "未复核"}</strong></div>
    <div><span>总部复核</span><strong>{headquarters ? statusLabel(headquarters.decision) : "未复核"}</strong></div>
    <div><span>过账</span><strong>{posted ? "已过账" : "未过账"}</strong></div>
    <div><span>关闭</span><strong>{closed ? "已关闭" : "未关闭"}</strong></div>
  </div>;
}

function TaskTable({
  tasks,
  onOpen,
}: {
  tasks: OpeningStocktakeTaskSummary[];
  onOpen: (taskId: string) => void;
}) {
  return <div className="table-wrap"><table className="opening-task-table">
    <thead><tr>
      <th>任务</th><th>状态</th><th>轮次</th><th>范围进度</th><th>差异</th><th>期限</th><th>允许操作</th><th>详情</th>
    </tr></thead>
    <tbody>{tasks.map((task) => <tr key={task.task_id}>
      <td><strong>{task.task_no}</strong><span className="cell-subtitle mono">{task.task_id}</span></td>
      <td><span className={`status status-${task.status}`}>{statusLabel(task.status)}</span></td>
      <td>{task.current_round_no || "-"}<span className="cell-subtitle">{task.current_round_status ? statusLabel(task.current_round_status) : "未开始"}</span></td>
      <td>{task.completed_scope_count} / {task.visible_scope_count}</td>
      <td>{task.evidence_status === "sealed" ? task.difference_count : "封存前隐藏"}</td>
      <td>{formatDate(task.deadline)}</td>
      <td><div className="opening-action-tags">{task.allowed_actions.length
        ? task.allowed_actions.map((action) => <span key={action}>{actionLabel(action)}</span>)
        : <span>无</span>}</div></td>
      <td><button className="table-action" onClick={() => onOpen(task.task_id)}><Eye size={16} />查看</button></td>
    </tr>)}</tbody>
  </table></div>;
}

export default function FormalOpeningStocktakesPage({ actor }: { actor?: OpeningRecountActor } = {}) {
  const [tasks, setTasks] = useState<OpeningStocktakeTaskSummary[]>([]);
  const tasksRef = useRef<OpeningStocktakeTaskSummary[]>([]);
  const [nextAfterId, setNextAfterId] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<OpeningStocktakeTaskDetail | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [zeroConfirmed, setZeroConfirmed] = useState(false);
  const observationKey = useRef(1);
  const [observations, setObservations] = useState<ObservationDraft[]>([
    newObservation(1),
  ]);
  const [reviewDecision, setReviewDecision] = useState<OpeningReviewTopDecision | "">("");
  const [reviewComment, setReviewComment] = useState("");
  const [itemComments, setItemComments] = useState<Record<string, string>>({});
  const [recountSelections, setRecountSelections] = useState<Record<string, boolean>>({});
  const [recountAssignees, setRecountAssignees] = useState<Record<string, OpeningRecountAssigneeSelection>>({});
  const [recountReason, setRecountReason] = useState("");
  const [dispositionReason, setDispositionReason] = useState("");
  const [dispositionComment, setDispositionComment] = useState("");
  const [resolvedMaterialId, setResolvedMaterialId] = useState("");
  const [resolvedLotId, setResolvedLotId] = useState("");
  const [resolvedSerialId, setResolvedSerialId] = useState("");

  const loadList = useCallback(async (afterId: string | null = null) => {
    setListLoading(true);
    setError("");
    try {
      const page = await loadFormalOpeningStocktakes(afterId);
      const current = tasksRef.current;
      if (
        afterId !== null
        && page.items.some((row) => current.some((item) => item.task_id === row.task_id))
      ) throw new Error("正式期初盘点跨页返回重复任务，已停止推进游标");
      const merged = afterId === null ? page.items : [...current, ...page.items];
      tasksRef.current = merged;
      setTasks(merged);
      setNextAfterId(page.next_after_id);
    } catch (err) {
      setError(showError(err));
    } finally {
      setListLoading(false);
    }
  }, []);

  const openDetail = useCallback(async (taskId: string) => {
    setDetailLoading(true);
    setError("");
    setNotice("");
    try {
      setDetail(await loadFormalOpeningStocktakeDetail(taskId));
    } catch (err) {
      setDetail(null);
      setError(showError(err));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  const availableActions = useMemo(
    () => new Set(detail?.allowed_actions || []),
    [detail],
  );

  const recountContexts = useMemo(() => {
    const contexts: Record<string, OpeningRecountAssigneeContext | null> = {};
    if (!detail || !actor) return contexts;
    for (const scope of detail.scopes) {
      try { contexts[scope.scope_id] = openingRecountAssigneeContext(detail, scope.scope_id, actor); }
      catch { contexts[scope.scope_id] = null; }
    }
    return contexts;
  }, [detail, actor?.person_id, actor?.authorization_version]);

  function resetDialog(): void {
    setDialog(null);
    setZeroConfirmed(false);
    observationKey.current += 1;
    setObservations([newObservation(observationKey.current)]);
    setReviewDecision("");
    setReviewComment("");
    setItemComments({});
    setRecountSelections({});
    setRecountAssignees({});
    setRecountReason("");
    setDispositionReason("");
    setDispositionComment("");
    setResolvedMaterialId("");
    setResolvedLotId("");
    setResolvedSerialId("");
  }

  function openReview(kind: "review_region" | "review_headquarters"): void {
    if (!detail?.allowed_actions.includes(kind)) return;
    setReviewDecision("");
    setReviewComment("");
    setItemComments({});
    setDialog({ kind });
  }

  function openObservationDisposition(
    observationId: string,
    disposition: OpeningObservationDisposition,
  ): void {
    const observation = detail?.observations.find(
      (row) => row.observation_id === observationId,
    );
    if (!observation?.allowed_dispositions.includes(disposition)) return;
    setDispositionReason("");
    setDispositionComment("");
    setResolvedMaterialId("");
    setResolvedLotId("");
    setResolvedSerialId("");
    setDialog({ kind: "observation_disposition", observationId, disposition });
  }

  async function completeAction(
    work: () => Promise<{ detail: OpeningStocktakeTaskDetail }>,
    success: string,
  ): Promise<void> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await work();
      setDetail(result.detail);
      setNotice(success);
      resetDialog();
      await loadList();
    } catch (err) {
      setError(showError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitCount(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || dialog?.kind !== "count") return;
    await completeAction(() => submitOpeningScopeCount(
      detail.task_id,
      dialog.scopeId,
      {
        zero_confirmed: zeroConfirmed,
        physical_observations: zeroConfirmed ? [] : observations.map((row) => ({
          material_identifier_raw: row.materialIdentifier.trim(),
          material_identifier_type: row.identifierType,
          condition_code: row.conditionCode,
          availability_bucket: row.availabilityBucket,
          counted_qty: row.countedQty.trim(),
          ...(row.lotNo.trim() ? { lot_no_raw: row.lotNo.trim() } : {}),
          ...(row.serialNo.trim() ? {
            serial_no_raw: row.serialNo.trim(),
            serial_identifier_type: "serial_no" as const,
          } : {}),
          count_method: "manual" as const,
          remark: row.remark.trim(),
        })),
      },
    ), "该范围计数已提交；详情已从服务端重新读取。");
  }

  function reviewItems(): OpeningReviewItemInput[] {
    if (!detail || !reviewDecision) throw new Error("请先选择本级复核决定");
    return detail.differences.map((difference) => {
      const policy = openingReviewItemPolicy(detail, difference, reviewDecision);
      const comment = (itemComments[difference.difference_id] || "").trim();
      if (policy.comment_required && !comment) throw new Error(policy.reason);
      return {
        difference_id: difference.difference_id,
        decision: policy.decision,
        comment,
      };
    });
  }

  async function submitReview(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || (dialog?.kind !== "review_region" && dialog?.kind !== "review_headquarters")) return;
    if (!reviewDecision) {
      setError("请先选择本级复核决定");
      return;
    }
    const comment = reviewComment.trim();
    let items: OpeningReviewItemInput[];
    try {
      items = reviewItems();
    } catch (err) {
      setError(showError(err));
      return;
    }
    if (dialog.kind === "review_region") {
      await completeAction(() => submitOpeningRegionReview(detail.task_id, {
        decision: reviewDecision,
        items,
        comment,
      }), "区域复核已提交；总部复核仍为独立状态。");
      return;
    }
    if (reviewDecision === "recount") {
      setError("总部复核不接受要求复盘决定");
      return;
    }
    await completeAction(() => submitOpeningHeadquartersReview(detail.task_id, {
      decision: reviewDecision,
      items,
      comment,
    }), "总部复核已提交；尚未代表完成过账或关闭。");
  }

  async function submitObservationDisposition(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || dialog?.kind !== "observation_disposition") return;
    const resolved = dialog.disposition === "resolved_existing_master";
    await completeAction(() => recordOpeningObservationDisposition(
      detail.task_id,
      dialog.observationId,
      {
        disposition: dialog.disposition,
        reason_code: dispositionReason.trim(),
        comment: dispositionComment.trim(),
        resolved_material_id: resolved ? resolvedMaterialId.trim() : null,
        resolved_lot_id: resolved && resolvedLotId.trim() ? resolvedLotId.trim() : null,
        resolved_serial_id: resolved && resolvedSerialId.trim() ? resolvedSerialId.trim() : null,
      },
    ), "现场观察处置已写入；详情已强制重读确认，复核仍为独立操作。");
  }

  async function submitRecount(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || dialog?.kind !== "open_recount" || busy) return;
    try {
      if (!actor || !detail.current_round) throw new Error("缺少当前正式身份，请刷新后重新选择复盘人员");
      const selectedScopes = detail.scopes.filter((scope) => recountSelections[scope.scope_id]);
      if (!selectedScopes.length) throw new Error("请选择至少一个复盘范围及合格人员");
      const assignments = selectedScopes.map((scope) => {
        const context = openingRecountAssigneeContext(detail, scope.scope_id, actor);
        const selection = recountAssignees[scope.scope_id];
        if (!selection || openingRecountAssigneeContextKey(selection.context) !== openingRecountAssigneeContextKey(context)) {
          throw new Error("复盘人员选择已失效，请按当前任务和身份重新选择");
        }
        return { scope_id: scope.scope_id, assignee_user_id: selection.assignee.user_id };
      });
      const expected = { task_version: detail.task_version, source_round_id: detail.current_round.round_id };
      await completeAction(() => openOpeningRecount(detail.task_id, {
        assignments, reason: recountReason.trim(),
      }, expected), "复盘轮次已独立创建；原轮次证据保持不变。");
    } catch (err) {
      setError(showError(err));
    }
  }

  async function terminal(action: "post" | "close"): Promise<void> {
    if (!detail || !detail.allowed_actions.includes(action)) return;
    const confirmed = window.confirm(action === "post"
      ? "确认将已审批盘点独立过账？过账不会自动关闭任务。"
      : "确认关闭已过账盘点？关闭不会改写既有库存流水。");
    if (!confirmed) return;
    await completeAction(
      () => action === "post"
        ? postOpeningStocktake(detail.task_id)
        : closeOpeningStocktake(detail.task_id),
      action === "post"
        ? "盘点已过账；关闭状态仍需独立执行。"
        : "盘点已关闭；既有过账状态与流水保持独立可审计。",
    );
  }

  function updateObservation(key: number, patch: Partial<ObservationDraft>): void {
    setObservations((rows) => rows.map((row) => row.key === key ? { ...row, ...patch } : row));
  }

  return <>
    <SectionHeader
      title="盘点中心"
      subtitle="正式 V1.0 期初盘点；计数、观察处置、两级复核、复盘、过账与关闭保持独立"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} disabled={listLoading || busy} onClick={() => void loadList()}>刷新</Button>}
    />
    <div className="alert alert-info">
      <ClipboardCheck size={18} />
      <span>盲盘证据封存前，本页不展示或推算账面数、实盘数与差异数；任务按钮来自 allowed_actions，观察处置来自 allowed_dispositions。</span>
    </div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {notice && <div className="form-notice opening-notice">{notice}</div>}
    <section className="content-section table-section">
      <div className="content-title"><div><h2>正式期初盘点任务</h2><p>仅调用 /api/v1/stocktakes/opening 正式接口族</p></div></div>
      {listLoading && tasks.length === 0 ? <Loading label="正在读取正式盘点任务" />
        : tasks.length === 0 ? <Empty title="暂无可见正式盘点任务" detail="旧版 v0.9 盘点不会在此显示。" />
          : <TaskTable tasks={tasks} onOpen={(taskId) => void openDetail(taskId)} />}
      {nextAfterId && <div className="pagination"><span>还有更多正式任务</span><Button tone="secondary" disabled={listLoading} onClick={() => void loadList(nextAfterId)}>加载下一页</Button></div>}
    </section>

    {detailLoading && <Loading label="正在读取盘点详情" />}
    {detail && <section className="content-section opening-detail" aria-label="盘点详情">
      <div className="content-title"><div><h2>{detail.task_no}</h2><p className="mono">{detail.task_id}</p></div><span className={`status status-${detail.status}`}>{statusLabel(detail.status)}</span></div>
      <div className={`alert ${detail.evidence_status === "sealed" ? "alert-info" : "alert-warning"}`}>{evidenceLabel(detail)}</div>
      <Lifecycle detail={detail} />
      <dl className="detail-grid opening-detail-grid">
        <div><dt>当前轮次</dt><dd>{detail.current_round ? `第 ${detail.current_round.round_no} 轮 · ${statusLabel(detail.current_round.status)}` : "未开始"}</dd></div>
        <div><dt>任务版本</dt><dd>v{detail.task_version}</dd></div>
        <div><dt>截止时间</dt><dd>{formatDate(detail.deadline)}</dd></div>
      </dl>

      <div className="opening-detail-section">
        <header><div><h3>盘点范围</h3><p>完成状态可见；数量证据仅在封存后显示。</p></div></header>
        <div className="table-wrap"><table><thead><tr><th>范围</th><th>指派</th><th>完成状态</th><th>零库存</th><th>实盘行</th><th>SN</th><th>实盘数量</th><th>操作</th></tr></thead><tbody>
          {detail.scopes.map((scope) => <tr key={scope.scope_id}>
            <td>范围 {scope.scope_no}<span className="cell-subtitle mono">{scope.location_id}</span></td>
            <td>{scope.assigned_to_me ? "指派给我" : "其他人员"}</td>
            <td>{scope.completion_status === "completed" ? "已完成" : "待计数"}</td>
            {detail.evidence_status === "sealed" ? <>
              <td>{scope.zero_confirmed ? "是" : "否"}</td>
              <td>{scope.observation_line_count}</td>
              <td>{scope.serial_count}</td>
              <td>{scope.total_counted_qty}</td>
            </> : <td colSpan={4}>封存前隐藏</td>}
            <td>{availableActions.has("count") && scope.assigned_to_me && scope.completion_status === "pending"
              ? <Button tone="secondary" onClick={() => setDialog({ kind: "count", scopeId: scope.scope_id })}>提交计数</Button>
              : "-"}</td>
          </tr>)}
        </tbody></table></div>
      </div>

      <div className="opening-detail-section">
        <header><div><h3>差异证据</h3><p>账面数、实盘数与差异数在封存前全部隐藏。</p></div></header>
        {detail.evidence_status !== "sealed" ? <Empty title="差异证据尚未封存" detail="本页不会读取、展示或计算差异。" />
          : detail.differences.length === 0 ? <Empty title="当前封存差异集为空" />
            : <div className="table-wrap"><table><thead><tr><th>差异</th><th>类型</th><th>账面数</th><th>实盘数</th><th>差异数</th><th>影响数</th></tr></thead><tbody>
              {detail.differences.map((row) => <tr key={row.difference_id}><td>#{row.difference_no}</td><td>{row.difference_type}</td><td>{row.book_qty}</td><td>{row.counted_qty}</td><td>{row.difference_qty}</td><td>{row.affected_qty}</td></tr>)}
            </tbody></table></div>}
      </div>

      <div className="opening-detail-section">
        <header><div><h3>现场观察与处置</h3><p>只展示已封存观察；处置按钮完全来自每条观察的 allowed_dispositions。</p></div></header>
        {detail.evidence_status !== "sealed"
          ? <Empty title="现场观察证据尚未封存" detail="本页不会读取或展示现场标识、数量及处置。" />
          : detail.observations.length === 0
            ? <Empty title="当前封存轮次没有现场观察" />
            : <div className="table-wrap"><table><thead><tr><th>观察</th><th>现场标识</th><th>范围</th><th>实盘数量</th><th>核实状态</th><th>已有处置</th><th>允许处置</th></tr></thead><tbody>
              {detail.observations.map((observation) => <tr key={observation.observation_id}>
                <td>#{observation.observation_no}<span className="cell-subtitle mono">{observation.observation_id}</span></td>
                <td>{observation.material_identifier_raw}<span className="cell-subtitle">{observation.material_identifier_type}</span></td>
                <td className="mono">{observation.scope_id}</td>
                <td>{observation.counted_qty}</td>
                <td>{observation.verification_status === "verified" ? "已核实" : "待核实"}</td>
                <td>{observation.disposition
                  ? <>{DISPOSITION_LABELS[observation.disposition.disposition]}<span className="cell-subtitle">{observation.disposition.reason_code}</span></>
                  : "未处置"}</td>
                <td><div className="opening-command-buttons">
                  {observation.allowed_dispositions.map((disposition) => <Button
                    key={disposition}
                    tone="secondary"
                    disabled={busy}
                    onClick={() => openObservationDisposition(
                      observation.observation_id,
                      disposition,
                    )}
                  >{DISPOSITION_LABELS[disposition]}</Button>)}
                  {observation.allowed_dispositions.length === 0 && <span>-</span>}
                </div></td>
              </tr>)}
            </tbody></table></div>}
      </div>

      <div className="opening-detail-section opening-command-section">
        <header><div><h3>当前允许操作</h3><p>服务端未返回的操作不会显示，也不能从状态推断。</p></div></header>
        <div className="opening-command-buttons">
          {availableActions.has("review_region") && <Button onClick={() => openReview("review_region")}>区域复核</Button>}
          {availableActions.has("review_headquarters") && <Button onClick={() => openReview("review_headquarters")}>总部复核</Button>}
          {availableActions.has("open_recount") && <Button tone="secondary" icon={<RotateCcw size={16} />} onClick={() => setDialog({ kind: "open_recount" })}>发起复盘</Button>}
          {availableActions.has("post") && <Button icon={<CheckCircle2 size={16} />} disabled={busy} onClick={() => void terminal("post")}>独立过账</Button>}
          {availableActions.has("close") && <Button tone="secondary" disabled={busy} onClick={() => void terminal("close")}>独立关闭</Button>}
          {detail.allowed_actions.length === 0 && <span className="opening-no-action">当前没有服务端授权操作</span>}
        </div>
      </div>
    </section>}

    {detail && dialog?.kind === "count" && <Modal title="提交范围计数" onClose={resetDialog} wide>
      <form className="form-stack" onSubmit={(event) => void submitCount(event)}>
        <div className="alert alert-warning">盲盘期间不得以账面数提示或校验实盘数量。本表只录入现场事实。</div>
        <label className="opening-check"><input type="checkbox" checked={zeroConfirmed} onChange={(event) => setZeroConfirmed(event.target.checked)} />确认该范围现场为零库存</label>
        {!zeroConfirmed && <div className="line-items">
          <div className="line-title"><div><strong>现场实盘行</strong><span className="line-subtitle">数量使用正数，最多三位小数</span></div><Button type="button" tone="secondary" icon={<Plus size={16} />} onClick={() => {
            observationKey.current += 1;
            setObservations((rows) => [...rows, newObservation(observationKey.current)]);
          }}>添加</Button></div>
          {observations.map((row) => <div className="opening-observation" key={row.key}>
            <Field label="物料标识"><input required maxLength={300} value={row.materialIdentifier} onChange={(event) => updateObservation(row.key, { materialIdentifier: event.target.value })} /></Field>
            <Field label="标识类型"><select value={row.identifierType} onChange={(event) => updateObservation(row.key, { identifierType: event.target.value as ObservationDraft["identifierType"] })}><option value="sku_code">SKU</option><option value="qr_code">二维码</option><option value="external_code">外部编码</option><option value="unknown">待核标识</option></select></Field>
            <Field label="状态"><select value={row.conditionCode} onChange={(event) => updateObservation(row.key, { conditionCode: event.target.value as ObservationDraft["conditionCode"] })}><option value="new">新件</option><option value="used">旧件</option><option value="damaged">损坏</option><option value="scrapped">报废</option></select></Field>
            <Field label="可用桶"><select value={row.availabilityBucket} onChange={(event) => updateObservation(row.key, { availabilityBucket: event.target.value as ObservationDraft["availabilityBucket"] })}><option value="available">可用</option><option value="reserved">已预留</option><option value="picking">拣货中</option><option value="outbound">出库中</option><option value="in_transit">运输中</option><option value="arrived_pending">到货待入</option><option value="frozen">冻结</option><option value="return_pending">退回待处理</option><option value="scrap_pending">报废待处理</option></select></Field>
            <Field label="实盘数量"><input required inputMode="decimal" value={row.countedQty} onChange={(event) => updateObservation(row.key, { countedQty: event.target.value })} /></Field>
            <Field label="批次号（可选）"><input value={row.lotNo} onChange={(event) => updateObservation(row.key, { lotNo: event.target.value })} /></Field>
            <Field label="SN（可选）"><input value={row.serialNo} onChange={(event) => updateObservation(row.key, { serialNo: event.target.value })} /></Field>
            <Field label="备注"><input value={row.remark} onChange={(event) => updateObservation(row.key, { remark: event.target.value })} /></Field>
            <Button type="button" tone="quiet" aria-label="删除实盘行" disabled={observations.length === 1} onClick={() => setObservations((rows) => rows.filter((item) => item.key !== row.key))}><Trash2 size={16} /></Button>
          </div>)}
        </div>}
        <div className="form-actions"><Button type="button" tone="quiet" onClick={resetDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在提交" : "确认提交计数"}</Button></div>
      </form>
    </Modal>}

    {detail && (dialog?.kind === "review_region" || dialog?.kind === "review_headquarters") && <Modal title={dialog.kind === "review_region" ? "区域复核" : "总部复核"} onClose={resetDialog} wide>
      <form className="form-stack" onSubmit={(event) => void submitReview(event)}>
        <Field label="本级决定"><select required value={reviewDecision} onChange={(event) => setReviewDecision(event.target.value as typeof reviewDecision)}>
          <option value="">请选择本级决定</option>
          {compatibleReviewDecisions(detail, dialog.kind).map((decision) => <option key={decision} value={decision}>{statusLabel(decision)}</option>)}
        </select></Field>
        {detail.differences.length > 0 && <div className="table-wrap"><table><thead><tr><th>差异</th><th>账面 / 实盘 / 差异</th><th>逐行决定</th><th>说明</th></tr></thead><tbody>
          {detail.differences.map((row) => <ReviewDifferenceRow key={row.difference_id} detail={detail} difference={row} topLevelDecision={reviewDecision} comment={itemComments[row.difference_id] || ""} onComment={(value) => setItemComments((current) => ({ ...current, [row.difference_id]: value }))} />)}
        </tbody></table></div>}
        <Field label="复核说明"><textarea rows={3} maxLength={10000} required={Boolean(reviewDecision && reviewDecision !== "approve")} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} /></Field>
        <div className="form-actions"><Button type="button" tone="quiet" onClick={resetDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在提交" : "提交本级复核"}</Button></div>
      </form>
    </Modal>}

    {detail && dialog?.kind === "observation_disposition" && <Modal title={DISPOSITION_LABELS[dialog.disposition]} onClose={resetDialog} wide>
      <form className="form-stack" onSubmit={(event) => void submitObservationDisposition(event)}>
        <div className="alert alert-info">
          本次处置只绑定当前任务、当前轮次和当前观察项；处置完成后仍须独立复核。
        </div>
        {dialog.disposition === "resolved_existing_master" && <>
          <div className="alert alert-warning">
            必须先在正式主数据中人工确认 UUID。本页不会搜索、猜测或自动选择物料、批次或 SN。
          </div>
          <Field label="正式物料 UUID"><input required aria-label="正式物料 UUID" value={resolvedMaterialId} onChange={(event) => setResolvedMaterialId(event.target.value)} /></Field>
          <Field label="正式批次 UUID（可选）"><input aria-label="正式批次 UUID（可选）" value={resolvedLotId} onChange={(event) => setResolvedLotId(event.target.value)} /></Field>
          <Field label="正式 SN UUID（可选）"><input aria-label="正式 SN UUID（可选）" value={resolvedSerialId} onChange={(event) => setResolvedSerialId(event.target.value)} /></Field>
        </>}
        <Field label="原因代码"><input required maxLength={80} value={dispositionReason} onChange={(event) => setDispositionReason(event.target.value)} /></Field>
        <Field label="处置说明"><textarea required={dialog.disposition !== "resolved_existing_master"} rows={3} maxLength={4000} value={dispositionComment} onChange={(event) => setDispositionComment(event.target.value)} /></Field>
        <div className="form-actions"><Button type="button" tone="quiet" onClick={resetDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在提交" : "确认处置"}</Button></div>
      </form>
    </Modal>}

    {detail && dialog?.kind === "open_recount" && <Modal title="发起独立复盘轮次" onClose={resetDialog} wide>
      <form className="form-stack" onSubmit={(event) => void submitRecount(event)}>
        <div className="alert alert-info">复盘会创建新轮次，不覆盖原计数、差异或复核证据。仅可选择当前任务、范围和权限下的正式人员。</div>
        <div className="opening-recount-list">{detail.scopes.map((scope) => <div key={scope.scope_id}>
          <label className="opening-check"><input type="checkbox" disabled={busy} checked={Boolean(recountSelections[scope.scope_id])} onChange={(event) => {
            setRecountSelections((current) => ({ ...current, [scope.scope_id]: event.target.checked }));
            setRecountAssignees((current) => { const next = { ...current }; delete next[scope.scope_id]; return next; });
          }} />范围 {scope.scope_no}</label>
          {recountSelections[scope.scope_id] && recountContexts[scope.scope_id] && <OpeningRecountAssigneePicker
            context={recountContexts[scope.scope_id]!}
            label={`范围 ${scope.scope_no} 复盘人员`} selection={recountAssignees[scope.scope_id] || null} disabled={busy}
            onChange={(selection) => setRecountAssignees((current) => {
              const next = { ...current };
              if (selection) next[scope.scope_id] = selection; else delete next[scope.scope_id];
              return next;
            })} />}
          {recountSelections[scope.scope_id] && !recountContexts[scope.scope_id] && <div role="alert">当前身份、任务或轮次尚未核验，不能选择复盘人员。</div>}
        </div>)}</div>
        <Field label="复盘原因"><textarea required rows={3} maxLength={4000} value={recountReason} onChange={(event) => setRecountReason(event.target.value)} /></Field>
        <div className="form-actions"><Button type="button" tone="quiet" onClick={resetDialog}>取消</Button><Button type="submit" disabled={busy || !actor}>{busy ? "正在创建" : "创建复盘轮次"}</Button></div>
      </form>
    </Modal>}
  </>;
}

function ReviewDifferenceRow({
  detail,
  difference,
  topLevelDecision,
  comment,
  onComment,
}: {
  detail: OpeningStocktakeTaskDetail;
  difference: OpeningStocktakeDifference;
  topLevelDecision: OpeningReviewTopDecision | "";
  comment: string;
  onComment: (value: string) => void;
}) {
  let policy = null;
  if (topLevelDecision) {
    try {
      policy = openingReviewItemPolicy(detail, difference, topLevelDecision);
    } catch {
      policy = null;
    }
  }
  const decisionText = policy
    ? policy.decision === "pending_verification"
      ? difference.difference_type === "control_unassigned"
        ? "待控制账核验"
        : "保留待核实"
      : ({
        accept_for_posting: "接受过账",
        recount: "要求复盘",
        reject: "驳回",
      } as const)[policy.decision]
    : topLevelDecision ? "当前决定不适用" : "请先选择本级决定";
  return <tr>
    <td>#{difference.difference_no}<span className="cell-subtitle">{difference.difference_type}</span></td>
    <td>{difference.book_qty} / {difference.counted_qty} / {difference.difference_qty}</td>
    <td><span aria-label={`差异 ${difference.difference_no} 决定`}>{decisionText}</span></td>
    <td><input required={Boolean(policy?.comment_required)} aria-label={`差异 ${difference.difference_no} 说明`} maxLength={4000} value={comment} onChange={(event) => onComment(event.target.value)} /></td>
  </tr>;
}
