import { createShipmentStore, type ShipmentStore } from "../materialRequestShipmentRecovery";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Edit3, Eye, Plus, RefreshCw, Send, Trash2 } from "lucide-react";

import { ApiError } from "../api";
import {
  type FormalMaterialCatalogItem,
  validateFormalMaterialCatalogPage,
} from "../formalMaterialCatalog";
import {
  MaterialRequestCreateIntentRegistry,
  MaterialRequestIntentRegistry,
  type MaterialRequestCreateResult,
  type MaterialRequestDetail,
  type MaterialRequestDraftInput,
  type MaterialRequestDraftLine,
  type MaterialRequestMutationIntent,
  type MaterialRequestMutationResult,
  type MaterialRequestSummary,
  validateMaterialRequestCreateResult,
  validateMaterialRequestDetail,
  validateMaterialRequestDraftInput,
  validateMaterialRequestMutationResult,
  validateMaterialRequestPage,
} from "../formalMaterialRequests";
import {
  type FormalMaterialRequestAccess,
  type FormalMaterialRequestAdapter,
  isDefinitiveMaterialRequestRejection,
  validateFormalMaterialRequestAccess,
  validateFormalMaterialRequestEditableDraft,
} from "../formalMaterialRequestAdapter";
import {
  type MaterialRequestWorkOrderOption,
  validateMaterialRequestWorkOrderOptionDetail,
  validateMaterialRequestWorkOrderOptionPage,
} from "../formalMaterialRequestOptions";
import {
  createMaterialRequestLifecycleRecoveryStore,
  lifecycleSentinelBlockingMessage,
  recoverMaterialRequestLifecycleCommand,
  type MaterialRequestLifecycleRecoveryStore,
  type MaterialRequestLifecycleSentinel,
  type MaterialRequestLifecycleSentinelRead,
} from "../materialRequestLifecycleRecovery";
import FormalFileUploadField, {
  type AvailableFormalFile,
  type FormalFileUploadClient,
  defaultFormalFileUploadClient,
} from "../FormalFileUploadField";
import FormalMaterialRequestReservationPanel from "../FormalMaterialRequestReservationPanel";
import FormalMaterialRequestOutboundPanel from "../FormalMaterialRequestOutboundPanel";
import FormalMaterialRequestShipmentPanel from "../FormalMaterialRequestShipmentPanel";
import FormalMaterialRequestInboundPanel from "../FormalMaterialRequestInboundPanel";
import FormalMaterialRequestReceiptPanel from "../FormalMaterialRequestReceiptPanel";
import FormalMaterialRequestOamReceiptPanel from "../FormalMaterialRequestOamReceiptPanel";
import { createOutboundStore, type OutboundStore } from "../materialRequestOutbound";
import FormalMaterialRequestPickPanel from "../FormalMaterialRequestPickPanel";
import { createPickStore, type PickStore } from "../materialRequestReservationPick";
import FormalMaterialRequestReleasePanel from "../FormalMaterialRequestReleasePanel";
import FormalMaterialRequestFulfillmentPreparationPanel from "../FormalMaterialRequestFulfillmentPreparationPanel";
import { createReleaseStore, type ReleaseStore } from "../materialRequestReservationRelease";
import { createReservationRecoveryStore, type ReservationRecoveryStore } from "../materialRequestReservationRecovery";
import FormalMaterialRequestSupplyPanel from "../FormalMaterialRequestSupplyPanel";
import { createSupplyRecoveryStore, supplyRecoveryBlocked, type SupplyRecoveryStore } from "../materialRequestSupplyRecovery";
import { createAllocationRecoveryStore, type AllocationRecoveryStore } from "../materialRequestAllocationRecovery";
import { Button, Empty, Field, Loading, Modal, SectionHeader, showError } from "../ui";

const REQUEST_STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  submitted: "已提交",
  approval_in_progress: "审批中",
  returned: "已退回",
  partially_approved: "部分批准",
  approved: "已批准",
  rejected: "已驳回",
  withdrawn: "已撤回",
  cancellation_pending: "取消处理中",
  cancelled: "已取消",
};
const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$/;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f]/;
const ACTIVE_WITHDRAW_STEP_STATUSES = new Set([
  "pending", "open", "awaiting_external_evidence", "evidence_pending_verification",
]);

function isLifecycleRejectionSafeToClear(error: unknown): boolean {
  return isDefinitiveMaterialRequestRejection(error)
    && error instanceof ApiError
    // Authentication and authorization drift must keep the original trace coordinate. A 409 is
    // also retained conservatively because it may be an authorization-version/idempotency conflict.
    && ![401, 403, 409].includes(error.status);
}

const AXIS_LABELS: ReadonlyArray<readonly [keyof MaterialRequestDetail["states"], string]> = [
  ["request_status", "申请"],
  ["allocation_status", "分配"],
  ["reservation_status", "占用"],
  ["outbound_status", "出库"],
  ["shipment_status", "发货"],
  ["logistics_signature_status", "物流签收"],
  ["oam_receipt_status", "OAM收货"],
  ["personal_inbound_status", "RSC/个人仓入库"],
  ["notification_status", "通知送达"],
  ["reconciliation_status", "对账同步"],
];

type DraftLineState = {
  key: number;
  material: FormalMaterialCatalogItem | null;
  requestedQty: string;
  requiredDate: string;
  substituteMaterial: FormalMaterialCatalogItem | null;
  note: string;
};

type MaterialPickerState = {
  lineKey: number;
  target: "material" | "substitute";
  query: string;
  items: FormalMaterialCatalogItem[];
  nextAfterId: string | null;
  loading: boolean;
  error: string;
};

type WorkOrderPickerState = {
  query: string;
  items: MaterialRequestWorkOrderOption[];
  nextAfterId: string | null;
  cursorHistory: string[];
  loading: boolean;
  error: string;
};

type DraftWorkOrderResolution = Readonly<{
  item: MaterialRequestWorkOrderOption | null;
  unavailable: boolean;
}>;

type ApprovalLineState = {
  requestLineId: string;
  inputQty: string;
  decisionQty: string;
  reason: string;
};

type ApprovalProcessState = {
  kind: "internal" | "external_registration" | "external_verification";
  stepId: string;
  stepVersion: number;
  action: "approve" | "return" | "reject";
  lines: ApprovalLineState[];
  comment: string;
  evidenceFileId: string;
  externalApproverName: string;
  externalReferenceNo: string;
  externalDecidedAt: string;
  registrationId: string;
  verificationDecision: "accept" | "reject";
  error: string;
  pendingMessage: string;
};

type LifecycleProcessState = {
  kind: "withdraw" | "cancel";
  requestId: string;
  requestVersion: number;
  reason: string;
  lines: Array<{
    requestLineId: string;
    cancelledQty: string;
    reason: string;
  }>;
  error: string;
  pendingMessage: string;
};

type LifecycleRecoveryView = Readonly<{
  phase: "ready" | "checking" | "blocked" | "recovered";
  message: string;
  retryable: boolean;
}>;

function recoveryView(read: MaterialRequestLifecycleSentinelRead): LifecycleRecoveryView {
  if (read.kind === "missing") return { phase: "ready", message: "", retryable: false };
  return {
    phase: read.kind === "valid" ? "checking" : "blocked",
    message: lifecycleSentinelBlockingMessage(read),
    retryable: read.kind === "valid",
  };
}

type DraftFormState = {
  workOrder: MaterialRequestWorkOrderOption | null;
  workOrderUnavailable: boolean;
  purpose: string;
  urgency: "normal" | "urgent" | "emergency";
  expectedDate: string;
  provinceCode: string;
  provinceName: string;
  cityName: string;
  districtName: string;
  addressDetail: string;
  contactName: string;
  contactMobile: string;
  attachmentFileIds: string[];
  note: string;
  lines: DraftLineState[];
};

type FormMode = Readonly<{ kind: "create" }> | Readonly<{
  kind: "edit";
  requestId: string;
  requestVersion: number;
}>;

function emptyLine(key: number): DraftLineState {
  return {
    key,
    material: null,
    requestedQty: "",
    requiredDate: "",
    substituteMaterial: null,
    note: "",
  };
}

function emptyForm(key: number): DraftFormState {
  return {
    workOrder: null,
    workOrderUnavailable: false,
    purpose: "",
    urgency: "normal",
    expectedDate: "",
    provinceCode: "",
    provinceName: "",
    cityName: "",
    districtName: "",
    addressDetail: "",
    contactName: "",
    contactMobile: "",
    attachmentFileIds: [],
    note: "",
    lines: [emptyLine(key)],
  };
}

function formFromDraft(
  draft: MaterialRequestDraftInput,
  nextKey: () => number,
  materials: ReadonlyMap<string, FormalMaterialCatalogItem>,
  workOrder: DraftWorkOrderResolution,
): DraftFormState {
  return {
    workOrder: workOrder.item,
    workOrderUnavailable: workOrder.unavailable,
    purpose: draft.purpose,
    urgency: draft.urgency,
    expectedDate: draft.expected_date || "",
    provinceCode: draft.address.province_code,
    provinceName: draft.address.province_name,
    cityName: draft.address.city_name,
    districtName: draft.address.district_name,
    addressDetail: draft.address.detail,
    contactName: draft.contact.name,
    contactMobile: draft.contact.mobile,
    attachmentFileIds: [...draft.attachment_file_ids],
    note: draft.note,
    lines: draft.lines.map((line) => ({
      key: nextKey(),
      material: materials.get(line.material_id) || null,
      requestedQty: line.requested_qty,
      requiredDate: line.required_date || "",
      substituteMaterial: line.suggested_substitute_material_id
        ? materials.get(line.suggested_substitute_material_id) || null
        : null,
      note: line.note,
    })),
  };
}

function draftFromForm(
  form: DraftFormState,
  uploadedFiles: readonly AvailableFormalFile[],
): MaterialRequestDraftInput {
  if (form.workOrderUnavailable) {
    throw new ApiError(409, "原关联工单当前不可选，必须明确清除或从正式列表重新选择");
  }
  return validateMaterialRequestDraftInput({
    work_order_id: form.workOrder?.work_order_id || null,
    purpose: form.purpose,
    urgency: form.urgency,
    expected_date: form.expectedDate || null,
    address: {
      province_code: form.provinceCode,
      province_name: form.provinceName,
      city_name: form.cityName,
      district_name: form.districtName,
      detail: form.addressDetail,
    },
    contact: { name: form.contactName, mobile: form.contactMobile },
    attachment_file_ids: [
      ...form.attachmentFileIds,
      ...uploadedFiles.map((file) => file.file_id),
    ],
    note: form.note,
    lines: form.lines.map((line): MaterialRequestDraftLine => ({
      material_id: line.material?.material_id || "",
      requested_qty: line.requestedQty,
      required_date: line.requiredDate || null,
      suggested_substitute_material_id: line.substituteMaterial?.material_id || null,
      note: line.note,
    })),
  });
}

function trackingLabel(mode: FormalMaterialCatalogItem["tracking_mode"]): string {
  return ({
    none: "不追踪",
    lot: "批次",
    serial: "序列号",
    lot_and_serial: "批次+序列号",
  } as const)[mode];
}

function materialLabel(item: FormalMaterialCatalogItem): string {
  return `${item.sku_code}｜${item.name}｜${item.specification || "无规格"}｜${item.base_unit}｜${trackingLabel(item.tracking_mode)}`;
}

function workOrderSourceTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function currentApprovalStep(detail: MaterialRequestDetail) {
  const stepId = detail.approval_instance?.current_step_id;
  return stepId
    ? detail.approval_instance?.steps.find((step) => step.step_id === stepId) || null
    : null;
}

function approvalInputLines(detail: MaterialRequestDetail): ApprovalLineState[] {
  const step = currentApprovalStep(detail);
  if (!step) throw new Error("当前审批步骤锚点缺失，已停止处理");
  const predecessor = step.predecessor_step_id
    ? detail.approval_instance?.steps.find((item) => item.step_id === step.predecessor_step_id)
    : null;
  const predecessorQty = new Map(
    (predecessor?.line_decisions || []).map((item) => [item.request_line_id, item.approved_qty]),
  );
  const rows = detail.lines.map((line) => {
    const inputQty = step.step_no === 1 ? line.requested_qty : predecessorQty.get(line.request_line_id);
    if (!inputQty) throw new Error("审批前序逐行数量不完整，已停止处理");
    return {
      requestLineId: line.request_line_id,
      inputQty,
      decisionQty: inputQty,
      reason: "",
    };
  });
  if (step.step_no > 1 && predecessorQty.size !== detail.lines.length) {
    throw new Error("审批前序逐行数量与当前修订不一致，已停止处理");
  }
  return rows;
}

function decimalUnits(value: string): bigint | null {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
}

function approvalLinePayload(process: ApprovalProcessState) {
  if (process.action === "reject") return { lines: [], return_lines: [] };
  return process.lines.reduce((result, line) => {
    const input = decimalUnits(line.inputQty);
    const decision = decimalUnits(line.decisionQty);
    if (input === null || decision === null || decision > input
        || (process.action === "return" && decision === 0n)) {
      throw new Error("逐行处理数量无效或超过当前级可处理数量");
    }
    if (process.action === "approve") {
      if (decision < input && !line.reason.trim()) throw new Error("部分驳回的明细必须填写原因");
      result.lines.push({
        request_line_id: line.requestLineId,
        approved_qty: line.decisionQty,
        reason: line.reason.trim(),
      });
    } else {
      if (!line.reason.trim()) throw new Error("退回的每条明细必须填写重审原因");
      result.return_lines.push({
        request_line_id: line.requestLineId,
        requested_reapproval_qty: line.decisionQty,
        reason: line.reason.trim(),
      });
    }
    return result;
  }, {
    lines: [] as Array<{ request_line_id: string; approved_qty: string; reason: string }>,
    return_lines: [] as Array<{ request_line_id: string; requested_reapproval_qty: string; reason: string }>,
  });
}

function axesMatch(
  result: Readonly<{ states: MaterialRequestDetail["states"]; request_version: number }>,
  detail: MaterialRequestDetail,
): boolean {
  return result.request_version === detail.request_version
    && AXIS_LABELS.every(([key]) => result.states[key] === detail.states[key]);
}

function draftWriteMatches(
  result: MaterialRequestCreateResult | MaterialRequestMutationResult,
  detail: MaterialRequestDetail,
  draft: MaterialRequestDraftInput,
): boolean {
  return axesMatch(result, detail)
    && result.revision_id === detail.current_revision_id
    && result.revision_no === detail.current_revision_no
    && detail.work_order_id === draft.work_order_id;
}

function approvalMutationMatches(result: MaterialRequestMutationResult, detail: MaterialRequestDetail): boolean {
  const instance = detail.approval_instance;
  return axesMatch(result, detail)
    && result.revision_id === detail.current_revision_id
    && result.revision_no === detail.current_revision_no
    && result.approval_instance_id === (instance?.instance_id || null)
    && result.approval_attempt_no === (instance?.attempt_no || null)
    && result.current_step_id === (instance?.current_step_id || null);
}

function sameProjection(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function lifecycleInvariantProjectionMatches(
  before: MaterialRequestDetail,
  detail: MaterialRequestDetail,
): boolean {
  return before.schema_version === detail.schema_version
    && before.request_id === detail.request_id
    && before.request_no === detail.request_no
    && before.current_revision_id === detail.current_revision_id
    && before.current_revision_no === detail.current_revision_no
    && before.work_order_id === detail.work_order_id
    && before.requester_person_id === detail.requester_person_id
    && before.requester_org_id === detail.requester_org_id
    && before.purpose === detail.purpose
    && before.urgency === detail.urgency
    && before.expected_date === detail.expected_date
    && before.note === detail.note
    && before.approval_mode === detail.approval_mode
    && before.created_at === detail.created_at
    && before.submitted_at === detail.submitted_at
    && sameProjection(before.address_snapshot, detail.address_snapshot)
    && sameProjection(before.contact_masked, detail.contact_masked)
    && sameProjection(before.attachment_refs, detail.attachment_refs)
    && sameProjection(before.revision_history, detail.revision_history)
    && sameProjection(before.supply_tasks, detail.supply_tasks)
    && before.approval_history.length === detail.approval_history.length
    && sameProjection(
      before.approval_history.slice(0, -1),
      detail.approval_history.slice(0, -1),
    );
}

function lifecycleMutationMatches(
  result: MaterialRequestMutationResult,
  before: MaterialRequestDetail,
  detail: MaterialRequestDetail,
  action: LifecycleProcessState["kind"],
): boolean {
  const beforeInstance = before.approval_instance;
  const afterInstance = detail.approval_instance;
  if (!approvalMutationMatches(result, detail)
      || !lifecycleInvariantProjectionMatches(before, detail)
      || result.current_step_id !== null
      || !beforeInstance
      || !afterInstance
      || result.approval_instance_id !== beforeInstance.instance_id
      || result.approval_attempt_no !== beforeInstance.attempt_no
      || afterInstance.current_step_id !== null
      || afterInstance.current_step_no !== null
      || detail.current_revision_id !== before.current_revision_id
      || detail.current_revision_no !== before.current_revision_no
      || detail.states.request_status !== (action === "withdraw" ? "withdrawn" : "cancelled")
      || AXIS_LABELS.slice(1).some(([key]) => detail.states[key] !== before.states[key])
      || detail.allowed_actions.length !== 0
      || detail.lines.length !== before.lines.length) {
    return false;
  }
  if (action === "withdraw") {
    const cancellableStepCount = beforeInstance.steps.filter(
      (step) => ACTIVE_WITHDRAW_STEP_STATUSES.has(step.status),
    ).length;
    const expectedInstance = {
      ...beforeInstance,
      status: "withdrawn" as const,
      current_step_no: null,
      current_step_id: null,
      version: beforeInstance.version + 1,
      steps: beforeInstance.steps.map((step) => (
        ACTIVE_WITHDRAW_STEP_STATUSES.has(step.status)
          ? { ...step, status: "cancelled" as const, decided_at: null, version: step.version + 1 }
          : step
      )),
    };
    return cancellableStepCount > 0
      && sameProjection(afterInstance, expectedInstance)
      && sameProjection(detail.lines, before.lines);
  }
  const expectedLines = before.lines.map((line) => ({
    ...line,
    cancelled_qty: line.final_approved_qty,
    status: "cancelled" as const,
    version: line.version + 1,
  }));
  return (beforeInstance.status === "completed" || beforeInstance.status === "returned")
    && sameProjection(afterInstance, beforeInstance)
    && sameProjection(detail.lines, expectedLines);
}

function approvalNotice(detail: MaterialRequestDetail): string {
  const currentStepId = detail.approval_instance?.current_step_id;
  const step = currentStepId
    ? detail.approval_instance?.steps.find((item) => item.step_id === currentStepId)
    : null;
  if (step?.source_mode === "external_registration" && step.status === "awaiting_external_evidence") {
    return "外部审批证据尚未登记。当前仅展示审批事实，禁止据此推进供给或履约。";
  }
  if (detail.states.request_status === "returned") {
    return "申请已退回；请按退回意见修改后重新提交，不会自动进入分配或履约。";
  }
  return "审批、供给计划与实际履约是独立事实；本页不推断后续状态。";
}

function DetailPanel({
  detail,
  access,
  busy,
  lifecycleBlocked,
  onEdit,
  onSubmit,
  onProcess,
  onLifecycle,
}: {
  detail: MaterialRequestDetail;
  access: FormalMaterialRequestAccess;
  busy: boolean;
  lifecycleBlocked: boolean;
  onEdit: () => void;
  onSubmit: () => void;
  onProcess: (kind: ApprovalProcessState["kind"]) => void;
  onLifecycle: (kind: LifecycleProcessState["kind"]) => void;
}) {
  const actions = new Set(detail.allowed_actions);
  const editableDraft = ["draft", "returned"].includes(detail.states.request_status);
  const step = currentApprovalStep(detail);
  const internalPermission = step?.step_no === 1
    ? access.can_approve_region
    : step?.step_no === 2 && access.can_approve_headquarters;
  const canProcessInternal = Boolean(
    step?.source_mode === "internal"
    && internalPermission
    && (actions.has("approve") || actions.has("return") || actions.has("reject")),
  );
  const canRegisterExternal = Boolean(
    step?.source_mode === "external_registration"
    && access.can_register_external
    && actions.has("register_external_approval"),
  );
  const pendingEvidence = detail.approval_instance?.external_evidence_summaries?.filter((item) => (
    item.step_id === step?.step_id && item.status === "pending_verification"
  )) || [];
  const canVerifyExternal = Boolean(
    step?.source_mode === "external_registration"
    && access.can_verify_external
    && actions.has("verify_external_approval")
    && pendingEvidence.length === 1,
  );
  const canWithdraw = access.can_withdraw && actions.has("withdraw");
  const canCancel = access.can_cancel && actions.has("cancel");
  return <section aria-label="正式需求详情内容">
    <div className="detail-heading">
      <div>
        <strong className="detail-number">{detail.request_no}</strong>
        <span className={`status status-${detail.states.request_status}`}>
          {REQUEST_STATUS_LABELS[detail.states.request_status] || detail.states.request_status}
        </span>
      </div>
      <span>版本 v{detail.request_version}</span>
    </div>

    <div className="alert alert-info">{approvalNotice(detail)}</div>
    <dl className="detail-grid">
      <div><dt>工单</dt><dd className="mono">{detail.work_order_id || "未关联"}</dd></div>
      <div><dt>用途</dt><dd>{detail.purpose}</dd></div>
      <div><dt>紧急程度</dt><dd>{detail.urgency}</dd></div>
      <div><dt>期望日期</dt><dd>{detail.expected_date || "未填写"}</dd></div>
      <div><dt>联系人（脱敏）</dt><dd>{detail.contact_masked.name_masked} · {detail.contact_masked.mobile_masked}</dd></div>
      <div><dt>收货地址（脱敏）</dt><dd>{detail.address_snapshot.province_name}{detail.address_snapshot.city_name}{detail.address_snapshot.district_name}{detail.address_snapshot.detail_masked}</dd></div>
      <div><dt>申请备注</dt><dd>{detail.note || "无"}</dd></div>
      <div><dt>附件</dt><dd>{detail.attachment_refs.length
        ? detail.attachment_refs.map((file) => file.display_name).join("、")
        : "无"}</dd></div>
      <div><dt>审批模式</dt><dd>{detail.approval_mode}</dd></div>
    </dl>

    <div className="opening-lifecycle" aria-label="需求十个独立状态轴">
      {AXIS_LABELS.map(([key, label]) => <div key={key}>
        <span>{label}</span><strong>{detail.states[key]}</strong>
      </div>)}
    </div>

    <section className="opening-detail-section" aria-label="需求完整明细">
      <header><div><h3>物料明细</h3><p>数量为 Decimal(18,3) 定点字符串</p></div></header>
      <div className="table-wrap"><table>
        <thead><tr><th>行</th><th>物料 ID</th><th>申请数量</th><th>需用日期</th><th>建议替代物料</th><th>备注</th><th>批准/取消</th></tr></thead>
        <tbody>{detail.lines.map((line) => <tr key={line.request_line_id}>
          <td>{line.line_no}</td><td className="mono">{line.material_id}</td><td>{line.requested_qty}</td>
          <td>{line.required_date || "-"}</td><td className="mono">{line.suggested_substitute_material_id || "-"}</td>
          <td>{line.note || "-"}</td><td>{line.final_approved_qty} / {line.cancelled_qty}</td>
        </tr>)}</tbody>
      </table></div>
    </section>

    <section className="opening-detail-section" aria-label="审批进度与处理">
      <header><div><h3>审批进度与处理</h3><p>两级内部逐行审批 + 外部证据登记/独立复核；权限或证据缺失时失败关闭</p></div></header>
      {detail.approval_instance ? <div className="table-wrap"><table>
        <thead><tr><th>步骤</th><th>来源</th><th>状态</th><th>版本</th></tr></thead>
        <tbody>{detail.approval_instance.steps.map((step) => <tr key={step.step_id}>
          <td>{step.step_no}</td><td>{step.source_mode}</td><td>{step.status}</td><td>{step.version}</td>
        </tr>)}</tbody>
      </table></div> : <Empty title="尚未产生审批实例" detail="草稿提交前不会创建审批事实" />}
      {(canProcessInternal || canRegisterExternal || canVerifyExternal) && <div className="form-actions">
        {canProcessInternal && <Button disabled={busy} onClick={() => onProcess("internal")}>处理当前内部审批</Button>}
        {canRegisterExternal && <Button disabled={busy} onClick={() => onProcess("external_registration")}>登记星星总部审批证据</Button>}
        {canVerifyExternal && <Button disabled={busy} onClick={() => onProcess("external_verification")}>独立复核外部审批证据</Button>}
      </div>}
      {actions.has("verify_external_approval") && access.can_verify_external && pendingEvidence.length !== 1
        && <div className="alert alert-warning">当前步骤没有唯一待复核证据，已阻止复核写入。</div>}
    </section>

    <div className="form-actions">
      {editableDraft && actions.has("update") && <Button tone="secondary" icon={<Edit3 size={17} />} disabled={busy} onClick={onEdit}>编辑草稿</Button>}
      {editableDraft && actions.has("submit") && <Button icon={<Send size={17} />} disabled={busy} onClick={onSubmit}>提交前确认</Button>}
      {canWithdraw && <Button tone="secondary" disabled={busy || lifecycleBlocked} onClick={() => onLifecycle("withdraw")}>撤回申请</Button>}
      {canCancel && <Button tone="secondary" disabled={busy || lifecycleBlocked} onClick={() => onLifecycle("cancel")}>安全取消</Button>}
      {!((editableDraft && (actions.has("update") || actions.has("submit"))) || canWithdraw || canCancel)
        && <span className="opening-no-action">当前主体没有可执行的提报动作</span>}
    </div>
  </section>;
}

function DraftForm({
  form,
  mode,
  busy,
  writePending,
  error,
  pendingMessage,
  onChange,
  onLineChange,
  onAddLine,
  onRemoveLine,
  onChooseWorkOrder,
  onClearWorkOrder,
  onChooseMaterial,
  onClearSubstitute,
  uploadClient,
  uploadBindingKey,
  uploadBlocking,
  onUploadedFiles,
  onUploadBlocking,
  onCancel,
  onSave,
}: {
  form: DraftFormState;
  mode: FormMode;
  busy: boolean;
  writePending: boolean;
  error: string;
  pendingMessage: string;
  onChange: <K extends keyof DraftFormState>(key: K, value: DraftFormState[K]) => void;
  onLineChange: (key: number, field: "requestedQty" | "requiredDate" | "note", value: string) => void;
  onAddLine: () => void;
  onRemoveLine: (key: number) => void;
  onChooseWorkOrder: () => void;
  onClearWorkOrder: () => void;
  onChooseMaterial: (key: number, target: MaterialPickerState["target"]) => void;
  onClearSubstitute: (key: number) => void;
  uploadClient: FormalFileUploadClient;
  uploadBindingKey: string;
  uploadBlocking: boolean;
  onUploadedFiles: (files: readonly AvailableFormalFile[]) => void;
  onUploadBlocking: (blocking: boolean) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return <div className="form-stack">
    {error && <div className="form-error">{error}</div>}
    {pendingMessage && <div className="alert alert-warning">{pendingMessage}</div>}
    <div className="form-grid three">
      <Field label="关联 OAM 工单（可空）">
        <div className="form-actions">
          <button
            className="table-action"
            type="button"
            aria-label="选择关联 OAM 工单"
            disabled={busy || writePending}
            onClick={onChooseWorkOrder}
          >{form.workOrder
              ? `${form.workOrder.work_order_no} · ${form.workOrder.status === "active" ? "进行中" : "待处理"}`
              : form.workOrderUnavailable
                ? "原关联工单当前不可选，请重新选择"
                : "从本人当前有效工单中选择"}</button>
          {(form.workOrder || form.workOrderUnavailable) && <button
            className="table-action"
            type="button"
            aria-label="清除关联 OAM 工单"
            disabled={busy || writePending}
            onClick={onClearWorkOrder}
          >明确清除</button>}
        </div>
        {form.workOrderUnavailable && <div className="alert alert-warning" role="alert">
          本人草稿原有关联工单当前不可选；不会显示或查询通用工单详情。保存前必须明确清除，或从正式列表重新选择。
        </div>}
        {form.workOrder && <div className="alert alert-info" aria-label="已选工单最小来源证据">
          来源 {form.workOrder.source_system_code} · 外部锚点 {form.workOrder.source_external_id} · 版本 {form.workOrder.source_version} · 源更新时间 {workOrderSourceTime(form.workOrder.source_updated_at)} · 同步时间 {workOrderSourceTime(form.workOrder.synced_at)} · fresh
        </div>}
      </Field>
      <Field label="用途"><input aria-label="用途" value={form.purpose} onChange={(e) => onChange("purpose", e.target.value)} /></Field>
      <Field label="紧急程度"><select aria-label="紧急程度" value={form.urgency} onChange={(e) => onChange("urgency", e.target.value as DraftFormState["urgency"])}>
        <option value="normal">普通</option><option value="urgent">紧急</option><option value="emergency">应急</option>
      </select></Field>
      <Field label="期望日期（可空）"><input aria-label="期望日期（可空）" type="date" value={form.expectedDate} onChange={(e) => onChange("expectedDate", e.target.value)} /></Field>
      <Field label="联系人"><input aria-label="联系人" value={form.contactName} onChange={(e) => onChange("contactName", e.target.value)} /></Field>
      <Field label="联系电话"><input aria-label="联系电话" type="tel" autoComplete="off" value={form.contactMobile} onChange={(e) => onChange("contactMobile", e.target.value)} /></Field>
      <Field label="省代码"><input aria-label="省代码" value={form.provinceCode} onChange={(e) => onChange("provinceCode", e.target.value)} /></Field>
      <Field label="省"><input aria-label="省" value={form.provinceName} onChange={(e) => onChange("provinceName", e.target.value)} /></Field>
      <Field label="市"><input aria-label="市" value={form.cityName} onChange={(e) => onChange("cityName", e.target.value)} /></Field>
      <Field label="区县"><input aria-label="区县" value={form.districtName} onChange={(e) => onChange("districtName", e.target.value)} /></Field>
      <Field label="详细地址"><input aria-label="详细地址" autoComplete="off" value={form.addressDetail} onChange={(e) => onChange("addressDetail", e.target.value)} /></Field>
    </div>
    {!!form.attachmentFileIds.length && <div className="alert alert-info">当前草稿已有 {form.attachmentFileIds.length} 个正式附件；仅可从服务端明文草稿恢复，不接受手填 file_id。</div>}
    <FormalFileUploadField
      purpose="request_attachment"
      bindingKey={uploadBindingKey}
      label="选择并上传需求附件"
      client={uploadClient}
      multiple
      disabled={busy}
      onAvailableChange={onUploadedFiles}
      onBlockingChange={onUploadBlocking}
    />
    <Field label="申请备注"><textarea aria-label="申请备注" value={form.note} onChange={(e) => onChange("note", e.target.value)} /></Field>

    <div className="line-items">
      <div className="line-title"><div><strong>完整物料明细</strong><span className="line-subtitle">至少一条；相同物料/需用日期/替代物料不可重复</span></div>
        <Button tone="secondary" icon={<Plus size={16} />} type="button" disabled={busy} onClick={onAddLine}>增加明细</Button>
      </div>
      {form.lines.map((line, index) => <div className="form-grid three" key={line.key} style={{ padding: 12, borderBottom: "1px solid #e8ebec" }}>
        <Field label={`正式物料 ${index + 1}`}>
          <button className="table-action" type="button" aria-label={`选择正式物料 ${index + 1}`} disabled={busy} onClick={() => onChooseMaterial(line.key, "material")}>{line.material ? materialLabel(line.material) : "从正式目录选择物料"}</button>
        </Field>
        <Field label={`申请数量 ${index + 1}`} hint="必须为三位小数"><input aria-label={`申请数量 ${index + 1}`} inputMode="decimal" value={line.requestedQty} onChange={(e) => onLineChange(line.key, "requestedQty", e.target.value)} /></Field>
        <Field label={`需用日期 ${index + 1}`}><input aria-label={`需用日期 ${index + 1}`} type="date" value={line.requiredDate} onChange={(e) => onLineChange(line.key, "requiredDate", e.target.value)} /></Field>
        <Field label={`建议替代物料 ${index + 1}`}>
          <div className="form-actions">
            <button className="table-action" type="button" aria-label={`选择建议替代物料 ${index + 1}`} disabled={busy} onClick={() => onChooseMaterial(line.key, "substitute")}>{line.substituteMaterial ? materialLabel(line.substituteMaterial) : "从正式目录选择（可空）"}</button>
            {line.substituteMaterial && <button className="table-action" type="button" disabled={busy} onClick={() => onClearSubstitute(line.key)}>清除替代料</button>}
          </div>
        </Field>
        <Field label={`明细备注 ${index + 1}`}><input aria-label={`明细备注 ${index + 1}`} value={line.note} onChange={(e) => onLineChange(line.key, "note", e.target.value)} /></Field>
        <Button tone="quiet" icon={<Trash2 size={16} />} type="button" disabled={busy || form.lines.length === 1} onClick={() => onRemoveLine(line.key)}>删除该行</Button>
      </div>)}
    </div>

    <div className="alert alert-info">详细地址和联系电话仅保留在当前页面内存中；不会写入 localStorage、日志或列表/详情投影。</div>
    <div className="form-actions">
      <Button tone="secondary" disabled={busy} onClick={onCancel}>取消</Button>
      <Button disabled={busy || uploadBlocking || form.workOrderUnavailable} onClick={onSave}>{busy ? "正在确认结果" : form.workOrderUnavailable ? "请先处理不可选工单" : uploadBlocking ? "附件尚未确认 available" : mode.kind === "create" ? "保存草稿" : "保存修改"}</Button>
    </div>
  </div>;
}

function WorkOrderPicker({
  picker,
  onQuery,
  onSearch,
  onLoadMore,
  onSelect,
  onClose,
}: {
  picker: WorkOrderPickerState;
  onQuery: (value: string) => void;
  onSearch: () => void;
  onLoadMore: () => void;
  onSelect: (item: MaterialRequestWorkOrderOption) => void;
  onClose: () => void;
}) {
  return <Modal title="选择本人当前有效 OAM 工单" wide onClose={onClose}>
    <div className="form-stack">
      <div className="form-actions">
        <input
          aria-label="OAM 工单检索词"
          placeholder="输入工单编号"
          value={picker.query}
          onChange={(event) => onQuery(event.target.value)}
        />
        <Button disabled={picker.loading} onClick={onSearch}>搜索正式工单投影</Button>
      </div>
      {picker.error && <div className="alert alert-error">{picker.error}；不会回退旧 `/work-orders`、接受手填 UUID 或猜测工单。</div>}
      {picker.loading && <Loading label="正在读取本人当前有效 OAM 工单投影" />}
      {!picker.loading && !picker.error && !picker.items.length && <Empty title="没有可选 OAM 工单" detail="仅展示本人 pending/active 且属于当前区域的正式投影" />}
      {!!picker.items.length && <div className="table-wrap"><table>
        <thead><tr><th>工单编号</th><th>状态</th><th>最小来源证据</th><th>源更新时间</th><th>同步时间</th><th>选择</th></tr></thead>
        <tbody>{picker.items.map((item) => <tr key={item.work_order_id}>
          <td><strong className="mono">{item.work_order_no}</strong></td>
          <td>{item.status === "active" ? "进行中" : "待处理"}</td>
          <td><strong>{item.source_system_code}</strong><span className="cell-subtitle mono">{item.source_external_id}</span><span className="cell-subtitle">版本 {item.source_version} · fresh</span></td>
          <td>{workOrderSourceTime(item.source_updated_at)}</td>
          <td>{workOrderSourceTime(item.synced_at)}</td>
          <td><button className="table-action" type="button" aria-label={`选择工单 ${item.work_order_no}`} disabled={picker.loading || Boolean(picker.error)} onClick={() => onSelect(item)}>选择</button></td>
        </tr>)}</tbody>
      </table></div>}
      {picker.nextAfterId && <div className="form-actions"><Button tone="secondary" disabled={picker.loading} onClick={onLoadMore}>加载更多正式工单</Button></div>}
    </div>
  </Modal>;
}

function MaterialPicker({
  picker,
  onQuery,
  onSearch,
  onLoadMore,
  onSelect,
  onClose,
}: {
  picker: MaterialPickerState;
  onQuery: (value: string) => void;
  onSearch: () => void;
  onLoadMore: () => void;
  onSelect: (item: FormalMaterialCatalogItem) => void;
  onClose: () => void;
}) {
  return <Modal title={picker.target === "material" ? "选择正式物料" : "选择建议替代物料"} wide onClose={onClose}>
    <div className="form-stack">
      <div className="form-actions">
        <input aria-label="物料目录检索词" placeholder="SKU / 名称 / 规格" value={picker.query} disabled={picker.loading} onChange={(event) => onQuery(event.target.value)} />
        <Button disabled={picker.loading} onClick={onSearch}>搜索正式目录</Button>
      </div>
      {picker.error && <div className="alert alert-error">{picker.error}；不会回退旧物料接口或接受手填 UUID。</div>}
      {picker.loading && <Loading label="正在读取正式物料目录" />}
      {!picker.loading && !picker.error && !picker.items.length && <Empty title="没有匹配的正式物料" detail="请调整 SKU、名称或规格检索词" />}
      {!!picker.items.length && <div className="table-wrap"><table>
        <thead><tr><th>SKU / 名称</th><th>规格</th><th>单位</th><th>追踪策略</th><th>数量精度</th><th>选择</th></tr></thead>
        <tbody>{picker.items.map((item) => <tr key={item.material_id}>
          <td><strong>{item.sku_code}</strong><span className="cell-subtitle">{item.name}</span></td>
          <td>{item.specification || "无规格"}</td><td>{item.base_unit}</td>
          <td>{trackingLabel(item.tracking_mode)}</td>
          <td>{item.quantity_scale} 位 / {item.allow_fraction ? "允许小数" : "仅整数"}</td>
          <td><button className="table-action" type="button" onClick={() => onSelect(item)}>选择</button></td>
        </tr>)}</tbody>
      </table></div>}
      {picker.nextAfterId && <div className="form-actions"><Button tone="secondary" disabled={picker.loading} onClick={onLoadMore}>加载更多正式物料</Button></div>}
    </div>
  </Modal>;
}

function ApprovalProcessForm({
  process,
  detail,
  busy,
  uploadClient,
  uploadBindingKey,
  uploadBlocking,
  onAction,
  onLine,
  onField,
  onUploadBlocking,
  onCancel,
  onSubmit,
}: {
  process: ApprovalProcessState;
  detail: MaterialRequestDetail;
  busy: boolean;
  uploadClient: FormalFileUploadClient;
  uploadBindingKey: string;
  uploadBlocking: boolean;
  onAction: (action: ApprovalProcessState["action"]) => void;
  onLine: (requestLineId: string, field: "decisionQty" | "reason", value: string) => void;
  onField: <K extends keyof ApprovalProcessState>(field: K, value: ApprovalProcessState[K]) => void;
  onUploadBlocking: (blocking: boolean) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const locked = busy || Boolean(process.pendingMessage);
  const evidence = detail.approval_instance?.external_evidence_summaries?.find(
    (item) => item.registration_id === process.registrationId,
  );
  return <Modal title={process.kind === "internal"
    ? "处理当前内部审批"
    : process.kind === "external_registration"
      ? "登记星星总部审批证据"
      : "独立复核外部审批证据"} wide onClose={onCancel}>
    <div className="form-stack" aria-label="正式需求审批处理表单">
      <div className="alert alert-warning">本操作只更新审批事实，不代表分配、占用、出库、发货、签收、OAM收货、个人仓入库、通知或对账完成。</div>
      {process.pendingMessage && <div className="alert alert-warning">{process.pendingMessage}</div>}
      {process.error && <div className="alert alert-error">{process.error}</div>}

      {process.kind !== "external_verification" && <>
        <div className="form-actions" aria-label="审批动作选择">
          {(["approve", "return", "reject"] as const).map((action) => <Button key={action} tone={process.action === action ? "primary" : "secondary"} disabled={locked || (process.kind === "internal" && !detail.allowed_actions.includes(action))} onClick={() => onAction(action)}>{action === "approve" ? "逐行批准" : action === "return" ? "逐行退回" : "整单驳回"}</Button>)}
        </div>
        {process.action !== "reject" && process.lines.map((line, index) => <div className="form-grid three" key={line.requestLineId}>
          <Field label={`明细 ${index + 1} 当前级输入`}><input readOnly value={line.inputQty} /></Field>
          <Field label={process.action === "approve" ? `明细 ${index + 1} 批准数量` : `明细 ${index + 1} 请求重审数量`}><input aria-label={process.action === "approve" ? `批准数量 ${index + 1}` : `请求重审数量 ${index + 1}`} inputMode="decimal" disabled={locked} value={line.decisionQty} onChange={(event) => onLine(line.requestLineId, "decisionQty", event.target.value)} /></Field>
          <Field label={`明细 ${index + 1} 原因`}><input aria-label={`审批明细原因 ${index + 1}`} disabled={locked} value={line.reason} onChange={(event) => onLine(line.requestLineId, "reason", event.target.value)} /></Field>
        </div>)}
      </>}

      {process.kind === "external_registration" && <>
        <div className="alert alert-warning">外部审批证据必须以独立用途上传并严格确认 available；不能复用需求附件、手填 file_id 或调用旧 `/media`。</div>
        <FormalFileUploadField
          purpose="external_approval_evidence"
          bindingKey={uploadBindingKey}
          label="选择并上传星星总部审批证据"
          client={uploadClient}
          disabled={locked}
          onAvailableChange={(files) => onField("evidenceFileId", files[0]?.file_id || "")}
          onBlockingChange={onUploadBlocking}
        />
        <div className="form-grid three">
          <Field label="星星总部审批人"><input aria-label="星星总部审批人" disabled={locked} value={process.externalApproverName} onChange={(event) => onField("externalApproverName", event.target.value)} /></Field>
          <Field label="外部审批参考号"><input aria-label="外部审批参考号" disabled={locked} value={process.externalReferenceNo} onChange={(event) => onField("externalReferenceNo", event.target.value)} /></Field>
          <Field label="外部决定时间（含时区 ISO8601）"><input aria-label="外部决定时间（含时区 ISO8601）" disabled={locked} value={process.externalDecidedAt} onChange={(event) => onField("externalDecidedAt", event.target.value)} /></Field>
        </div>
      </>}

      {process.kind === "external_verification" && <>
        {!evidence ? <div className="alert alert-error">待复核证据锚点已失效，禁止提交。</div> : <dl className="detail-grid">
          <div><dt>登记号</dt><dd>{evidence.registration_no}</dd></div>
          <div><dt>证据文件</dt><dd className="mono">{evidence.evidence_file_id}</dd></div>
          <div><dt>外部动作</dt><dd>{evidence.external_action}</dd></div>
          <div><dt>外部审批人</dt><dd>{evidence.external_approver_name_masked}</dd></div>
        </dl>}
        <div className="form-actions">
          <Button disabled={locked} tone={process.verificationDecision === "accept" ? "primary" : "secondary"} onClick={() => onField("verificationDecision", "accept")}>复核接受</Button>
          <Button disabled={locked} tone={process.verificationDecision === "reject" ? "primary" : "secondary"} onClick={() => onField("verificationDecision", "reject")}>复核拒绝</Button>
        </div>
        <div className="alert alert-info">复核必须由不同的蔚来总部管理员执行；服务端将独立校验，客户端不代替该约束。</div>
      </>}

      <Field label="处理意见"><textarea aria-label="审批处理意见" disabled={locked} value={process.comment} onChange={(event) => onField("comment", event.target.value)} /></Field>
      <div className="form-actions"><Button tone="secondary" disabled={busy} onClick={onCancel}>取消</Button><Button disabled={busy || uploadBlocking || (process.kind === "external_registration" && !process.evidenceFileId) || (!evidence && process.kind === "external_verification")} onClick={onSubmit}>{busy ? "正在精确回读" : uploadBlocking ? "证据尚未确认 available" : "确认处理"}</Button></div>
    </div>
  </Modal>;
}

function LifecycleProcessForm({
  process,
  busy,
  onReason,
  onLineReason,
  onCancel,
  onSubmit,
}: {
  process: LifecycleProcessState;
  busy: boolean;
  onReason: (reason: string) => void;
  onLineReason: (requestLineId: string, reason: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const pending = Boolean(process.pendingMessage);
  const cancelling = process.kind === "cancel";
  return <Modal title={cancelling ? "安全取消需求" : "撤回需求"} onClose={onCancel}>
    <div className="form-stack" aria-label={cancelling ? "需求安全取消确认" : "需求撤回确认"}>
      <div className="alert alert-warning">
        {cancelling
          ? "安全取消仅在服务端确认不存在分配、占用、出库、发货、物流、入库、通知、对账或其他补偿事实时可执行；本页不会自行释放或推断任何下游事实。"
          : "撤回只终止当前申请与审批轴，不代表或改写分配、占用、出库、发货、物流签收、OAM收货、RSC/个人仓入库、通知送达或对账同步。"}
      </div>
      <p><strong>对象：</strong><span className="mono">{process.requestId}</span> · 版本 v{process.requestVersion}</p>
      {process.pendingMessage && <div className="alert alert-warning">{process.pendingMessage}</div>}
      {process.error && <div className="alert alert-error">{process.error}</div>}
      <Field label={cancelling ? "整单取消原因" : "撤回原因"}>
        <textarea
          aria-label={cancelling ? "整单取消原因" : "撤回原因"}
          disabled={busy || pending}
          value={process.reason}
          onChange={(event) => onReason(event.target.value)}
        />
      </Field>
      {cancelling && (process.lines.length ? <div className="table-wrap"><table>
        <thead><tr><th>需求明细</th><th>完整取消数量（只读）</th><th>逐行取消原因</th></tr></thead>
        <tbody>{process.lines.map((line, index) => <tr key={line.requestLineId}>
          <td>{index + 1}<span className="cell-subtitle mono">{line.requestLineId}</span></td>
          <td className="mono">{line.cancelledQty}</td>
          <td><input
            aria-label={`取消明细原因 ${index + 1}`}
            disabled={busy || pending}
            value={line.reason}
            onChange={(event) => onLineReason(line.requestLineId, event.target.value)}
          /></td>
        </tr>)}</tbody>
      </table></div> : <div className="alert alert-info">当前修订没有最终批准数量，取消明细按后端契约提交空数组。</div>)}
      <div className="form-actions">
        <Button tone="secondary" disabled={busy} onClick={onCancel}>返回检查</Button>
        <Button disabled={busy} onClick={onSubmit}>
          {busy ? "正在精确回读" : pending ? "按原请求坐标重试" : cancelling ? "确认安全取消" : "确认撤回"}
        </Button>
      </div>
    </div>
  </Modal>;
}

export default function FormalMaterialRequestsPage({
  adapter,
  fileUploadClient = defaultFormalFileUploadClient,
  lifecycleRecoveryStore,
  supplyRecoveryStore,
  allocationRecoveryStore,
  reservationRecoveryStore,
  releaseRecoveryStore,
  pickRecoveryStore,
  outboundRecoveryStore,
  shipmentRecoveryStore,
}: {
  adapter: FormalMaterialRequestAdapter;
  fileUploadClient?: FormalFileUploadClient;
  lifecycleRecoveryStore?: MaterialRequestLifecycleRecoveryStore;
  supplyRecoveryStore?: SupplyRecoveryStore;
  allocationRecoveryStore?: AllocationRecoveryStore;
  reservationRecoveryStore?: ReservationRecoveryStore;
  releaseRecoveryStore?: ReleaseStore;
  pickRecoveryStore?: PickStore;
  outboundRecoveryStore?: OutboundStore;
  shipmentRecoveryStore?: ShipmentStore;
}) {
  const recoveryStore = useRef(
    lifecycleRecoveryStore ?? createMaterialRequestLifecycleRecoveryStore(),
  );
  const initialRecoveryRead = useRef(recoveryStore.current.read());
  const supplyStore = useRef(supplyRecoveryStore ?? createSupplyRecoveryStore());
  const [supplyBlocked, setSupplyBlocked] = useState(() => supplyRecoveryBlocked(supplyStore.current.read()));
  const allocationStore = useRef(allocationRecoveryStore ?? createAllocationRecoveryStore());
  const [allocationBlocked, setAllocationBlocked] = useState(
    () => allocationStore.current.read().kind !== "missing",
  );
  const reservationStore = useRef(reservationRecoveryStore ?? createReservationRecoveryStore());
  const [reservationBlocked, setReservationBlocked] = useState(() => reservationStore.current.read().kind !== "missing");
  const reservationBlockingRef = useRef(reservationBlocked);
  const shipmentStore = useRef(shipmentRecoveryStore ?? createShipmentStore());
  const [shipmentBlocked, setShipmentBlocked] = useState(() => shipmentStore.current.read().kind !== "missing");
  const shipmentBlockingRef = useRef(shipmentBlocked);
  function onShipmentBlocking(value: boolean) { shipmentBlockingRef.current = value; setShipmentBlocked(value); }
  function shipmentWriteBlocked() { return shipmentBlockingRef.current || shipmentStore.current.read().kind !== "missing"; }
  const outboundStore = useRef(outboundRecoveryStore ?? createOutboundStore());
  const [outboundBlocked, setOutboundBlocked] = useState(() => outboundStore.current.read().kind !== "missing");
  const outboundBlockingRef = useRef(outboundBlocked);
  function onOutboundBlocking(value: boolean) { outboundBlockingRef.current = value; setOutboundBlocked(value); }
  function outboundWriteBlocked() { return shipmentWriteBlocked() || outboundBlockingRef.current || outboundStore.current.read().kind !== "missing"; }
  const pickStore = useRef(pickRecoveryStore ?? createPickStore());
  const [pickBlocked, setPickBlocked] = useState(() => pickStore.current.read().kind !== "missing");
  const pickBlockingRef = useRef(pickBlocked);
  function onPickBlocking(value: boolean) { pickBlockingRef.current = value; setPickBlocked(value); }
  function pickWriteBlocked() { return outboundWriteBlocked() || pickBlockingRef.current || pickStore.current.read().kind !== "missing"; }
  const releaseStore = useRef(releaseRecoveryStore ?? createReleaseStore());
  const [releaseBlocked, setReleaseBlocked] = useState(() => releaseStore.current.read().kind !== "missing");
  const releaseBlockingRef = useRef(releaseBlocked);
  function onReleaseBlocking(value: boolean) { releaseBlockingRef.current = value; setReleaseBlocked(value); }
  function releaseWriteBlocked() { return pickWriteBlocked() || releaseBlockingRef.current || releaseStore.current.read().kind !== "missing"; }
  const supplyBlockingRef = useRef(supplyBlocked);
  const allocationBlockingRef = useRef(allocationBlocked);
  function onReservationBlocking(value: boolean) { reservationBlockingRef.current = value; setReservationBlocked(value); }
  function onSupplyBlocking(value: boolean) { supplyBlockingRef.current = value; setSupplyBlocked(value); }
  function onAllocationBlocking(value: boolean) { allocationBlockingRef.current = value; setAllocationBlocked(value); }
  function reservationWriteBlocked() { return reservationBlockingRef.current || reservationStore.current.read().kind !== "missing" || releaseWriteBlocked(); }
  const [access, setAccess] = useState<FormalMaterialRequestAccess | null>(null);
  const [items, setItems] = useState<MaterialRequestSummary[]>([]);
  const [nextAfterId, setNextAfterId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [baseBusy, setBaseBusy] = useState(false);
  const baseBusyRef = useRef(false);
  function setBusy(value: boolean) { baseBusyRef.current = value; setBaseBusy(value); }
  const busy = shipmentBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || outboundBlocked;
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<MaterialRequestDetail | null>(null);
  const [formMode, setFormMode] = useState<FormMode | null>(null);
  const lineKey = useRef(1);
  const [form, setForm] = useState<DraftFormState>(() => emptyForm(1));
  const [formError, setFormError] = useState("");
  const [pendingMessage, setPendingMessage] = useState("");
  const [submitConfirm, setSubmitConfirm] = useState(false);
  const [workOrderPicker, setWorkOrderPicker] = useState<WorkOrderPickerState | null>(null);
  const [materialPicker, setMaterialPicker] = useState<MaterialPickerState | null>(null);
  const [approvalProcess, setApprovalProcess] = useState<ApprovalProcessState | null>(null);
  const [lifecycleProcess, setLifecycleProcess] = useState<LifecycleProcessState | null>(null);
  const [lifecycleRecovery, setLifecycleRecovery] = useState<LifecycleRecoveryView>(
    () => recoveryView(initialRecoveryRead.current),
  );
  const [draftUploadFiles, setDraftUploadFiles] = useState<readonly AvailableFormalFile[]>([]);
  const [draftUploadBlocking, setDraftUploadBlocking] = useState(false);
  const [externalUploadBlocking, setExternalUploadBlocking] = useState(false);
  const accessRef = useRef<FormalMaterialRequestAccess | null>(access);
  accessRef.current = access;
  const generation = useRef(0);
  const editRecoveryGeneration = useRef(0);
  const workOrderPickerGeneration = useRef(0);
  const pickerGeneration = useRef(0);
  const createRegistry = useRef(new MaterialRequestCreateIntentRegistry());
  const mutationRegistry = useRef(new MaterialRequestIntentRegistry());
  const lifecycleRecoveryInFlight = useRef<Readonly<{
    xRequestId: string;
    promise: ReturnType<typeof recoverMaterialRequestLifecycleCommand>;
  }> | null>(null);

  const lifecycleWritesBlocked = lifecycleRecovery.phase === "checking"
    || lifecycleRecovery.phase === "blocked" || shipmentBlocked || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || outboundBlocked;
  const draftWritePending = Boolean(
    formMode?.kind === "create"
      ? createRegistry.current.get()
      : formMode?.kind === "edit"
        ? mutationRegistry.current.get(formMode.requestId)
        : undefined,
  );

  const nextLineKey = useCallback(() => {
    lineKey.current += 1;
    return lineKey.current;
  }, []);

  const clearRawForm = useCallback(() => {
    editRecoveryGeneration.current += 1;
    setForm(emptyForm(nextLineKey()));
    setFormMode(null);
    setFormError("");
    setPendingMessage("");
    workOrderPickerGeneration.current += 1;
    setWorkOrderPicker(null);
    setMaterialPicker(null);
    setDraftUploadFiles([]);
    setDraftUploadBlocking(false);
  }, [nextLineKey]);

  const cancelRawForm = useCallback(() => {
    const createPending = createRegistry.current.get();
    const updatePending = formMode?.kind === "edit"
      ? mutationRegistry.current.get(formMode.requestId)
      : undefined;
    if (createPending || updatePending) {
      setFormError("当前写入结果尚未确认，必须保留原内容与坐标以便精确重试或人工核验");
      return;
    }
    clearRawForm();
  }, [clearRawForm, formMode]);

  const loadList = useCallback(async (afterId: string | null = null) => {
    const currentGeneration = generation.current;
    setLoading(true);
    setError("");
    try {
      const page = validateMaterialRequestPage(await adapter.list(afterId));
      if (currentGeneration !== generation.current) return;
      setItems((current) => {
        if (afterId === null) return page.items;
        if (page.items.some((row) => current.some((item) => item.request_id === row.request_id))) {
          throw new Error("正式需求跨页返回重复申请，已停止推进游标");
        }
        return [...current, ...page.items];
      });
      setNextAfterId(page.next_after_id);
    } catch (err) {
      if (currentGeneration === generation.current) setError(showError(err));
    } finally {
      if (currentGeneration === generation.current) setLoading(false);
    }
  }, [adapter]);

  const recoverStoredLifecycle = useCallback(async (
    sentinel: MaterialRequestLifecycleSentinel,
    currentGeneration: number,
  ): Promise<FormalMaterialRequestAccess | null> => {
    setLifecycleRecovery({
      phase: "checking",
      message: `正在核验生命周期请求坐标 ${sentinel.x_request_id}；核验完成前不会生成新坐标。`,
      retryable: false,
    });
    let inFlight = lifecycleRecoveryInFlight.current;
    if (!inFlight || inFlight.xRequestId !== sentinel.x_request_id) {
      const promise = recoverMaterialRequestLifecycleCommand({
        adapter,
        store: recoveryStore.current,
        sentinel,
      });
      inFlight = Object.freeze({ xRequestId: sentinel.x_request_id, promise });
      lifecycleRecoveryInFlight.current = inFlight;
      void promise.finally(() => {
        if (lifecycleRecoveryInFlight.current?.promise === promise) {
          lifecycleRecoveryInFlight.current = null;
        }
      });
    }
    const outcome = await inFlight.promise;
    if (currentGeneration !== generation.current) return null;
    if (outcome.access) setAccess(outcome.access);
    if (outcome.kind === "confirmed") {
      setDetail(outcome.detail);
      setLifecycleProcess(null);
      setLifecycleRecovery({
        phase: "recovered",
        message: `${outcome.command.action === "withdraw" ? "撤回" : "取消"}命令已从服务端事实恢复，并完成需求版本、修订、审批锚点及十状态轴精确回读。`,
        retryable: false,
      });
      setNotice("生命周期命令已恢复确认；分配、占用、出库、发货、物流签收、OAM收货、RSC/个人仓入库、通知送达和对账同步仍是独立状态。");
      return outcome.access;
    }
    setLifecycleRecovery({ phase: "blocked", message: outcome.message, retryable: true });
    return outcome.access;
  }, [adapter]);

  useEffect(() => {
    generation.current += 1;
    editRecoveryGeneration.current += 1;
    const currentGeneration = generation.current;
    setApprovalProcess(null);
    setLifecycleProcess(null);
    setExternalUploadBlocking(false);
    setDraftUploadFiles([]);
    setDraftUploadBlocking(false);
    setBusy(false);
    setWorkOrderPicker(null);
    setFormMode(null);
    setForm(emptyForm(nextLineKey()));
    setLoading(true);
    void (async () => {
      try {
        const stored = recoveryStore.current.read();
        let checked: FormalMaterialRequestAccess | null;
        if (stored.kind === "valid") {
          checked = await recoverStoredLifecycle(stored.value, currentGeneration);
        } else {
          setLifecycleRecovery(recoveryView(stored));
          checked = validateFormalMaterialRequestAccess(await adapter.loadAccess());
        }
        if (currentGeneration !== generation.current) return;
        if (!checked) {
          setAccess(null);
          setError("待核验生命周期命令的登录身份或新鲜授权不可用，页面已失败关闭");
          setLoading(false);
          return;
        }
        setAccess(checked);
        if (!checked.can_read) {
          setError("当前主体没有正式需求读取权限，页面已失败关闭");
          setLoading(false);
          return;
        }
        await loadList();
      } catch (err) {
        if (currentGeneration === generation.current) {
          setAccess(null);
          setItems([]);
          setError(showError(err));
          setLoading(false);
        }
      }
    })();
    return () => {
      generation.current += 1;
      editRecoveryGeneration.current += 1;
      workOrderPickerGeneration.current += 1;
      setForm(emptyForm(nextLineKey()));
    };
  }, [adapter, loadList, nextLineKey, recoverStoredLifecycle]);

  async function retryStoredLifecycleRecovery(): Promise<void> {
    const stored = recoveryStore.current.read();
    if (stored.kind !== "valid") {
      setLifecycleRecovery(recoveryView(stored));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await recoverStoredLifecycle(stored.value, generation.current);
    } finally {
      setBusy(false);
    }
  }

  const openDetail = useCallback(async (requestId: string) => {
    editRecoveryGeneration.current += 1;
    setBusy(true);
    setError("");
    setNotice("");
    setApprovalProcess(null);
    setLifecycleProcess(null);
    setExternalUploadBlocking(false);
    try {
      setDetail(validateMaterialRequestDetail(await adapter.detail(requestId), requestId));
    } catch (err) {
      setDetail(null);
      setError(showError(err));
    } finally {
      setBusy(false);
    }
  }, [adapter]);

  function hasPendingDraftWrite(): boolean {
    if (formMode?.kind === "create") return Boolean(createRegistry.current.get());
    if (formMode?.kind === "edit") {
      return Boolean(mutationRegistry.current.get(formMode.requestId));
    }
    return false;
  }

  function canUseWorkOrderOptions(): boolean {
    return Boolean(access && (access.can_create || formMode?.kind === "edit"));
  }

  function startCreate(): void {
    if (busy || reservationWriteBlocked()) {
      setError("当前读取或写入尚未结束，不能开始新的需求草稿");
      return;
    }
    if (!access?.can_create) {
      setError("当前主体没有正式需求创建权限，页面已失败关闭");
      return;
    }
    if (!access.can_read_material_catalog) {
      setError("当前主体没有正式物料目录读取权限，禁止新建需求或回退旧目录");
      return;
    }
    editRecoveryGeneration.current += 1;
    setDetail(null);
    setForm(emptyForm(nextLineKey()));
    setFormMode({ kind: "create" });
    setFormError("");
    setPendingMessage("");
    setDraftUploadFiles([]);
    setDraftUploadBlocking(false);
  }

  async function readWorkOrderPickerPage(
    query: string,
    afterId: string | null,
    append: boolean,
  ): Promise<void> {
    if (!access || !canUseWorkOrderOptions()) {
      setWorkOrderPicker((current) => current ? {
        ...current,
        items: [],
        nextAfterId: null,
        cursorHistory: [],
        loading: false,
        error: "当前授权不能读取正式需求工单选项",
      } : current);
      return;
    }
    const currentGeneration = ++workOrderPickerGeneration.current;
    const currentPicker = workOrderPicker;
    if (
      append
      && (!afterId || !currentPicker || currentPicker.cursorHistory.includes(afterId))
    ) {
      setWorkOrderPicker((current) => current ? {
        ...current,
        items: [],
        nextAfterId: null,
        cursorHistory: [],
        loading: false,
        error: "正式工单选项游标重复或缺失，已失败关闭",
      } : current);
      return;
    }
    setWorkOrderPicker((current) => current ? {
      ...current,
      items: append ? current.items : [],
      nextAfterId: append ? current.nextAfterId : null,
      cursorHistory: append ? current.cursorHistory : [],
      loading: true,
      error: "",
    } : current);
    try {
      const page = validateMaterialRequestWorkOrderOptionPage(
        await adapter.listWorkOrderOptions(query, afterId),
        access,
      );
      if (currentGeneration !== workOrderPickerGeneration.current) return;
      setWorkOrderPicker((current) => {
        if (!current) return null;
        const items = append ? [...current.items, ...page.items] : [...page.items];
        const cursorHistory = append && afterId
          ? [...current.cursorHistory, afterId]
          : [];
        const invalidPage = new Set(items.map((item) => item.work_order_id)).size !== items.length
          || new Set(items.map((item) => item.work_order_no)).size !== items.length
          || (page.next_after_id !== null && cursorHistory.includes(page.next_after_id));
        if (invalidPage) {
          return {
            ...current,
            items: [],
            nextAfterId: null,
            cursorHistory: [],
            loading: false,
            error: "正式工单选项跨页重复或游标循环，已失败关闭",
          };
        }
        return {
          ...current,
          items,
          nextAfterId: page.next_after_id,
          cursorHistory,
          loading: false,
          error: "",
        };
      });
    } catch (error) {
      if (currentGeneration !== workOrderPickerGeneration.current) return;
      setWorkOrderPicker((current) => current ? {
        ...current,
        items: [],
        nextAfterId: null,
        cursorHistory: [],
        loading: false,
        error: showError(error),
      } : current);
    }
  }

  function openWorkOrderPicker(): void {
    if (
      !formMode
      || busy
      || hasPendingDraftWrite()
      || !canUseWorkOrderOptions()
    ) {
      setFormError("正式工单选项当前不可用；未确认写入期间禁止改动工单，也不会接受手填 UUID 或回退旧接口");
      return;
    }
    workOrderPickerGeneration.current += 1;
    setWorkOrderPicker({
      query: "",
      items: [],
      nextAfterId: null,
      cursorHistory: [],
      loading: false,
      error: "",
    });
    queueMicrotask(() => void readWorkOrderPickerPage("", null, false));
  }

  function updateWorkOrderPickerQuery(query: string): void {
    if (!workOrderPicker || query === workOrderPicker.query) return;
    workOrderPickerGeneration.current += 1;
    setWorkOrderPicker((current) => current ? {
      ...current,
      query,
      items: [],
      nextAfterId: null,
      cursorHistory: [],
      loading: false,
      error: "",
    } : current);
  }

  function chooseWorkOrder(item: MaterialRequestWorkOrderOption): void {
    if (hasPendingDraftWrite()) {
      workOrderPickerGeneration.current += 1;
      setWorkOrderPicker(null);
      setFormError("写入结果尚未确认，禁止改动关联工单");
      return;
    }
    if (!workOrderPicker || workOrderPicker.loading || workOrderPicker.error) return;
    const selected = workOrderPicker.items.find(
      (candidate) => candidate.work_order_id === item.work_order_id,
    );
    if (!selected) {
      setFormError("工单选择项已失效，请重新打开正式选择器");
      return;
    }
    setForm((current) => ({
      ...current,
      workOrder: selected,
      workOrderUnavailable: false,
    }));
    workOrderPickerGeneration.current += 1;
    setWorkOrderPicker(null);
    setFormError("");
  }

  function clearWorkOrder(): void {
    if (!formMode || busy || hasPendingDraftWrite()) {
      setFormError("写入结果尚未确认，禁止清除关联工单");
      return;
    }
    workOrderPickerGeneration.current += 1;
    setWorkOrderPicker(null);
    setForm((current) => ({
      ...current,
      workOrder: null,
      workOrderUnavailable: false,
    }));
    setFormError("");
  }

  async function resolveDraftWorkOrder(
    draft: MaterialRequestDraftInput,
  ): Promise<DraftWorkOrderResolution> {
    if (!draft.work_order_id) return Object.freeze({ item: null, unavailable: false });
    if (!access) throw new Error("当前身份或授权锚点不可用，已停止编辑");
    try {
      const resolved = validateMaterialRequestWorkOrderOptionDetail(
        await adapter.workOrderOptionDetail(draft.work_order_id),
        {
          person_id: access.person_id,
          authorization_version: access.authorization_version,
          work_order_id: draft.work_order_id,
        },
      );
      return Object.freeze({ item: resolved.item, unavailable: false });
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.responseReceived) {
        return Object.freeze({ item: null, unavailable: true });
      }
      throw error;
    }
  }

  async function readMaterialPickerPage(
    query: string,
    afterId: string | null,
    append: boolean,
  ): Promise<void> {
    if (!access?.can_read_material_catalog) {
      setMaterialPicker((current) => current ? {
        ...current,
        loading: false,
        error: "当前授权不包含正式物料目录读取权限",
      } : current);
      return;
    }
    const currentGeneration = ++pickerGeneration.current;
    setMaterialPicker((current) => current ? { ...current, loading: true, error: "" } : current);
    try {
      const page = validateFormalMaterialCatalogPage(await adapter.listMaterials(query, afterId));
      if (currentGeneration !== pickerGeneration.current) return;
      setMaterialPicker((current) => {
        if (!current) return null;
        const items = append ? [...current.items, ...page.items] : page.items;
        if (new Set(items.map((item) => item.material_id)).size !== items.length
            || new Set(items.map((item) => item.sku_code)).size !== items.length) {
          return { ...current, items: [], nextAfterId: null, loading: false, error: "正式物料目录跨页返回重复物料，已失败关闭" };
        }
        return { ...current, items, nextAfterId: page.next_after_id, loading: false, error: "" };
      });
    } catch (error) {
      if (currentGeneration !== pickerGeneration.current) return;
      setMaterialPicker((current) => current ? {
        ...current,
        items: [],
        nextAfterId: null,
        loading: false,
        error: showError(error),
      } : current);
    }
  }

  function openMaterialPicker(lineKey: number, target: MaterialPickerState["target"]): void {
    const line = form.lines.find((item) => item.key === lineKey);
    if (!line || !access?.can_read_material_catalog) {
      setFormError("正式物料目录权限不可用，禁止手填物料 UUID 或回退旧目录");
      return;
    }
    pickerGeneration.current += 1;
    setMaterialPicker({
      lineKey,
      target,
      query: "",
      items: [],
      nextAfterId: null,
      loading: false,
      error: "",
    });
    queueMicrotask(() => void readMaterialPickerPage("", null, false));
  }

  function chooseMaterial(item: FormalMaterialCatalogItem): void {
    if (!materialPicker) return;
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line) => line.key !== materialPicker.lineKey ? line : {
        ...line,
        [materialPicker.target === "material" ? "material" : "substituteMaterial"]: item,
      }),
    }));
    pickerGeneration.current += 1;
    setMaterialPicker(null);
    setFormError("");
  }

  async function resolveDraftMaterials(draft: MaterialRequestDraftInput) {
    if (!access?.can_read_material_catalog) {
      throw new Error("当前授权不包含正式物料目录读取权限，已停止编辑");
    }
    const requiredIds = new Set(draft.lines.flatMap((line) => [
      line.material_id,
      ...(line.suggested_substitute_material_id ? [line.suggested_substitute_material_id] : []),
    ]));
    const found = new Map<string, FormalMaterialCatalogItem>();
    const seenIds = new Set<string>();
    const seenCursors = new Set<string>();
    let afterId: string | null = null;
    for (let pageNo = 0; pageNo < 100 && found.size < requiredIds.size; pageNo += 1) {
      const page = validateFormalMaterialCatalogPage(await adapter.listMaterials("", afterId));
      for (const item of page.items) {
        if (seenIds.has(item.material_id)) throw new Error("正式物料目录跨页重复，已停止编辑");
        seenIds.add(item.material_id);
        if (requiredIds.has(item.material_id)) found.set(item.material_id, item);
      }
      if (!page.next_after_id) break;
      if (seenCursors.has(page.next_after_id)) throw new Error("正式物料目录游标循环，已停止编辑");
      seenCursors.add(page.next_after_id);
      afterId = page.next_after_id;
    }
    if (found.size !== requiredIds.size) {
      throw new Error("草稿引用的物料不在当前正式活动目录中，已停止编辑");
    }
    return found;
  }

  async function startEdit(): Promise<void> {
    if (supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked()) return;
    if (!detail || !["draft", "returned"].includes(detail.states.request_status)
        || !detail.allowed_actions.includes("update")) return;
    const currentEditGeneration = ++editRecoveryGeneration.current;
    const currentPageGeneration = generation.current;
    const expectedAccess = access;
    const editIsCurrent = (): boolean => {
      const currentAccess = accessRef.current;
      return currentEditGeneration === editRecoveryGeneration.current
        && currentPageGeneration === generation.current
        && Boolean(expectedAccess)
        && currentAccess?.person_id === expectedAccess?.person_id
        && currentAccess?.authorization_version === expectedAccess?.authorization_version;
    };
    setBusy(true);
    setError("");
    try {
      const snapshot = validateFormalMaterialRequestEditableDraft(
        await adapter.loadDraftForEdit(detail.request_id),
        detail.request_id,
        detail.request_version,
      );
      if (!editIsCurrent()) return;
      const [materials, workOrder] = await Promise.all([
        resolveDraftMaterials(snapshot.draft),
        resolveDraftWorkOrder(snapshot.draft),
      ]);
      if (!editIsCurrent()) return;
      setForm(formFromDraft(snapshot.draft, nextLineKey, materials, workOrder));
      setFormMode({
        kind: "edit",
        requestId: snapshot.request_id,
        requestVersion: snapshot.request_version,
      });
      setDraftUploadFiles([]);
      setDraftUploadBlocking(false);
      setDetail(null);
      setFormError("");
      setPendingMessage("");
    } catch (err) {
      if (editIsCurrent()) setError(showError(err));
    } finally {
      if (editIsCurrent()) setBusy(false);
    }
  }

  function updateForm<K extends keyof DraftFormState>(key: K, value: DraftFormState[K]): void {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateLine(
    key: number,
    field: "requestedQty" | "requiredDate" | "note",
    value: string,
  ): void {
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line) => line.key === key ? { ...line, [field]: value } : line),
    }));
  }

  async function saveDraft(): Promise<void> {
    if (supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked()) return;
    if (!formMode) return;
    if (form.workOrderUnavailable) {
      setFormError("原关联工单当前不可选；必须明确清除或从正式列表重新选择后才能保存");
      return;
    }
    if (draftUploadBlocking) {
      setFormError("附件尚未完成 available 严格确认，已停止需求写入");
      return;
    }
    setBusy(true);
    setFormError("");
    setPendingMessage("");
    let responseValidated = false;
    try {
      const draft = draftFromForm(form, draftUploadFiles);
      if (formMode.kind === "create") {
        const intent = createRegistry.current.begin({ body: draft });
        setPendingMessage(`草稿创建结果确认中（${intent.client_draft_key}）；重试只复用原请求坐标。`);
        const result = validateMaterialRequestCreateResult(await adapter.createDraft(intent));
        responseValidated = true;
        const reread = validateMaterialRequestDetail(
          await adapter.detail(result.request_id),
          result.request_id,
        );
        if (!draftWriteMatches(result, reread, draft)) {
          throw new Error("创建响应、修订或工单绑定与详情回读不一致，写入仍待人工核验");
        }
        createRegistry.current.confirm(intent.client_draft_key, intent.signature);
        clearRawForm();
        setDetail(reread);
        setNotice("草稿已创建并完成精确详情回读；列表可刷新获取最新排序。");
      } else {
        const body = { ...draft, expected_version: formMode.requestVersion };
        const intent = mutationRegistry.current.begin({
          requestId: formMode.requestId,
          action: "update",
          path: `/v1/material-requests/${formMode.requestId}`,
          body,
          expectedVersion: formMode.requestVersion,
        });
        setPendingMessage(`草稿修改结果确认中（请求坐标 ${intent.headers["X-Request-ID"]}）；重试只复用原坐标。`);
        const result = validateMaterialRequestMutationResult(await adapter.mutate(intent), {
          requestId: formMode.requestId,
          action: "update",
          previousVersion: formMode.requestVersion,
        });
        responseValidated = true;
        const reread = validateMaterialRequestDetail(
          await adapter.detail(formMode.requestId),
          formMode.requestId,
        );
        if (!draftWriteMatches(result, reread, draft)) {
          throw new Error("修改响应、修订或工单绑定与详情回读不一致，写入仍待人工核验");
        }
        mutationRegistry.current.confirm(intent.request_id, intent.signature);
        clearRawForm();
        setDetail(reread);
        setNotice("草稿修改已完成并精确回读。");
      }
    } catch (err) {
      if (formMode.kind === "create") {
        const pending = createRegistry.current.get();
        if (!responseValidated && pending && isDefinitiveMaterialRequestRejection(err)) {
          createRegistry.current.clearDefinitiveRejection(pending.client_draft_key, pending.signature);
          setPendingMessage("");
        } else if (pending) {
          setPendingMessage(`创建结果仍未确认（${pending.client_draft_key}）；禁止修改内容后生成新坐标。`);
        }
      } else {
        const pending = mutationRegistry.current.get(formMode.requestId);
        if (!responseValidated && pending && isDefinitiveMaterialRequestRejection(err)) {
          mutationRegistry.current.clearDefinitiveRejection(pending.request_id, pending.signature);
          setPendingMessage("");
        } else if (pending) {
          setPendingMessage(`修改结果仍未确认（请求坐标 ${pending.headers["X-Request-ID"]}）；禁止同对象其他写入。`);
        }
      }
      setFormError(showError(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitRequest(): Promise<void> {
    if (supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked()) return;
    if (!detail || !["draft", "returned"].includes(detail.states.request_status)
        || !detail.allowed_actions.includes("submit")) return;
    const before = detail;
    setSubmitConfirm(false);
    setBusy(true);
    setError("");
    let responseValidated = false;
    let intent: MaterialRequestMutationIntent | undefined;
    try {
      intent = mutationRegistry.current.begin({
        requestId: before.request_id,
        action: "submit",
        path: `/v1/material-requests/${before.request_id}/submit`,
        body: { expected_version: before.request_version },
        expectedVersion: before.request_version,
      });
      const result: MaterialRequestMutationResult = validateMaterialRequestMutationResult(
        await adapter.mutate(intent),
        { requestId: before.request_id, action: "submit", previousVersion: before.request_version },
      );
      responseValidated = true;
      const reread = validateMaterialRequestDetail(
        await adapter.detail(before.request_id),
        before.request_id,
      );
      if (!axesMatch(result, reread)) throw new Error("提交响应与详情回读不一致，写入仍待人工核验");
      mutationRegistry.current.confirm(intent.request_id, intent.signature);
      setDetail(reread);
      setNotice("需求已提交并精确回读审批状态；分配、履约和通知仍为独立状态。");
    } catch (err) {
      const pending = mutationRegistry.current.get(before.request_id);
      if (!responseValidated && pending && isDefinitiveMaterialRequestRejection(err)) {
        mutationRegistry.current.clearDefinitiveRejection(pending.request_id, pending.signature);
      }
      setError(showError(err));
    } finally {
      setBusy(false);
    }
  }

  function startLifecycleProcess(kind: LifecycleProcessState["kind"]): void {
    if (!detail || !access) return;
    if (lifecycleWritesBlocked || reservationWriteBlocked()) {
      setError("已有生命周期命令待核验或恢复存储不可用，禁止生成新的撤回/取消请求坐标");
      return;
    }
    const pending = mutationRegistry.current.get(detail.request_id);
    if (pending) {
      setError(`当前需求已有结果未确认的 ${pending.action} 写入（请求坐标 ${pending.headers["X-Request-ID"]}），禁止开始其他动作`);
      return;
    }
    const allowed = detail.allowed_actions.includes(kind);
    const permitted = kind === "withdraw" ? access.can_withdraw : access.can_cancel;
    if (!allowed || !permitted) {
      setError("当前访问权限与详情允许动作不一致，已停止生命周期操作");
      return;
    }
    const lines: LifecycleProcessState["lines"] = [];
    if (kind === "cancel") {
      for (const line of detail.lines) {
        const approved = decimalUnits(line.final_approved_qty);
        if (approved === null) {
          setError("需求明细批准数量无效，已停止安全取消");
          return;
        }
        if (approved > 0n) {
          lines.push({
            requestLineId: line.request_line_id,
            cancelledQty: line.final_approved_qty,
            reason: "",
          });
        }
      }
    }
    setLifecycleProcess({
      kind,
      requestId: detail.request_id,
      requestVersion: detail.request_version,
      reason: "",
      lines,
      error: "",
      pendingMessage: "",
    });
    setError("");
  }

  function cancelLifecycleProcess(): void {
    if (lifecycleProcess && mutationRegistry.current.get(lifecycleProcess.requestId)) {
      setLifecycleProcess((current) => current ? {
        ...current,
        error: "操作结果尚未确认，必须保留原内容与请求坐标以便精确重试或人工核验",
      } : current);
      return;
    }
    setLifecycleProcess(null);
  }

  async function submitLifecycleProcess(): Promise<void> {
    if (reservationWriteBlocked()) return;
    if (!detail || !access || !lifecycleProcess) return;
    const before = detail;
    const process = lifecycleProcess;
    const permitted = process.kind === "withdraw" ? access.can_withdraw : access.can_cancel;
    if (
      process.requestId !== before.request_id
      || process.requestVersion !== before.request_version
      || !before.allowed_actions.includes(process.kind)
      || !permitted
    ) {
      setLifecycleProcess((current) => current ? {
        ...current,
        error: "需求版本、权限或允许动作已变化，请关闭后重新读取详情",
      } : current);
      return;
    }
    const reason = process.reason.trim();
    if (!reason || reason.length > 4000 || CONTROL_CHARACTERS.test(reason)) {
      setLifecycleProcess((current) => current ? {
        ...current,
        error: "必须填写不超过 4000 字的整单原因，且不能包含换行或控制字符",
      } : current);
      return;
    }
    const body: Record<string, unknown> = {
      expected_version: before.request_version,
      reason,
    };
    if (process.kind === "cancel") {
      const expectedLines = before.lines.filter((line) => {
        const quantity = decimalUnits(line.final_approved_qty);
        return quantity !== null && quantity > 0n;
      });
      if (
        expectedLines.length !== process.lines.length
        || expectedLines.some((line, index) => (
          process.lines[index]?.requestLineId !== line.request_line_id
          || process.lines[index]?.cancelledQty !== line.final_approved_qty
        ))
      ) {
        setLifecycleProcess((current) => current ? {
          ...current,
          error: "完整取消明细与当前最终批准数量不一致，请重新读取详情",
        } : current);
        return;
      }
      if (process.lines.some((line) => (
        !line.reason.trim()
        || line.reason.trim().length > 4000
        || CONTROL_CHARACTERS.test(line.reason.trim())
      ))) {
        setLifecycleProcess((current) => current ? {
          ...current,
          error: "每条有批准数量的明细都必须填写不超过 4000 字且不含控制字符的取消原因",
        } : current);
        return;
      }
      const cancellationLines = process.lines.map((line) => ({
        request_line_id: line.requestLineId,
        cancelled_qty: line.cancelledQty,
        reason: line.reason.trim(),
      }));
      body.lines = cancellationLines;
    }

    setBusy(true);
    let responseValidated = false;
    let transportStarted = false;
    try {
      const intent = mutationRegistry.current.begin({
        requestId: before.request_id,
        action: process.kind,
        path: `/v1/material-requests/${before.request_id}/${process.kind}`,
        body,
        expectedVersion: before.request_version,
      });
      recoveryStore.current.persist(intent.headers["X-Request-ID"]);
      setLifecycleRecovery({
        phase: "blocked",
        message: `生命周期请求坐标 ${intent.headers["X-Request-ID"]} 已在当前标签页持久化，服务端事实精确回读前保持阻断。`,
        retryable: true,
      });
      setLifecycleProcess((current) => current ? {
        ...current,
        error: "",
        pendingMessage: `操作结果确认中（请求坐标 ${intent.headers["X-Request-ID"]}）；重试只复用原坐标。`,
      } : current);
      transportStarted = true;
      const result = validateMaterialRequestMutationResult(await adapter.mutate(intent), {
        requestId: before.request_id,
        action: process.kind,
        previousVersion: before.request_version,
      });
      responseValidated = true;
      const reread = validateMaterialRequestDetail(
        await adapter.detail(before.request_id),
        before.request_id,
      );
      if (!lifecycleMutationMatches(result, before, reread, process.kind)) {
        throw new Error("生命周期响应与同一需求终态、审批锚点或逐行取消事实回读不一致，仍待人工核验");
      }
      recoveryStore.current.clear(intent.headers["X-Request-ID"]);
      mutationRegistry.current.confirm(intent.request_id, intent.signature);
      setDetail(reread);
      setLifecycleProcess(null);
      setLifecycleRecovery({
        phase: "recovered",
        message: `${process.kind === "withdraw" ? "撤回" : "取消"}命令已完成服务端响应和详情双重核验，当前标签页恢复坐标已安全清理。`,
        retryable: false,
      });
      setNotice(process.kind === "withdraw"
        ? "需求已撤回并完成终态精确回读；其他九个状态轴未被合并。"
        : "需求已安全取消并完成逐行事实与十个状态轴精确回读；未推断任何下游补偿。"
      );
    } catch (error) {
      const pending = mutationRegistry.current.get(before.request_id);
      if (!transportStarted && pending) {
        setLifecycleRecovery({
          phase: "blocked",
          message: `${showError(error)}；POST 未发送，当前页仍保留原内存意图并禁止生成新坐标。`,
          retryable: recoveryStore.current.read().kind === "valid",
        });
        setLifecycleProcess((current) => current ? {
          ...current,
          error: `${showError(error)}；生命周期 POST 未发送`,
          pendingMessage: "原请求坐标仍保留在当前页面内存中；修复恢复存储后只能按原坐标重试。",
        } : current);
      } else if (!responseValidated && pending && isLifecycleRejectionSafeToClear(error)) {
        try {
          recoveryStore.current.clear(pending.headers["X-Request-ID"]);
          mutationRegistry.current.clearDefinitiveRejection(pending.request_id, pending.signature);
          setLifecycleRecovery({ phase: "ready", message: "", retryable: false });
          setLifecycleProcess((current) => current ? {
            ...current,
            error: showError(error),
            pendingMessage: "",
          } : current);
        } catch (clearError) {
          setLifecycleRecovery({
            phase: "blocked",
            message: `${showError(clearError)}；虽已收到明确拒绝，但恢复坐标尚未完成本地清理。`,
            retryable: true,
          });
          setLifecycleProcess((current) => current ? {
            ...current,
            error: `${showError(error)}；${showError(clearError)}`,
            pendingMessage: "明确拒绝已收到，但本地恢复坐标清理失败，继续阻断新的生命周期写。",
          } : current);
        }
      } else if (pending) {
        setLifecycleRecovery({
          phase: "blocked",
          message: `生命周期请求坐标 ${pending.headers["X-Request-ID"]} 的服务端结果仍待核验，禁止生成新坐标。`,
          retryable: true,
        });
        setLifecycleProcess((current) => current ? {
          ...current,
          error: showError(error),
          pendingMessage: `操作结果仍未确认（请求坐标 ${pending.headers["X-Request-ID"]}）；禁止生成新坐标或执行其他动作。`,
        } : current);
      } else {
        setLifecycleProcess((current) => current ? { ...current, error: showError(error) } : current);
      }
    } finally {
      setBusy(false);
    }
  }

  function startApprovalProcess(kind: ApprovalProcessState["kind"]): void {
    if (supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked()) return;
    if (!detail || !access) return;
    const step = currentApprovalStep(detail);
    if (!step) {
      setError("当前审批步骤锚点缺失，已停止处理");
      return;
    }
    const actions = new Set(detail.allowed_actions);
    const internalAllowed = step.source_mode === "internal"
      && (step.step_no === 1 ? access.can_approve_region : step.step_no === 2 && access.can_approve_headquarters)
      && (actions.has("approve") || actions.has("return") || actions.has("reject"));
    const registrationAllowed = step.source_mode === "external_registration"
      && access.can_register_external && actions.has("register_external_approval");
    const evidence = detail.approval_instance?.external_evidence_summaries?.filter((item) => (
      item.step_id === step.step_id && item.status === "pending_verification"
    )) || [];
    const verificationAllowed = step.source_mode === "external_registration"
      && access.can_verify_external && actions.has("verify_external_approval") && evidence.length === 1;
    if ((kind === "internal" && !internalAllowed)
        || (kind === "external_registration" && !registrationAllowed)
        || (kind === "external_verification" && !verificationAllowed)) {
      setError("当前访问权限与详情允许动作不一致，已停止处理");
      return;
    }
    let lines: ApprovalLineState[] = [];
    try {
      if (kind !== "external_verification") lines = approvalInputLines(detail);
    } catch (error) {
      setError(showError(error));
      return;
    }
    const initialAction = kind === "internal"
      ? (["approve", "return", "reject"] as const).find((action) => actions.has(action)) || "reject"
      : "approve";
    setApprovalProcess({
      kind,
      stepId: step.step_id,
      stepVersion: step.version,
      action: initialAction,
      lines,
      comment: "",
      evidenceFileId: "",
      externalApproverName: "",
      externalReferenceNo: "",
      externalDecidedAt: "",
      registrationId: evidence[0]?.registration_id || "",
      verificationDecision: "accept",
      error: "",
      pendingMessage: "",
    });
    setExternalUploadBlocking(false);
    setError("");
  }

  function cancelApprovalProcess(): void {
    if (detail && mutationRegistry.current.get(detail.request_id)) {
      setApprovalProcess((current) => current ? {
        ...current,
        error: "处理结果尚未确认，必须保留原内容与请求坐标",
      } : current);
      return;
    }
    setApprovalProcess(null);
    setExternalUploadBlocking(false);
  }

  function approvalProcessField<K extends keyof ApprovalProcessState>(
    field: K,
    value: ApprovalProcessState[K],
  ): void {
    setApprovalProcess((current) => current ? { ...current, [field]: value, error: "" } : current);
  }

  async function submitApprovalProcess(): Promise<void> {
    if (supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked()) return;
    if (!detail || !approvalProcess || !access) return;
    const before = detail;
    const process = approvalProcess;
    const step = currentApprovalStep(before);
    if (!step || step.step_id !== process.stepId || step.version !== process.stepVersion) {
      setApprovalProcess((current) => current ? { ...current, error: "当前审批步骤或版本已变化，请重新读取详情" } : current);
      return;
    }
    const allowedActions = new Set(before.allowed_actions);
    const internalPermission = step.step_no === 1
      ? access.can_approve_region
      : step.step_no === 2 && access.can_approve_headquarters;
    const pendingEvidence = before.approval_instance?.external_evidence_summaries?.filter((item) => (
      item.step_id === step.step_id && item.status === "pending_verification"
    )) || [];
    const authorized = process.kind === "internal"
      ? step.source_mode === "internal" && internalPermission && allowedActions.has(process.action)
      : process.kind === "external_registration"
        ? step.source_mode === "external_registration" && access.can_register_external
          && allowedActions.has("register_external_approval")
        : step.source_mode === "external_registration" && access.can_verify_external
          && allowedActions.has("verify_external_approval") && pendingEvidence.length === 1
          && pendingEvidence[0].registration_id === process.registrationId;
    if (!authorized) {
      setApprovalProcess((current) => current ? {
        ...current,
        error: "当前权限、详情允许动作或证据锚点已不满足处理条件，已停止写入",
      } : current);
      return;
    }
    let body: Record<string, unknown>;
    let intentAction: "approve" | "return" | "reject" | "register_external_approval" | "verify_external_approval";
    let path: string;
    try {
      const comment = process.comment.trim();
      if ((process.kind !== "external_verification" && ["return", "reject"].includes(process.action))
          && !comment) throw new Error("退回或驳回必须填写处理意见");
      if (process.kind === "external_verification") {
        if (!NONZERO_UUID.test(process.registrationId)) throw new Error("待复核证据锚点无效");
        if (process.verificationDecision === "reject" && !comment) throw new Error("复核拒绝必须填写原因");
        body = {
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          decision: process.verificationDecision,
          comment,
        };
        intentAction = "verify_external_approval";
        path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence/${process.registrationId}/verification`;
      } else {
        const linePayload = approvalLinePayload(process);
        body = {
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          action: process.action,
          ...linePayload,
          comment,
        };
        if (process.kind === "internal") {
          intentAction = process.action;
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/decision`;
        } else {
          const evidenceFileId = process.evidenceFileId.trim();
          const approver = process.externalApproverName.trim();
          const reference = process.externalReferenceNo.trim();
          const decidedAt = process.externalDecidedAt.trim();
          if (externalUploadBlocking || !NONZERO_UUID.test(evidenceFileId)) throw new Error("必须先完成独立正式外部审批证据上传并确认 available");
          if (!approver || approver.length > 160) throw new Error("星星总部审批人无效");
          if (!SAFE_EXTERNAL_REFERENCE.test(reference)) throw new Error("外部审批参考号无效");
          if (!AWARE_TIMESTAMP.test(decidedAt) || !Number.isFinite(Date.parse(decidedAt))) {
            throw new Error("外部决定时间必须是包含时区的 ISO8601 时间");
          }
          body = {
            expected_request_version: before.request_version,
            expected_step_version: step.version,
            evidence_file_id: evidenceFileId.toLowerCase(),
            external_approver_name: approver,
            external_reference_no: reference,
            external_decided_at: decidedAt,
            action: process.action,
            ...linePayload,
            comment,
          };
          intentAction = "register_external_approval";
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence`;
        }
      }
    } catch (error) {
      setApprovalProcess((current) => current ? { ...current, error: showError(error) } : current);
      return;
    }

    setBusy(true);
    let responseValidated = false;
    try {
      const intent = mutationRegistry.current.begin({
        requestId: before.request_id,
        action: intentAction,
        path,
        body,
        expectedVersion: before.request_version,
      });
      setApprovalProcess((current) => current ? {
        ...current,
        error: "",
        pendingMessage: `处理结果确认中（请求坐标 ${intent.headers["X-Request-ID"]}）；重试只复用原坐标。`,
      } : current);
      const result = validateMaterialRequestMutationResult(await adapter.mutate(intent), {
        requestId: before.request_id,
        action: intentAction,
        previousVersion: before.request_version,
      });
      responseValidated = true;
      const reread = validateMaterialRequestDetail(
        await adapter.detail(before.request_id),
        before.request_id,
      );
      if (!approvalMutationMatches(result, reread)) {
        throw new Error("处理响应与同一需求审批锚点/状态回读不一致，仍待人工核验");
      }
      mutationRegistry.current.confirm(intent.request_id, intent.signature);
      setDetail(reread);
      setApprovalProcess(null);
      setExternalUploadBlocking(false);
      setNotice("审批处理已完成精确回读；分配、占用及履约状态未被合并。");
    } catch (error) {
      const pending = mutationRegistry.current.get(before.request_id);
      if (!responseValidated && pending && isDefinitiveMaterialRequestRejection(error)) {
        mutationRegistry.current.clearDefinitiveRejection(pending.request_id, pending.signature);
        setApprovalProcess((current) => current ? { ...current, error: showError(error), pendingMessage: "" } : current);
      } else if (pending) {
        setApprovalProcess((current) => current ? {
          ...current,
          error: showError(error),
          pendingMessage: `处理结果仍未确认（请求坐标 ${pending.headers["X-Request-ID"]}）；禁止生成新坐标或执行其他动作。`,
        } : current);
      }
    } finally {
      setBusy(false);
    }
  }

  const canLoadMore = useMemo(() => Boolean(nextAfterId && access?.can_read), [nextAfterId, access]);

  return <>
    <SectionHeader
      title="需求提报"
      subtitle="正式 V1.0 客户端纵切；审批、供给和十个业务状态轴分别展示"
      actions={<>
        <Button tone="secondary" icon={<RefreshCw size={17} />} disabled={loading || !access?.can_read} onClick={() => void loadList()}>刷新</Button>
        {access?.can_create && <Button icon={<Plus size={17} />} disabled={busy} onClick={startCreate}>新建需求</Button>}
      </>}
    />

    <div className="alert alert-info">正式 V1.0 客户端路由已接线；生产写入仍受服务端写 gate 与 runtime ACL 控制。每次写入只在响应契约与同一需求详情精确回读一致后确认；明文草稿仅驻留当前页面内存。</div>
    {error && <div className="alert alert-error">{error}</div>}
    {notice && <div className="alert alert-info">{notice}</div>}
    {(lifecycleRecovery.phase === "checking" || lifecycleRecovery.phase === "blocked") && <div className="alert alert-warning" role="status">
      <strong>生命周期命令待核验：</strong>{lifecycleRecovery.message}
      {lifecycleRecovery.retryable && <div className="form-actions">
        <Button tone="secondary" disabled={busy || lifecycleRecovery.phase === "checking"} onClick={() => void retryStoredLifecycleRecovery()}>
          {lifecycleRecovery.phase === "checking" ? "正在有限退避核验" : "重新核验原请求坐标"}
        </Button>
      </div>}
    </div>}
    {lifecycleRecovery.phase === "recovered" && <div className="alert alert-info" role="status">
      <strong>生命周期命令已恢复：</strong>{lifecycleRecovery.message}
    </div>}

    <section className="content-section" aria-label="正式需求列表">
      <div className="content-title"><div><h2>我的需求</h2><p>列表和详情只接受脱敏地址、脱敏联系人</p></div></div>
      {loading ? <Loading label="正在校验身份、权限与正式需求契约" /> : items.length ? <>
        <div className="table-wrap"><table>
          <thead><tr><th>需求单</th><th>用途/工单</th><th>紧急程度</th><th>期望日期</th><th>脱敏收货信息</th><th>申请状态</th><th>明细</th><th>详情</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.request_id}>
            <td><strong>{item.request_no}</strong><span className="cell-subtitle mono">{item.request_id}</span></td>
            <td>{item.purpose}<span className="cell-subtitle mono">{item.work_order_id || "未关联工单"}</span></td>
            <td>{item.urgency}</td><td>{item.expected_date || "-"}</td>
            <td>{item.contact_masked.name_masked} · {item.contact_masked.mobile_masked}<span className="cell-subtitle">{item.address_snapshot.detail_masked}</span></td>
            <td><span className={`status status-${item.states.request_status}`}>{REQUEST_STATUS_LABELS[item.states.request_status] || item.states.request_status}</span></td>
            <td>{item.line_count}</td>
            <td><button className="table-action" disabled={busy} onClick={() => void openDetail(item.request_id)}><Eye size={16} />查看</button></td>
          </tr>)}</tbody>
        </table></div>
        {canLoadMore && <div className="form-actions" style={{ padding: 14 }}><Button tone="secondary" disabled={loading} onClick={() => void loadList(nextAfterId)}>加载更多</Button></div>}
      </> : <Empty title={access?.can_read ? "暂无可见需求" : "需求读取已失败关闭"} detail="不会回退非正式业务接口或猜测权限" />}
    </section>

    {!detail && shipmentBlocked && <FormalMaterialRequestShipmentPanel detail={null} adapter={adapter} access={access} store={shipmentStore.current}
        otherWriteBusy={baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || outboundBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
        otherWriteBlocked={() => baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || pickBlockingRef.current || outboundBlockingRef.current || [recoveryStore.current, supplyStore.current, allocationStore.current, reservationStore.current, releaseStore.current, pickStore.current, outboundStore.current].some(s => s.read().kind !== "missing")}
        onBlocking={onShipmentBlocking} onDetail={setDetail} />}
    {!detail && <FormalMaterialRequestSupplyPanel
      adapter={adapter} access={access} detail={null} store={supplyStore.current}
      registry={mutationRegistry.current}
      allocationRecoveryStore={allocationStore.current}
      otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || baseBusy || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking" || allocationBlocked || reservationBlocked}
      otherWriteBlocked={() => reservationWriteBlocked()}
      onBlocking={onSupplyBlocking} onAllocationBlocking={onAllocationBlocking} onDetail={setDetail}
    />}
    {!detail && <FormalMaterialRequestReservationPanel
      adapter={adapter} access={access} detail={null} store={reservationStore.current}
      otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || releaseBlocked || baseBusy || supplyBlocked || allocationBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => releaseWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onReservationBlocking} onDetail={setDetail}
    />}
    {!detail && <FormalMaterialRequestReleasePanel adapter={adapter} access={access} detail={null} store={releaseStore.current}
      otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => pickWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onReleaseBlocking} onDetail={setDetail} />}
    {!detail && <FormalMaterialRequestOutboundPanel adapter={adapter} access={access} detail={null} store={outboundStore.current}
      otherWriteBusy={shipmentBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => shipmentWriteBlocked() || pickBlockingRef.current || pickStore.current.read().kind !== "missing" || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || releaseStore.current.read().kind !== "missing" || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onOutboundBlocking} onDetail={setDetail} />}
    {!detail && <FormalMaterialRequestPickPanel adapter={adapter} access={access} detail={null} store={pickStore.current}
      otherWriteBusy={shipmentBlocked || outboundBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => outboundWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || releaseStore.current.read().kind !== "missing" || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onPickBlocking} onDetail={setDetail} />}
    {detail && access && <Modal title="正式需求详情" wide onClose={() => {
      if (!approvalProcess && !lifecycleProcess && !supplyBlocked && !allocationBlocked && !reservationWriteBlocked()) {
        editRecoveryGeneration.current += 1;
        setBusy(false);
        setDetail(null);
      }
    }}>
      <FormalMaterialRequestReservationPanel
        adapter={adapter} access={access} detail={detail} store={reservationStore.current}
        otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || releaseBlocked || baseBusy || supplyBlocked || allocationBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
        otherWriteBlocked={() => releaseWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
        onBlocking={onReservationBlocking} onDetail={setDetail}
      />
      <FormalMaterialRequestReleasePanel adapter={adapter} access={access} detail={detail} store={releaseStore.current}
        otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
        otherWriteBlocked={() => pickWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
        onBlocking={onReleaseBlocking} onDetail={setDetail} />
      <FormalMaterialRequestOutboundPanel adapter={adapter} access={access} detail={detail} store={outboundStore.current}
      otherWriteBusy={shipmentBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => shipmentWriteBlocked() || pickBlockingRef.current || pickStore.current.read().kind !== "missing" || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || releaseStore.current.read().kind !== "missing" || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onOutboundBlocking} onDetail={setDetail} />
      <FormalMaterialRequestShipmentPanel detail={detail} adapter={adapter} access={access} store={shipmentStore.current}
        otherWriteBusy={baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || pickBlocked || outboundBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
        otherWriteBlocked={() => baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || pickBlockingRef.current || outboundBlockingRef.current || [recoveryStore.current, supplyStore.current, allocationStore.current, reservationStore.current, releaseStore.current, pickStore.current, outboundStore.current].some(s => s.read().kind !== "missing")}
        onBlocking={onShipmentBlocking} onDetail={setDetail} />
      <fieldset disabled={shipmentBlocked} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <FormalMaterialRequestReceiptPanel adapter={adapter} detail={detail} />
        <FormalMaterialRequestOamReceiptPanel adapter={adapter} detail={detail} />
        <FormalMaterialRequestInboundPanel adapter={adapter} detail={detail} />
      </fieldset>
      <FormalMaterialRequestPickPanel adapter={adapter} access={access} detail={detail} store={pickStore.current}
      otherWriteBusy={shipmentBlocked || outboundBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
      otherWriteBlocked={() => outboundWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationBlockingRef.current || releaseBlockingRef.current || releaseStore.current.read().kind !== "missing" || reservationStore.current.read().kind !== "missing" || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"}
      onBlocking={onPickBlocking} onDetail={setDetail} />
      <DetailPanel detail={detail} access={access} busy={busy} lifecycleBlocked={lifecycleWritesBlocked} onEdit={() => void startEdit()} onSubmit={() => setSubmitConfirm(true)} onProcess={startApprovalProcess} onLifecycle={startLifecycleProcess} />
      <FormalMaterialRequestFulfillmentPreparationPanel adapter={adapter} access={access} detail={detail}
        otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || baseBusy || supplyBlocked || allocationBlocked || reservationBlocked || releaseBlocked || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking"}
        otherWriteBlocked={() => pickWriteBlocked() || baseBusyRef.current || supplyBlockingRef.current || allocationBlockingRef.current || reservationWriteBlocked() || supplyStore.current.read().kind !== "missing" || allocationStore.current.read().kind !== "missing" || recoveryStore.current.read().kind !== "missing"} />
      <FormalMaterialRequestSupplyPanel
        adapter={adapter} access={access} detail={detail} store={supplyStore.current}
        registry={mutationRegistry.current}
        allocationRecoveryStore={allocationStore.current}
        otherWriteBusy={shipmentBlocked || outboundBlocked || pickBlocked || baseBusy || lifecycleRecovery.phase === "blocked" || lifecycleRecovery.phase === "checking" || allocationBlocked || reservationBlocked}
        otherWriteBlocked={() => reservationWriteBlocked()}
        onBlocking={onSupplyBlocking} onAllocationBlocking={onAllocationBlocking} onDetail={setDetail}
      />
    </Modal>}

    {formMode && <Modal title={formMode.kind === "create" ? "新建需求草稿" : "编辑需求草稿"} wide onClose={cancelRawForm}>
      <DraftForm
        form={form}
        mode={formMode}
        busy={busy}
        writePending={draftWritePending}
        error={formError}
        pendingMessage={pendingMessage}
        onChange={updateForm}
        onLineChange={updateLine}
        onAddLine={() => setForm((current) => ({ ...current, lines: [...current.lines, emptyLine(nextLineKey())] }))}
        onRemoveLine={(key) => setForm((current) => ({ ...current, lines: current.lines.filter((line) => line.key !== key) }))}
        onChooseWorkOrder={openWorkOrderPicker}
        onClearWorkOrder={clearWorkOrder}
        onChooseMaterial={openMaterialPicker}
        onClearSubstitute={(key) => setForm((current) => ({ ...current, lines: current.lines.map((line) => line.key === key ? { ...line, substituteMaterial: null } : line) }))}
        uploadClient={fileUploadClient}
        uploadBindingKey={`${access?.person_id || "no-person"}:${access?.authorization_version || 0}:draft:${formMode.kind === "create" ? "create" : `${formMode.requestId}:${formMode.requestVersion}`}`}
        uploadBlocking={draftUploadBlocking}
        onUploadedFiles={setDraftUploadFiles}
        onUploadBlocking={setDraftUploadBlocking}
        onCancel={cancelRawForm}
        onSave={() => void saveDraft()}
      />
    </Modal>}

    {workOrderPicker && <WorkOrderPicker
      picker={workOrderPicker}
      onQuery={updateWorkOrderPickerQuery}
      onSearch={() => void readWorkOrderPickerPage(workOrderPicker.query, null, false)}
      onLoadMore={() => void readWorkOrderPickerPage(
        workOrderPicker.query,
        workOrderPicker.nextAfterId,
        true,
      )}
      onSelect={chooseWorkOrder}
      onClose={() => {
        workOrderPickerGeneration.current += 1;
        setWorkOrderPicker(null);
      }}
    />}

    {submitConfirm && detail && <Modal title="提交前确认" onClose={() => setSubmitConfirm(false)}>
      <div className="form-stack" aria-label="需求提交确认摘要">
        <p><strong>用途：</strong>{detail.purpose}</p>
        <p><strong>明细：</strong>{detail.lines.length} 条，共按各行定点数量提交</p>
        <p><strong>联系人：</strong>{detail.contact_masked.name_masked} · {detail.contact_masked.mobile_masked}</p>
        <p><strong>地址：</strong>{detail.address_snapshot.province_name}{detail.address_snapshot.city_name}{detail.address_snapshot.district_name}{detail.address_snapshot.detail_masked}</p>
        <div className="alert alert-warning">提交只会进入审批。审批通过不等于分配、占用、出库、发货、物流签收、OAM收货、RSC/个人仓入库、通知送达或对账同步完成。</div>
        <div className="form-actions"><Button tone="secondary" onClick={() => setSubmitConfirm(false)}>返回检查</Button><Button disabled={busy} onClick={() => void submitRequest()}>确认提交</Button></div>
      </div>
    </Modal>}

    {materialPicker && <MaterialPicker
      picker={materialPicker}
      onQuery={(query) => setMaterialPicker((current) => current ? { ...current, query } : current)}
      onSearch={() => void readMaterialPickerPage(materialPicker.query, null, false)}
      onLoadMore={() => void readMaterialPickerPage(materialPicker.query, materialPicker.nextAfterId, true)}
      onSelect={chooseMaterial}
      onClose={() => { pickerGeneration.current += 1; setMaterialPicker(null); }}
    />}

    {approvalProcess && detail && <ApprovalProcessForm
      process={approvalProcess}
      detail={detail}
      busy={busy}
      uploadClient={fileUploadClient}
      uploadBindingKey={`${access?.person_id || "no-person"}:${access?.authorization_version || 0}:external:${detail.request_id}:${approvalProcess.stepId}:${approvalProcess.stepVersion}`}
      uploadBlocking={externalUploadBlocking}
      onAction={(action) => setApprovalProcess((current) => current ? { ...current, action, error: "" } : current)}
      onLine={(requestLineId, field, value) => setApprovalProcess((current) => current ? {
        ...current,
        error: "",
        lines: current.lines.map((line) => line.requestLineId === requestLineId ? { ...line, [field]: value } : line),
      } : current)}
      onField={approvalProcessField}
      onUploadBlocking={setExternalUploadBlocking}
      onCancel={cancelApprovalProcess}
      onSubmit={() => void submitApprovalProcess()}
    />}

    {lifecycleProcess && <LifecycleProcessForm
      process={lifecycleProcess}
      busy={busy}
      onReason={(reason) => setLifecycleProcess((current) => current ? { ...current, reason, error: "" } : current)}
      onLineReason={(requestLineId, reason) => setLifecycleProcess((current) => current ? {
        ...current,
        error: "",
        lines: current.lines.map((line) => line.requestLineId === requestLineId ? { ...line, reason } : line),
      } : current)}
      onCancel={cancelLifecycleProcess}
      onSubmit={() => void submitLifecycleProcess()}
    />}
  </>;
}
