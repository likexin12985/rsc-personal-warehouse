import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Eye,
  FileCheck2,
  Plus,
  RefreshCw,
  Scale,
  ShieldCheck,
} from "lucide-react";

import {
  approveOpeningReconciliation,
  explainOpeningReconciliation,
  loadOpeningReconciliationDetail,
  loadOpeningReconciliations,
  startOpeningReconciliation,
  type OpeningReconciliationDetail,
  type OpeningReconciliationSummary,
} from "../formalOpeningReconciliation";
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


type Dialog = "explain" | "approve" | null;
type ExplanationDraft = {
  explanation: string;
  evidenceReference: string;
  evidenceFileId: string;
};

const STATUS_LABELS = {
  differences: "待解释差异",
  approved: "总部已批准",
} as const;

const ITEM_STATUS_LABELS = {
  difference: "待解释",
  explained: "已解释待批准",
  resolved: "已批准解决",
} as const;

function statusLabel(status: OpeningReconciliationSummary["status"]): string {
  return STATUS_LABELS[status];
}

function actionLabel(action: string): string {
  return ({ explain: "解释差异", approve: "总部批准" } as Record<string, string>)[action] || action;
}

function SummaryTable({
  rows,
  onOpen,
}: {
  rows: OpeningReconciliationSummary[];
  onOpen: (runId: string) => void;
}) {
  return <div className="table-wrap"><table className="reconciliation-run-table">
    <thead><tr>
      <th>期初任务</th><th>独立状态</th><th>差异进度</th><th>外部快照</th>
      <th>本地游标</th><th>允许操作</th><th>详情</th>
    </tr></thead>
    <tbody>{rows.map((row) => <tr key={row.reconciliation_run_id}>
      <td><strong>{row.task_no}</strong><span className="cell-subtitle mono">{row.task_id}</span></td>
      <td><span className={`status ${row.status === "approved" ? "status-approved" : "status-pending"}`}>{statusLabel(row.status)}</span><span className="cell-subtitle">对账 v{row.version}</span></td>
      <td>{row.status === "approved"
        ? `${row.resolved_item_count} / ${row.item_count} 已批准解决`
        : `${row.explained_item_count} / ${row.item_count} 已解释待批准`}
        <span className="cell-subtitle">{row.status === "approved" ? "原因与证据继续保留" : `${row.resolved_item_count} 项已解决`}</span>
      </td>
      <td>{formatDate(row.external_snapshot_at)}<span className="cell-subtitle mono">区域 {row.region_org_id}</span></td>
      <td className="mono">{row.local_ledger_cursor}</td>
      <td><div className="opening-action-tags">{row.allowed_actions.length
        ? row.allowed_actions.map((action) => <span key={action}>{actionLabel(action)}</span>)
        : <span>无</span>}</div></td>
      <td><button className="table-action" onClick={() => onOpen(row.reconciliation_run_id)}><Eye size={16} />查看</button></td>
    </tr>)}</tbody>
  </table></div>;
}

function ReconciliationLifecycle({ detail }: { detail: OpeningReconciliationDetail }) {
  const explained = detail.items.every((item) => item.status !== "difference");
  const approved = detail.status === "approved";
  return <div className="opening-lifecycle reconciliation-lifecycle" aria-label="对账独立状态">
    <div><span>OAM 控制快照</span><strong>已校验封存</strong></div>
    <div><span>本地明细账</span><strong>游标 {detail.local_ledger_cursor}</strong></div>
    <div><span>区域差异解释</span><strong>{explained ? "已完整解释" : "待完整解释"}</strong></div>
    <div><span>总部独立批准</span><strong>{approved ? "已批准" : "未批准"}</strong></div>
    <div><span>期初关闭</span><strong>{approved ? "可由盘点服务重证" : "控制差异阻断"}</strong></div>
  </div>;
}

export default function FormalOpeningReconciliationsPage({
  canCreate,
}: {
  canCreate: boolean;
}) {
  const [runs, setRuns] = useState<OpeningReconciliationSummary[]>([]);
  const [nextAfterId, setNextAfterId] = useState<string | null>(null);
  const [detail, setDetail] = useState<OpeningReconciliationDetail | null>(null);
  const [taskId, setTaskId] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [drafts, setDrafts] = useState<Record<string, ExplanationDraft>>({});
  const [approvalComment, setApprovalComment] = useState("");
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadList = useCallback(async (afterId: string | null = null) => {
    setListLoading(true);
    setError("");
    try {
      const page = await loadOpeningReconciliations(afterId);
      if (afterId === null) {
        setRuns(page.items);
      } else {
        setRuns((current) => {
          const seen = new Set(current.map((row) => row.reconciliation_run_id.toLowerCase()));
          if (page.items.some((row) => seen.has(row.reconciliation_run_id.toLowerCase()))) {
            throw new Error("正式期初对账跨页返回重复批次，已停止合并");
          }
          return [...current, ...page.items];
        });
      }
      setNextAfterId(page.next_after_id);
    } catch (reason) {
      setError(showError(reason));
    } finally {
      setListLoading(false);
    }
  }, []);

  const openDetail = useCallback(async (runId: string) => {
    setDetailLoading(true);
    setError("");
    setNotice("");
    try {
      setDetail(await loadOpeningReconciliationDetail(runId));
    } catch (reason) {
      setDetail(null);
      setError(showError(reason));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  const allowedActions = useMemo(
    () => new Set(detail?.allowed_actions || []),
    [detail],
  );

  function closeDialog(): void {
    setDialog(null);
    setDrafts({});
    setApprovalComment("");
  }

  function openExplanation(): void {
    if (!detail?.allowed_actions.includes("explain")) return;
    setDrafts(Object.fromEntries(detail.items.map((item) => [
      item.reconciliation_item_id,
      {
        explanation: item.explanation,
        evidenceReference: item.evidence_reference,
        evidenceFileId: item.evidence_file_id || "",
      },
    ])));
    setDialog("explain");
  }

  async function createRun(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!canCreate) return;
    const checkedTaskId = taskId.trim();
    if (!checkedTaskId) {
      setError("请输入已独立过账的期初任务标识");
      return;
    }
    if (!window.confirm("确认基于该期初任务的封存控制快照与本地账本创建独立对账？本操作不会访问或回写 OAM。")) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await startOpeningReconciliation(checkedTaskId);
      setDetail(result.detail);
      setTaskId("");
      setNotice(`已创建独立对账，共 ${result.result.item_count} 项待核差异；未执行期初关闭。`);
      await loadList();
    } catch (reason) {
      setError(showError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitExplanation(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || dialog !== "explain" || !detail.allowed_actions.includes("explain")) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await explainOpeningReconciliation(
        detail.reconciliation_run_id,
        detail.items.map((item) => {
          const draft = drafts[item.reconciliation_item_id];
          return {
            reconciliation_item_id: item.reconciliation_item_id,
            explanation: draft?.explanation.trim() || "",
            evidence_reference: draft?.evidenceReference.trim() || "",
            evidence_file_id: draft?.evidenceFileId.trim() || null,
          };
        }),
      );
      setDetail(result.detail);
      setNotice(`已完整解释 ${result.result.explained_item_count} 项控制差异；总部批准与期初关闭仍为独立动作。`);
      closeDialog();
      await loadList();
    } catch (reason) {
      setError(showError(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submitApproval(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!detail || dialog !== "approve" || !detail.allowed_actions.includes("approve")) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await approveOpeningReconciliation(
        detail.reconciliation_run_id,
        approvalComment.trim(),
      );
      setDetail(result.detail);
      setNotice(`总部已批准 ${result.result.resolved_item_count} 项控制差异；期初任务不会在本页自动关闭。`);
      closeDialog();
      await loadList();
    } catch (reason) {
      setError(showError(reason));
    } finally {
      setBusy(false);
    }
  }

  function updateDraft(itemId: string, patch: Partial<ExplanationDraft>): void {
    setDrafts((current) => ({
      ...current,
      [itemId]: { ...current[itemId], ...patch },
    }));
  }

  return <>
    <SectionHeader
      title="控制账对账"
      subtitle="OAM 省级控制数与期初本地明细账只做左右比较；解释、总部批准和盘点关闭保持独立"
      actions={<Button tone="secondary" icon={<RefreshCw size={17} />} disabled={listLoading || busy} onClick={() => void loadList()}>刷新</Button>}
    />
    <div className="alert alert-info">
      <ShieldCheck size={18} />
      <span>本页仅调用 /api/v1/reconciliations/opening 正式接口，消费云端已校验快照；不会访问 /integrations/oam、外部 OAM 或改写库存流水。</span>
    </div>
    {error && <div className="alert alert-error" role="alert">{error}</div>}
    {notice && <div className="form-notice opening-notice">{notice}</div>}

    {canCreate && <section className="content-section reconciliation-start-panel">
      <div className="content-title"><div><h2>创建期初控制账对账</h2><p>客户端先鲜读任务版本；服务端仅接受已过账且仍有待核控制差异的正式任务。</p></div></div>
      <form className="reconciliation-start-form" onSubmit={(event) => void createRun(event)}>
        <Field label="期初任务标识" hint="请输入正式 UUID；不会按单号猜测任务。">
          <input aria-label="期初任务标识" value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder="00000000-0000-4000-8000-000000000000" />
        </Field>
        <div className="form-actions"><Button type="submit" icon={<Plus size={16} />} disabled={busy}>{busy ? "正在创建" : "创建独立对账"}</Button></div>
      </form>
    </section>}

    <section className="content-section table-section">
      <div className="content-title"><div><h2>正式期初对账批次</h2><p>每个任务仅保留一条正式批次；按钮完全来自服务端 allowed_actions。</p></div></div>
      {listLoading && runs.length === 0 ? <Loading label="正在读取正式期初对账" />
        : runs.length === 0 ? <Empty title="暂无可见正式期初对账" detail="不会借用 v0.9 OAM 页面生成对账事实。" />
          : <SummaryTable rows={runs} onOpen={(runId) => void openDetail(runId)} />}
      {nextAfterId && <div className="pagination"><span>还有更多正式对账批次</span><Button tone="secondary" disabled={listLoading} onClick={() => void loadList(nextAfterId)}>加载下一页</Button></div>}
    </section>

    {detailLoading && <Loading label="正在校验对账详情" />}
    {detail && <section className="content-section opening-detail reconciliation-detail" aria-label="对账详情">
      <div className="content-title"><div><h2>{detail.task_no}</h2><p className="mono">{detail.reconciliation_run_id}</p></div><span className={`status ${detail.status === "approved" ? "status-approved" : "status-pending"}`}>{statusLabel(detail.status)}</span></div>
      <div className="alert alert-warning">OAM 控制数不会计入本地库存；有差异时必须保留完整解释与证据，批准后仍由盘点关闭服务独立重证。</div>
      <ReconciliationLifecycle detail={detail} />
      <dl className="detail-grid opening-detail-grid">
        <div><dt>OAM 来源系统</dt><dd className="mono">{detail.source_system_id}</dd></div>
        <div><dt>外部快照时间</dt><dd>{formatDate(detail.external_snapshot_at)}</dd></div>
        <div><dt>本地账本游标</dt><dd className="mono">{detail.local_ledger_cursor}</dd></div>
        <div><dt>盘点轮次</dt><dd className="mono">{detail.round_id}</dd></div>
        <div><dt>期初过账</dt><dd className="mono">{detail.posting_id}</dd></div>
        <div><dt>差异清单摘要</dt><dd className="mono">{detail.difference_manifest_sha256}</dd></div>
      </dl>

      <div className="opening-detail-section">
        <header><div><h3>左右账与待核证据</h3><p>OAM 控制数与本地明细账保持两列展示，绝不相加。</p></div></header>
        <div className="inventory-summary">
          <span>全部差异<strong>{detail.item_count}</strong></span>
          <span>已解释待批准<strong>{detail.explained_item_count}</strong></span>
          <span>已批准解决<strong>{detail.resolved_item_count}</strong></span>
          <span>对账版本<strong>v{detail.version}</strong></span>
        </div>
        <div className="table-wrap"><table className="reconciliation-item-table"><thead><tr>
          <th>外部业务键 / 物料</th><th className="num">OAM 控制数</th><th className="num">本地明细账</th><th className="num">差异</th><th>独立状态</th><th>原因与证据</th>
        </tr></thead><tbody>{detail.items.map((item) => <tr key={item.reconciliation_item_id}>
          <td><strong className="mono">{item.business_key}</strong><span className="cell-subtitle mono">{item.material_id || "待映射物料"}</span></td>
          <td className="num mono">{item.external_qty}</td>
          <td className="num mono">{item.local_qty}</td>
          <td className="num mono">{item.difference}</td>
          <td>{ITEM_STATUS_LABELS[item.status]}<span className="cell-subtitle">项版本 v{item.version}</span></td>
          <td>{item.explanation || "尚未解释"}<span className="cell-subtitle">{item.evidence_reference || "尚无证据引用"}</span>{item.evidence_file_id && <span className="cell-subtitle mono">文件 {item.evidence_file_id}</span>}</td>
        </tr>)}</tbody></table></div>
      </div>

      {detail.approval_comment && <div className="opening-detail-section"><div className="alert alert-info"><FileCheck2 size={18} />总部批准说明：{detail.approval_comment}</div></div>}
      <div className="opening-detail-section opening-command-section">
        <header><div><h3>当前允许操作</h3><p>服务端未返回的解释或批准操作不会显示；本页没有关闭动作。</p></div></header>
        <div className="opening-command-buttons">
          {allowedActions.has("explain") && <Button icon={<FileCheck2 size={16} />} disabled={busy} onClick={openExplanation}>逐项解释差异</Button>}
          {allowedActions.has("approve") && <Button icon={<CheckCircle2 size={16} />} disabled={busy} onClick={() => setDialog("approve")}>总部批准对账</Button>}
          {detail.allowed_actions.length === 0 && <span className="opening-no-action">当前没有服务端授权操作</span>}
        </div>
      </div>
    </section>}

    {detail && dialog === "explain" && <Modal title="逐项解释期初控制差异" onClose={closeDialog} wide>
      <form className="form-stack" onSubmit={(event) => void submitExplanation(event)}>
        <div className="alert alert-warning"><Scale size={18} />必须一次覆盖当前批次全部差异；解释不会改写 OAM 控制快照或本地库存。</div>
        <div className="reconciliation-explanation-list">{detail.items.map((item) => {
          const draft = drafts[item.reconciliation_item_id] || { explanation: "", evidenceReference: "", evidenceFileId: "" };
          return <section key={item.reconciliation_item_id} className="reconciliation-explanation-row">
            <div><strong className="mono">{item.business_key}</strong><span>OAM {item.external_qty} / 本地 {item.local_qty} / 差异 {item.difference}</span></div>
            <Field label="差异原因"><textarea aria-label={`${item.business_key} 差异原因`} required minLength={4} maxLength={4000} rows={3} value={draft.explanation} onChange={(event) => updateDraft(item.reconciliation_item_id, { explanation: event.target.value })} /></Field>
            <Field label="证据引用"><input aria-label={`${item.business_key} 证据引用`} required minLength={4} maxLength={1000} value={draft.evidenceReference} onChange={(event) => updateDraft(item.reconciliation_item_id, { evidenceReference: event.target.value })} placeholder="盘点单、交接单或受控证据编号" /></Field>
            <Field label="证据文件标识（可选）"><input aria-label={`${item.business_key} 证据文件标识`} value={draft.evidenceFileId} onChange={(event) => updateDraft(item.reconciliation_item_id, { evidenceFileId: event.target.value })} placeholder="正式文件 UUID" /></Field>
          </section>;
        })}</div>
        <div className="form-actions"><Button type="button" tone="quiet" onClick={closeDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在提交" : "提交全部解释"}</Button></div>
      </form>
    </Modal>}

    {detail && dialog === "approve" && <Modal title="总部批准期初控制账对账" onClose={closeDialog}>
      <form className="form-stack" onSubmit={(event) => void submitApproval(event)}>
        <div className="alert alert-warning">批准只解决独立控制账差异；不会改写已过账库存，也不会自动关闭期初任务。</div>
        <Field label="批准说明"><textarea aria-label="批准说明" required minLength={4} maxLength={4000} rows={4} value={approvalComment} onChange={(event) => setApprovalComment(event.target.value)} /></Field>
        <div className="form-actions"><Button type="button" tone="quiet" onClick={closeDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在批准" : "确认总部批准"}</Button></div>
      </form>
    </Modal>}
  </>;
}
