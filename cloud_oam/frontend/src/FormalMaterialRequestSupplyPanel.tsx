import { useEffect, useRef, useState } from "react";
import { mutationHeaders } from "./api";
import {
  type FormalMaterialRequestAccess, type FormalMaterialRequestAdapter,
  validateFormalMaterialRequestAccess,
  validateFormalMaterialRequestFreshIdentity,
} from "./formalMaterialRequestAdapter";
import {
  MaterialRequestIntentRegistry, type MaterialRequestDetail, type MaterialRequestSupplyTask, type MaterialRequestMutationIntent,
  MATERIAL_REQUEST_STATE_AXIS_FIELDS, validateMaterialRequestDetail,
} from "./formalMaterialRequests";
import {
  type SupplyAction, type SupplyCreateInput, type SupplyUpdateInput,
  isDefinitiveSupplyPostRejection, supplyMutationMatchesDetail, validateSupplyCreateInput,
  validateSupplyMutationResult, validateSupplyUpdateInput,
} from "./formalMaterialRequestSupply";
import type { MaterialRequestAllocationOption, MaterialRequestAllocationOptionPage } from "./formalMaterialRequestAllocationOptions";
import {
  type MaterialRequestAllocationMutationResult,
  validateMaterialRequestAllocationMutationResult,
} from "./formalMaterialRequestAllocationCommandStatus";
import { type SupplyRecoveryStore, type SupplySentinel, recoverSupplyCommand } from "./materialRequestSupplyRecovery";
import {
  createAllocationRecoveryStore,
  recoverAllocationCommand,
  type AllocationRecoveryStore,
  type AllocationSentinel,
} from "./materialRequestAllocationRecovery";
import { Button, Field, Modal, showError } from "./ui";

const TYPES: Record<SupplyCreateInput["supply_type"], string> = {
  cross_region_transfer: "跨区域供给计划", headquarters_replenishment: "总部补货计划",
  star_replenishment: "星星补货计划", external_procurement_reference: "外部采购参考",
};
const STATUSES: Record<SupplyUpdateInput["status"], string> = {
  open: "计划待跟进", reference_registered: "已登记参考号", awaiting_supply: "等待供应",
  cancelled: "计划已取消", closed_no_supply: "无供应结束",
};
type Form = {
  action: SupplyAction; before: MaterialRequestDetail; task: MaterialRequestSupplyTask | null;
  lineId: string; supplyType: SupplyCreateInput["supply_type"]; quantity: string;
  referenceNo: string; expectedDate: string; comment: string; status: SupplyUpdateInput["status"];
};
type AllocationForm = Readonly<{
  before: MaterialRequestDetail;
  page: MaterialRequestAllocationOptionPage;
  option: MaterialRequestAllocationOption;
  lineId: string;
  quantity: string;
  serialIds: string;
}>;
function units(value: string): bigint { return BigInt(value.replace(".", "")); }
function remaining(detail: MaterialRequestDetail, lineId: string): bigint {
  const line = detail.lines.find((row) => row.request_line_id === lineId);
  if (!line || !["approved", "partially_approved"].includes(line.status)) return 0n;
  return units(line.final_approved_qty) - units(line.cancelled_qty) - detail.supply_tasks
    .filter((task) => task.request_line_id === lineId && !["cancelled", "closed_no_supply"].includes(task.status))
    .reduce((total, task) => total + units(task.original_equivalent_qty), 0n);
}
function quantityText(value: string): string {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(value)) throw new Error("计划数量最多保留三位小数");
  const [whole, fraction = ""] = value.split(".");
  return `${whole}.${fraction.padEnd(3, "0")}`;
}
function exactScalarSnapshotMatches(left: object, right: object): boolean {
  const snapshot = (value: object) => Object.entries(value).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(snapshot(left)) === JSON.stringify(snapshot(right));
}

const ALLOCATION_QUANTITY = /^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ALLOCATION_STATUS_LABELS: Record<MaterialRequestDetail["states"]["allocation_status"], string> = {
  not_allocated: "未分配", partially_allocated: "部分分配", allocated: "已全部分配", shortage: "分配短缺",
};

function decimalUnits(value: string): bigint {
  if (!ALLOCATION_QUANTITY.test(value)) throw new Error("分配数量格式无效，最多保留三位小数");
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, "0"));
}

function allocationQuantityText(value: string, maxScale = 3): string {
  if (!ALLOCATION_QUANTITY.test(value)) throw new Error("分配数量格式无效，最多保留三位小数");
  const [, fraction = ""] = value.split(".");
  if (fraction.length > maxScale) throw new Error(`该货源数量最多保留 ${maxScale} 位小数`);
  if (decimalUnits(value) <= 0n) throw new Error("分配数量必须大于零");
  const [whole] = value.split(".");
  return `${whole}.${fraction.padEnd(3, "0")}`;
}

function editableQuantity(value: bigint): string {
  const whole = value / 1000n;
  const fraction = (value % 1000n).toString().padStart(3, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole.toString();
}

function parseSerialIds(value: string): string[] {
  const tokens = value.split(/[\s,，、]+/).map((item) => item.trim()).filter(Boolean);
  if (tokens.length > 1000) throw new Error("串码数量超过 1000 个，已停止提交");
  const ids = tokens.map((item) => {
    if (!UUID.test(item)) throw new Error("串码必须填写有效 UUID，每行一个");
    return item.toLowerCase();
  });
  if (new Set(ids).size !== ids.length) throw new Error("串码不能重复");
  return ids;
}

function axesExceptAllocationMatch(
  before: MaterialRequestDetail["states"],
  after: MaterialRequestDetail["states"],
): boolean {
  return (Object.values(MATERIAL_REQUEST_STATE_AXIS_FIELDS) as Array<keyof MaterialRequestDetail["states"]>)
    .filter((key) => key !== "allocation_status")
    .every((key) => before[key] === after[key]);
}

function allocationRecoveryAvailable(adapter: FormalMaterialRequestAdapter): boolean {
  return typeof adapter.allocationCommandStatusNoReplay === "function"
    && typeof adapter.loadIdentityNoReplay === "function"
    && typeof adapter.loadAccessNoReplay === "function"
    && typeof adapter.detailNoReplay === "function";
}

export default function FormalMaterialRequestSupplyPanel({
  adapter, access, detail, store, registry, otherWriteBusy, otherWriteBlocked, onBlocking, onDetail,
  allocationRecoveryStore, onAllocationBlocking = () => {},
}: {
  adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail | null;
  store: SupplyRecoveryStore; registry: MaterialRequestIntentRegistry; otherWriteBusy: boolean;
  otherWriteBlocked?: () => boolean;
  onBlocking: (blocked: boolean) => void; onDetail: (detail: MaterialRequestDetail) => void;
  allocationRecoveryStore?: AllocationRecoveryStore;
  onAllocationBlocking?: (blocked: boolean) => void;
}) {
  const allocationStoreRef = useRef<AllocationRecoveryStore | null>(null);
  if (!allocationStoreRef.current) {
    allocationStoreRef.current = allocationRecoveryStore ?? createAllocationRecoveryStore();
  }
  const allocationStore = allocationStoreRef.current;
  const [form, setForm] = useState<Form | null>(null);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [allocationOptions, setAllocationOptions] = useState<{ lineId: string; page: MaterialRequestAllocationOptionPage } | null>(null);
  const [allocationLoading, setAllocationLoading] = useState(false);
  const [allocationForm, setAllocationForm] = useState<AllocationForm | null>(null);
  const [allocationRunning, setAllocationRunning] = useState(false);
  const [allocationMessage, setAllocationMessage] = useState("");
  const [allocationError, setAllocationError] = useState("");
  const generation = useRef(0);
  const inFlight = useRef(false);
  const allocationGeneration = useRef(0);
  const allocationInFlight = useRef(false);
  const read = store.read();
  const blocked = read.kind !== "missing";
  const allocationRead = allocationStore.read();
  const allocationBlocked = allocationRead.kind !== "missing";
  const canCreate = Boolean(detail?.allowed_actions.includes("create_supply_task"));
  const canReadAllocationOptions = Boolean(access?.can_read_allocation_options);

  async function showAllocationOptions(lineId: string) {
    if (!detail || !canReadAllocationOptions || allocationLoading) return;
    setAllocationLoading(true);
    setAllocationError("");
    try {
      const page = await adapter.listAllocationOptions(detail.request_id, lineId);
      setAllocationOptions({ lineId, page });
    } catch (caught) {
      setAllocationError(`货源候选读取失败：${showError(caught)}`);
    } finally {
      setAllocationLoading(false);
    }
  }

  async function recoverAllocation() {
    if (allocationInFlight.current) return;
    const stored = allocationStore.read();
    if (stored.kind !== "valid") {
      setAllocationError("分配恢复记录不可用，写入保持暂停");
      onAllocationBlocking?.(true);
      return;
    }
    if (!allocationRecoveryAvailable(adapter)) {
      setAllocationError("当前客户端缺少分配无重放核验通道，写入保持暂停");
      onAllocationBlocking?.(true);
      return;
    }
    const currentGeneration = allocationGeneration.current;
    allocationInFlight.current = true;
    setAllocationRunning(true);
    onAllocationBlocking?.(true);
    setAllocationError("");
    try {
      const recoveryAdapter = adapter as FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter,
        "allocationCommandStatusNoReplay" | "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay">>;
      const outcome = await recoverAllocationCommand(
        recoveryAdapter,
        allocationStore,
        stored.value,
        () => currentGeneration === allocationGeneration.current,
      );
      if (currentGeneration !== allocationGeneration.current) return;
      onDetail(outcome.detail);
      setAllocationForm(null);
      setAllocationMessage(`此前分配操作已确认：${outcome.command.allocation_no}，数量 ${outcome.command.allocated_qty}。占用、出库、发运、收货和入库仍是独立状态。`);
      onAllocationBlocking?.(false);
    } catch (caught) {
      if (currentGeneration === allocationGeneration.current) setAllocationError(showError(caught));
    } finally {
      if (currentGeneration === allocationGeneration.current) setAllocationRunning(false);
      allocationInFlight.current = false;
    }
  }

  useEffect(() => {
    allocationGeneration.current += 1;
    const stored = allocationStore.read();
    onAllocationBlocking?.(stored.kind !== "missing");
    if (stored.kind === "valid" && access) void recoverAllocation();
    else if (stored.kind !== "missing" && stored.kind !== "valid") {
      setAllocationError("分配恢复存储不可用，写入保持暂停");
    }
    return () => { allocationGeneration.current += 1; };
    // Resume the exact allocation coordinate only after the authenticated identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, allocationStore, access?.person_id, access?.authorization_version]);

  function chooseAllocation(option: MaterialRequestAllocationOption) {
    const current = allocationOptions;
    const currentStore = allocationStore.read();
    if (!detail || !access || !current || blocked || allocationRunning || otherWriteBusy || otherWriteBlocked?.()
        || currentStore.kind !== "missing" || registry.get(detail.request_id)) {
      setAllocationError("当前需求存在待核验操作，请先完成结果核验");
      onAllocationBlocking?.(currentStore.kind !== "missing");
      return;
    }
    if (current.page.request_id !== detail.request_id
        || current.page.request_line_id !== current.lineId
        || current.page.request_version !== detail.request_version
        || option.material_id !== current.page.material_id) {
      setAllocationError("货源候选与当前需求版本不一致，请重新读取");
      return;
    }
    const maximum = decimalUnits(option.quantity) < decimalUnits(current.page.allocatable_qty)
      ? decimalUnits(option.quantity) : decimalUnits(current.page.allocatable_qty);
    if (maximum <= 0n) {
      setAllocationError("该货源已无可分配数量，请重新读取");
      return;
    }
    setAllocationError("");
    setAllocationMessage("");
    setAllocationForm({
      before: detail,
      page: current.page,
      option,
      lineId: current.lineId,
      quantity: editableQuantity(maximum),
      serialIds: "",
    });
    setAllocationOptions(null);
  }

  async function submitAllocation() {
    if (!allocationForm || !access || allocationRunning || allocationInFlight.current
        || blocked || otherWriteBusy || otherWriteBlocked?.() || allocationStore.read().kind !== "missing") return;
    const currentGeneration = allocationGeneration.current;
    const before = allocationForm.before;
    const originalPage = allocationForm.page;
    const originalOption = allocationForm.option;
    let persisted = false;
    let sentinel: AllocationSentinel | undefined;
    allocationInFlight.current = true;
    setAllocationRunning(true);
    setAllocationError("");
    onAllocationBlocking?.(true);
    try {
      const allocatedQty = allocationQuantityText(allocationForm.quantity, originalOption.quantity_scale);
      const allocatedUnits = decimalUnits(allocatedQty);
      if (allocatedUnits > decimalUnits(originalOption.quantity)
          || allocatedUnits > decimalUnits(originalPage.allocatable_qty)) {
        throw new Error("分配数量超过当前货源或明细可分配余量");
      }
      const serialIds = parseSerialIds(allocationForm.serialIds);
      const rawIdentity = adapter.loadIdentityNoReplay
        ? await adapter.loadIdentityNoReplay() : await adapter.loadIdentity();
      const identity = validateFormalMaterialRequestFreshIdentity(rawIdentity);
      const rawAccess = adapter.loadAccessNoReplay
        ? await adapter.loadAccessNoReplay() : await adapter.loadAccess();
      const freshAccess = validateFormalMaterialRequestAccess(rawAccess);
      if (identity.person_id !== access.person_id
          || identity.authorization_version !== access.authorization_version
          || !freshAccess.can_read || !freshAccess.can_read_allocation_options
          || !exactScalarSnapshotMatches(freshAccess, access)) {
        throw new Error("登录身份或分配授权已变化，请重新进入页面");
      }
      const rawDetail = adapter.detailNoReplay
        ? await adapter.detailNoReplay(before.request_id) : await adapter.detail(before.request_id);
      const fresh = validateMaterialRequestDetail(rawDetail, before.request_id);
      const freshLine = fresh.lines.find((line) => line.request_line_id === allocationForm.lineId);
      if (fresh.request_version !== before.request_version
          || fresh.current_revision_id !== before.current_revision_id
          || fresh.current_revision_no !== before.current_revision_no
          || !freshLine || freshLine.revision_id !== fresh.current_revision_id
          || freshLine.revision_no !== fresh.current_revision_no
          || !["approved", "partially_approved"].includes(freshLine.status)) {
        throw new Error("需求或批准明细已变化，请返回详情重新检查");
      }
      const freshPage = await adapter.listAllocationOptions(before.request_id, allocationForm.lineId);
      const freshOption = freshPage.items.find((item) => item.stock_account_id === originalOption.stock_account_id);
      if (freshPage.request_id !== originalPage.request_id
          || freshPage.request_line_id !== originalPage.request_line_id
          || freshPage.request_version !== originalPage.request_version
          || freshPage.current_revision_id !== originalPage.current_revision_id
          || freshPage.current_revision_no !== originalPage.current_revision_no
          || freshPage.material_id !== originalPage.material_id
          || freshPage.allocatable_qty !== originalPage.allocatable_qty
          || freshPage.ledger_cursor !== originalPage.ledger_cursor
          || !freshOption
          || freshOption.material_id !== originalOption.material_id
          || freshOption.quantity !== originalOption.quantity
          || freshOption.balance_version !== originalOption.balance_version
          || freshOption.ledger_cursor !== originalOption.ledger_cursor
          || allocatedUnits > decimalUnits(freshOption.quantity)
          || allocatedUnits > decimalUnits(freshPage.allocatable_qty)) {
        throw new Error("货源余额、投影游标或批准余量已变化，请重新选择货源");
      }
      if (currentGeneration !== allocationGeneration.current || otherWriteBlocked?.()) return;
      const generated = new Headers(mutationHeaders("material-request-allocation").headers);
      const xRequestId = generated.get("X-Request-ID") || "";
      const idempotencyKey = generated.get("Idempotency-Key") || "";
      if (!xRequestId || !idempotencyKey) throw new Error("无法生成分配请求坐标");
      const body = {
        expected_request_version: before.request_version,
        request_line_id: allocationForm.lineId,
        source_stock_account_id: freshOption.stock_account_id,
        allocated_qty: allocatedQty,
        source_balance_version: freshOption.balance_version,
        source_ledger_cursor: freshOption.ledger_cursor,
        serial_ids: serialIds,
      } as const;
      sentinel = {
        v: 1,
        kind: "material_request_allocation",
        x_request_id: xRequestId,
        person_id: access.person_id,
        authorization_version: access.authorization_version,
        request_id: before.request_id,
        request_line_id: allocationForm.lineId,
        request_version: before.request_version,
        source_stock_account_id: freshOption.stock_account_id,
        allocated_qty: allocatedQty,
        source_balance_version: freshOption.balance_version,
        source_ledger_cursor: freshOption.ledger_cursor,
      };
      allocationStore.persist(sentinel);
      persisted = true;
      const result: MaterialRequestAllocationMutationResult = validateMaterialRequestAllocationMutationResult(
        await adapter.createAllocation(before.request_id, body, {
          "X-Request-ID": xRequestId,
          "Idempotency-Key": idempotencyKey,
        }),
      );
      if (result.request_id !== before.request_id
          || result.request_line_id !== allocationForm.lineId
          || result.request_version !== before.request_version + 1
          || result.current_request_version !== result.request_version
          || result.revision_id !== before.current_revision_id
          || result.revision_no !== before.current_revision_no
          || result.source_stock_account_id !== freshOption.stock_account_id
          || result.source_balance_version !== freshOption.balance_version
          || result.source_ledger_cursor !== freshOption.ledger_cursor
          || result.allocated_qty !== allocatedQty
          || !axesExceptAllocationMatch(before.states, result.state_axes)) {
        throw new Error("分配响应与原需求、货源或独立状态轴不一致，原请求坐标继续保留");
      }
      const rawReread = adapter.detailNoReplay
        ? await adapter.detailNoReplay(before.request_id) : await adapter.detail(before.request_id);
      const reread = validateMaterialRequestDetail(rawReread, before.request_id);
      const rereadLine = reread.lines.find((line) => line.request_line_id === allocationForm.lineId);
      if (reread.request_version !== result.current_request_version
          || reread.current_revision_id !== result.revision_id
          || reread.current_revision_no !== result.revision_no
          || !rereadLine || rereadLine.revision_id !== result.revision_id
          || rereadLine.revision_no !== result.revision_no
          || reread.states.allocation_status !== result.state_axes.allocation_status
          || !axesExceptAllocationMatch(before.states, reread.states)
          || JSON.stringify(reread.states) !== JSON.stringify(result.state_axes)) {
        throw new Error("分配事实与需求详情精确回读不一致，原请求坐标继续保留");
      }
      const rawAfterIdentity = adapter.loadIdentityNoReplay
        ? await adapter.loadIdentityNoReplay() : await adapter.loadIdentity();
      const afterIdentity = validateFormalMaterialRequestFreshIdentity(rawAfterIdentity);
      const rawAfterAccess = adapter.loadAccessNoReplay
        ? await adapter.loadAccessNoReplay() : await adapter.loadAccess();
      const afterAccess = validateFormalMaterialRequestAccess(rawAfterAccess);
      if (afterIdentity.person_id !== sentinel.person_id
          || afterIdentity.authorization_version !== sentinel.authorization_version
          || !exactScalarSnapshotMatches(afterAccess, freshAccess)
          || currentGeneration !== allocationGeneration.current) {
        throw new Error("核验期间身份、权限或页面上下文发生变化，原分配坐标继续保留");
      }
      allocationStore.clear(sentinel.x_request_id);
      onDetail(reread);
      setAllocationForm(null);
      setAllocationOptions(null);
      setAllocationMessage(`分配单 ${result.allocation_no} 已保存并精确回读，数量 ${result.allocated_qty}。占用、出库、发运、收货、入库、通知和对账均未被合并推进。`);
      onAllocationBlocking?.(false);
    } catch (caught) {
      if (currentGeneration === allocationGeneration.current) {
        setAllocationError(`${showError(caught)}${persisted ? "。结果核验完成前，请勿再次提交。" : ""}`);
        if (!persisted && allocationStore.read().kind === "missing") onAllocationBlocking?.(false);
      }
    } finally {
      if (currentGeneration === allocationGeneration.current) setAllocationRunning(false);
      allocationInFlight.current = false;
    }
  }

  async function recover() {
    if (inFlight.current) return;
    const stored = store.read();
    if (stored.kind !== "valid") {
      setError("供给恢复记录不可用，写入保持暂停");
      onBlocking(true);
      return;
    }
    const currentGeneration = generation.current;
    inFlight.current = true;
    setRunning(true);
    onBlocking(true);
    setError("");
    try {
      const outcome = await recoverSupplyCommand(adapter, store, stored.value,
        () => currentGeneration === generation.current);
      if (currentGeneration !== generation.current) return;
      const pending = registry.get(outcome.command.request_id);
      if (pending?.headers["X-Request-ID"] === stored.value.x_request_id) registry.confirm(pending.request_id, pending.signature);
      onDetail(outcome.detail);
      setForm(null);
      const task = outcome.detail.supply_tasks.find((row) => row.id === outcome.command.supply_task_id)!;
      setMessage(`此前供给操作已确认：${outcome.command.task_no}。当前计划状态：${STATUSES[task.status]}；计划记录不代表实物履约。`);
      onBlocking(false);
    } catch (caught) {
      if (currentGeneration === generation.current) setError(showError(caught));
    } finally {
      if (currentGeneration === generation.current) setRunning(false);
      inFlight.current = false;
    }
  }

  useEffect(() => {
    generation.current += 1;
    const stored = store.read();
    onBlocking(stored.kind !== "missing");
    if (stored.kind === "valid" && access) void recover();
    else if (stored.kind !== "missing" && stored.kind !== "valid") setError("供给恢复存储不可用，写入保持暂停");
    return () => { generation.current += 1; };
    // The stored operation is resumed when the authenticated page identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, store, access?.person_id, access?.authorization_version]);

  function open(action: SupplyAction, task: MaterialRequestSupplyTask | null = null) {
    if (!detail || !access || otherWriteBusy || otherWriteBlocked?.() || blocked || running || registry.get(detail.request_id)) {
      setError("当前需求存在待核验操作，请先完成结果核验");
      return;
    }
    if (action === "create_supply_task" ? !canCreate : !task?.allowed_actions.includes(action)) return;
    const line = detail.lines.find((row) => remaining(detail, row.request_line_id) > 0n);
    if (action === "create_supply_task" && !line) { setError("当前需求已无可新增的供给计划数量"); return; }
    setError("");
    setMessage("");
    setForm({ action, before: detail, task, lineId: task?.request_line_id || line!.request_line_id,
      supplyType: task?.supply_type || "star_replenishment", quantity: task?.expected_qty || "1",
      referenceNo: task?.reference_no || "", expectedDate: task?.expected_date || "", comment: "",
      status: action === "cancel_supply_task" ? "cancelled" : task?.status || "open" });
  }

  async function submit() {
    if (!form || !access || running || blocked || otherWriteBusy || otherWriteBlocked?.() || inFlight.current) return;
    const currentGeneration = generation.current;
    const before = form.before;
    let persisted = false;
    let preparedIntent: MaterialRequestMutationIntent | undefined;
    let persistedSentinel: SupplySentinel | undefined;
    let directPostRejection: unknown;
    inFlight.current = true;
    setRunning(true);
    setError("");
    onBlocking(true);
    try {
      const body = form.action === "create_supply_task" ? validateSupplyCreateInput({
        expected_request_version: before.request_version, request_line_id: form.lineId, supply_type: form.supplyType,
        expected_qty: quantityText(form.quantity), reference_no: form.referenceNo.trim() || null,
        expected_date: form.expectedDate || null, note: form.comment.trim(),
      }) : validateSupplyUpdateInput({
        expected_request_version: before.request_version, expected_task_version: form.task!.version,
        status: form.status,
        reference_no: form.action === "cancel_supply_task" ? form.task!.reference_no : form.referenceNo.trim() || null,
        expected_date: form.action === "cancel_supply_task" ? form.task!.expected_date : form.expectedDate || null,
        comment: form.comment.trim(),
      }, form.action);
      if ("request_line_id" in body && units(body.expected_qty) > remaining(before, body.request_line_id)) {
        throw new Error("计划数量超过当前明细未安排的批准余量");
      }
      const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
      if (identity.person_id !== access.person_id || identity.authorization_version !== access.authorization_version) {
        throw new Error("登录身份或授权已变化，请重新进入页面");
      }
      const fresh = validateMaterialRequestDetail(await adapter.detail(before.request_id));
      const freshTask = form.task ? fresh.supply_tasks.find((task) => task.id === form.task!.id) : null;
      if (fresh.request_version !== before.request_version
          || (form.action === "create_supply_task" ? !fresh.allowed_actions.includes(form.action)
            : freshTask?.version !== form.task!.version || !freshTask.allowed_actions.includes(form.action))) {
        throw new Error("需求或供给任务已变化，请返回详情重新检查");
      }
      if (currentGeneration !== generation.current || otherWriteBlocked?.()) return;
      const intent = registry.begin({ requestId: before.request_id, action: form.action,
        path: `/v1/material-requests/${before.request_id}/supply-tasks${form.task ? `/${form.task.id}` : ""}`,
        expectedVersion: before.request_version, body });
      preparedIntent = intent;
      const sentinel: SupplySentinel = { v: 1, kind: "material_request_supply", x_request_id: intent.headers["X-Request-ID"],
        person_id: access.person_id, authorization_version: access.authorization_version, request_id: before.request_id,
        action: form.action, request_version: before.request_version, task_id: form.task?.id || null,
        task_version: form.task?.version ?? null };
      store.persist(sentinel);
      persisted = true;
      persistedSentinel = sentinel;
      let rawResult: unknown;
      try {
        rawResult = await adapter.mutate(intent);
      } catch (caught) {
        directPostRejection = caught;
        throw caught;
      }
      const result = validateSupplyMutationResult(rawResult, {
        requestId: before.request_id, action: form.action, previousVersion: before.request_version,
        ...(form.task ? { taskId: form.task.id, previousTaskVersion: form.task.version } : {}),
      });
      const reread = validateMaterialRequestDetail(await adapter.detail(before.request_id));
      if (!supplyMutationMatchesDetail(result, before, reread, body)) throw new Error("供给操作回读结果未完全匹配，原请求坐标继续保留");
      const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
      if (afterIdentity.person_id !== sentinel.person_id || afterIdentity.authorization_version !== sentinel.authorization_version) {
        throw new Error("核验期间权限发生变化，原供给坐标继续保留");
      }
      if (currentGeneration !== generation.current) return;
      store.clear(sentinel.x_request_id);
      registry.confirm(before.request_id, intent.signature);
      onDetail(reread);
      setForm(null);
      setMessage(`供给计划 ${result.task_no} 已保存并核验，当前为${STATUSES[result.task_status]}。`);
      onBlocking(false);
    } catch (caught) {
      if (currentGeneration === generation.current) {
        let released = false;
        if (persisted && persistedSentinel && preparedIntent
            && caught === directPostRejection && isDefinitiveSupplyPostRejection(caught)) {
          try {
            const freshIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentity());
            const freshAccess = validateFormalMaterialRequestAccess(await adapter.loadAccess());
            if (freshIdentity.person_id !== persistedSentinel.person_id
                || freshIdentity.authorization_version !== persistedSentinel.authorization_version
                || !exactScalarSnapshotMatches(freshAccess, access)) {
              throw new Error("明确拒绝回收期间身份或权限已变化，原供给坐标继续保留");
            }
            if (currentGeneration !== generation.current) return;
            const stored = store.read();
            if (stored.kind !== "valid" || !exactScalarSnapshotMatches(stored.value, persistedSentinel)) {
              throw new Error("明确拒绝回收时供给坐标已变化，原阻断继续保留");
            }
            store.clear(persistedSentinel.x_request_id);
            registry.clearDefinitiveRejection(before.request_id, preparedIntent.signature);
            onBlocking(false);
            released = true;
          } catch (clearError) {
            setError(`${showError(caught)}。${showError(clearError)}。结果核验完成前，请勿再次提交。`);
          }
        }
        if (released) {
          setError(`${showError(caught)}。服务端已明确拒绝，本地请求坐标已安全解除。`);
        } else if (!(persisted && persistedSentinel && preparedIntent
            && caught === directPostRejection && isDefinitiveSupplyPostRejection(caught))) {
          setError(`${showError(caught)}${persisted ? "。结果核验完成前，请勿再次提交。" : ""}`);
        }
        if (!persisted && store.read().kind === "missing") {
          if (preparedIntent) registry.clearDefinitiveRejection(before.request_id, preparedIntent.signature);
          onBlocking(false);
        }
      }
    } finally {
      if (currentGeneration === generation.current) setRunning(false);
      inFlight.current = false;
    }
  }

  const field = <K extends keyof Form>(key: K, value: Form[K]) => setForm((current) => current ? { ...current, [key]: value } : current);
  const allocationField = <K extends keyof AllocationForm>(key: K, value: AllocationForm[K]) => {
    setAllocationForm((current) => current ? { ...current, [key]: value } : current);
  };
  return <section className="opening-detail-section" aria-label="供给计划">
    <header><div><h3>供给计划</h3><p>登记补货或跨区域供给计划、参考号和预计日期；实际分配、发运及收货分别跟踪。</p></div>
      {canCreate && <Button disabled={otherWriteBusy || running || blocked || allocationBlocked || allocationRunning} onClick={() => open("create_supply_task")}>新建供给计划</Button>}
    </header>
    {message && <div className="alert alert-info">{message}</div>}
    {error && <div className="alert alert-error">{error}</div>}
    {allocationMessage && <div className="alert alert-info">{allocationMessage}</div>}
    {allocationError && <div className="alert alert-error">{allocationError}</div>}
    {blocked && <div className="alert alert-warning">供给操作结果待核验，当前页面暂停其他写入。
      <Button disabled={running || read.kind !== "valid"} onClick={() => void recover()}>{running ? "正在核验" : "核验原供给操作"}</Button>
    </div>}
    {allocationBlocked && <div className="alert alert-warning">分配操作结果待核验，当前页面暂停其他写入。
      <Button disabled={allocationRunning || allocationRead.kind !== "valid"} onClick={() => void recoverAllocation()}>{allocationRunning ? "正在核验" : "核验原分配操作"}</Button>
    </div>}
    {detail && <div className="alert alert-info">当前分配状态：{ALLOCATION_STATUS_LABELS[detail.states.allocation_status]}。分配只记录货源事实，不会自动推进占用、拣货、出库、发运、签收、收货或入库。</div>}
    {detail?.supply_tasks.length ? <div className="table-wrap"><table>
      <thead><tr><th>任务</th><th>明细</th><th>计划类型</th><th>计划数量</th><th>参考号</th><th>预计日期</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>{detail.supply_tasks.map((task) => <tr key={task.id}>
        <td>{task.task_no}</td><td>{detail.lines.find((line) => line.request_line_id === task.request_line_id)?.line_no}</td>
        <td>{TYPES[task.supply_type]}</td><td>{task.expected_qty}</td><td>{task.reference_no || "—"}</td>
        <td>{task.expected_date || "—"}</td><td>{STATUSES[task.status]}</td><td>
          {task.allowed_actions.includes("update_supply_task") && <Button disabled={otherWriteBusy || running || blocked || allocationBlocked || allocationRunning} onClick={() => open("update_supply_task", task)}>更新计划</Button>}
          {task.allowed_actions.includes("cancel_supply_task") && <Button tone="secondary" disabled={otherWriteBusy || running || blocked || allocationBlocked || allocationRunning} onClick={() => open("cancel_supply_task", task)}>取消计划</Button>}
        </td></tr>)}</tbody>
    </table></div> : <p>{detail ? "暂无供给计划" : "选择需求查看供给计划"}</p>}
    {detail && canReadAllocationOptions && <section aria-label="货源候选" className="opening-detail-subsection">
      <header><div><h4>可用货源与分配</h4><p>先读取当前库存投影，再选择一个货源建立独立分配事实；占用和后续履约不会被自动推进。</p></div></header>
      <div className="table-wrap"><table><thead><tr><th>明细</th><th>物料</th><th>批准余量</th><th>操作</th></tr></thead><tbody>
        {detail.lines.filter((line) => ["approved", "partially_approved"].includes(line.status)).map((line) => <tr key={line.request_line_id}>
          <td>{line.line_no}</td><td>{line.material_id}</td><td>{line.final_approved_qty}</td><td>
            <Button tone="secondary" disabled={allocationLoading || allocationRunning || blocked || allocationBlocked || otherWriteBusy || otherWriteBlocked?.()} onClick={() => void showAllocationOptions(line.request_line_id)}>{allocationLoading ? "正在读取" : "查看可用货源"}</Button>
          </td>
        </tr>)}
      </tbody></table></div>
    </section>}
    {allocationOptions && <Modal title="可用货源候选" wide onClose={() => { if (!allocationLoading && !allocationRunning) setAllocationOptions(null); }}>
      <p>明细 {detail?.lines.find((line) => line.request_line_id === allocationOptions.lineId)?.line_no} · 账面游标 {allocationOptions.page.ledger_cursor} · 投影时间 {allocationOptions.page.projected_at || "—"}</p>
      {allocationOptions.page.items.length ? <div className="table-wrap"><table><thead><tr><th>库位</th><th>货主</th><th>状态</th><th>数量</th><th>余额版本</th><th>流水游标</th><th>操作</th></tr></thead><tbody>
        {allocationOptions.page.items.map((item) => <tr key={`${item.stock_account_id}:${item.balance_version}:${item.ledger_cursor}`}><td>{item.location_name}（{item.location_code}）</td><td>{item.owner_org_name}</td><td>{item.condition_code} · {item.availability_bucket}</td><td>{item.quantity} {item.base_unit}</td><td>{item.balance_version}</td><td>{item.ledger_cursor}</td><td>
          <Button disabled={allocationRunning || blocked || allocationBlocked || otherWriteBusy || otherWriteBlocked?.()} onClick={() => chooseAllocation(item)}>选择并分配</Button>
        </td></tr>)}
      </tbody></table></div> : <p>当前没有满足条件的可用正余额货源。</p>}
    </Modal>}
    {allocationForm && <Modal title="确认库存分配" onClose={() => { if (!allocationRunning && !allocationBlocked) setAllocationForm(null); }}>
      <div className="form-stack">
        <p>需求 {allocationForm.before.request_no} · 明细 {allocationForm.before.lines.find((line) => line.request_line_id === allocationForm.lineId)?.line_no}</p>
        <div className="alert alert-info">来源：{allocationForm.option.owner_org_name} / {allocationForm.option.location_name}（{allocationForm.option.location_code}）；当前可用 {allocationForm.option.quantity} {allocationForm.option.base_unit}。</div>
        {allocationError && <div className="alert alert-error">{allocationError}</div>}
        <Field label="分配数量"><input aria-label="分配数量" inputMode="decimal" disabled={allocationRunning || allocationBlocked} value={allocationForm.quantity} onChange={(event) => allocationField("quantity", event.target.value)} /></Field>
        <Field label="分配串码（仅 SN 物料；每行一个 UUID）"><textarea aria-label="分配串码" disabled={allocationRunning || allocationBlocked} value={allocationForm.serialIds} onChange={(event) => allocationField("serialIds", event.target.value)} /></Field>
        <div className="alert alert-warning">提交后只建立分配事实。占用、拣货、出库、发运、物流签收、OAM 收货、RSC/个人仓入库、通知和对账继续分别处理。</div>
        <div className="form-actions">
          <Button tone="secondary" disabled={allocationRunning || allocationBlocked} onClick={() => setAllocationForm(null)}>返回检查</Button>
          <Button disabled={allocationRunning || allocationBlocked || blocked || otherWriteBusy || otherWriteBlocked?.()} onClick={() => void submitAllocation()}>{allocationRunning ? "正在精确回读" : "确认分配"}</Button>
          {allocationBlocked && <Button disabled={allocationRunning} onClick={() => void recoverAllocation()}>核验原分配操作</Button>}
        </div>
      </div>
    </Modal>}
    {form && <Modal title={form.action === "create_supply_task" ? "新建供给计划" : form.action === "cancel_supply_task" ? "取消供给计划" : "更新供给计划"}
      onClose={() => { if (!running && !blocked && !allocationBlocked) setForm(null); }}>
      <div className="form-stack">
        <p>需求 {form.before.request_no} · 本次操作仅修改供给计划。</p>
        {error && <div className="alert alert-error">{error}</div>}
        <Field label="需求明细"><select aria-label="供给需求明细" disabled={running || blocked || allocationBlocked || Boolean(form.task)} value={form.lineId} onChange={(event) => field("lineId", event.target.value)}>
          {form.before.lines.filter((line) => form.task || remaining(form.before, line.request_line_id) > 0n).map((line) => <option key={line.request_line_id} value={line.request_line_id}>明细 {line.line_no} · 已批准 {line.final_approved_qty}</option>)}
        </select></Field>
        <Field label="供给类型"><select aria-label="供给类型" disabled={running || blocked || allocationBlocked || Boolean(form.task)} value={form.supplyType} onChange={(event) => field("supplyType", event.target.value as Form["supplyType"])}>
          {Object.entries(TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></Field>
        <Field label="计划数量"><input aria-label="供给计划数量" inputMode="decimal" disabled={running || blocked || allocationBlocked || Boolean(form.task)} value={form.quantity} onChange={(event) => field("quantity", event.target.value)} /></Field>
        <Field label="参考号"><input aria-label="供给参考号" maxLength={160} disabled={running || blocked || allocationBlocked || form.action === "cancel_supply_task"} value={form.referenceNo} onChange={(event) => field("referenceNo", event.target.value)} /></Field>
        <Field label="预计日期"><input aria-label="供给预计日期" type="date" disabled={running || blocked || allocationBlocked || form.action === "cancel_supply_task"} value={form.expectedDate} onChange={(event) => field("expectedDate", event.target.value)} /></Field>
        {form.task && <Field label="计划状态"><select aria-label="供给计划状态" disabled={running || blocked || allocationBlocked || form.action === "cancel_supply_task"} value={form.status} onChange={(event) => field("status", event.target.value as Form["status"])}>
          {Object.entries(STATUSES).filter(([value]) => form.action === "cancel_supply_task" ? value === "cancelled"
            : value !== "cancelled" && (value !== "open" || form.task?.status === "open")
              && (value !== "reference_registered" || form.task?.status !== "awaiting_supply"))
            .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></Field>}
        <Field label={form.action === "create_supply_task" ? "计划备注" : "处理原因"}><textarea aria-label="供给处理说明" maxLength={4000} disabled={running || blocked || allocationBlocked} value={form.comment} onChange={(event) => field("comment", event.target.value)} /></Field>
        <div className="form-actions"><Button tone="secondary" disabled={running || blocked || allocationBlocked} onClick={() => setForm(null)}>返回检查</Button>
          <Button disabled={running || blocked || allocationBlocked || otherWriteBusy || otherWriteBlocked?.()} onClick={() => void submit()}>{running ? "正在核验" : "确认保存供给计划"}</Button>
          {blocked && <Button disabled={running} onClick={() => void recover()}>核验原供给操作</Button>}
        </div>
      </div>
    </Modal>}
  </section>;
}
