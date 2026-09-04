import { useEffect, useRef, useState } from "react";
import {
  type FormalMaterialRequestAccess, type FormalMaterialRequestAdapter,
  validateFormalMaterialRequestFreshIdentity,
} from "./formalMaterialRequestAdapter";
import {
  MaterialRequestIntentRegistry, type MaterialRequestDetail, type MaterialRequestSupplyTask, type MaterialRequestMutationIntent,
  validateMaterialRequestDetail,
} from "./formalMaterialRequests";
import {
  type SupplyAction, type SupplyCreateInput, type SupplyUpdateInput,
  supplyMutationMatchesDetail, validateSupplyCreateInput, validateSupplyMutationResult, validateSupplyUpdateInput,
} from "./formalMaterialRequestSupply";
import { type SupplyRecoveryStore, type SupplySentinel, recoverSupplyCommand } from "./materialRequestSupplyRecovery";
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

export default function FormalMaterialRequestSupplyPanel({
  adapter, access, detail, store, registry, otherWriteBusy, onBlocking, onDetail,
}: {
  adapter: FormalMaterialRequestAdapter; access: FormalMaterialRequestAccess | null; detail: MaterialRequestDetail | null;
  store: SupplyRecoveryStore; registry: MaterialRequestIntentRegistry; otherWriteBusy: boolean;
  onBlocking: (blocked: boolean) => void; onDetail: (detail: MaterialRequestDetail) => void;
}) {
  const [form, setForm] = useState<Form | null>(null);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const inFlight = useRef(false);
  const read = store.read();
  const blocked = read.kind !== "missing";
  const canCreate = Boolean(detail?.allowed_actions.includes("create_supply_task"));

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
    if (!detail || !access || otherWriteBusy || blocked || running || registry.get(detail.request_id)) {
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
    if (!form || !access || running || blocked || otherWriteBusy || inFlight.current) return;
    const currentGeneration = generation.current;
    const before = form.before;
    let persisted = false;
    let preparedIntent: MaterialRequestMutationIntent | undefined;
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
      if (currentGeneration !== generation.current) return;
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
      const result = validateSupplyMutationResult(await adapter.mutate(intent), {
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
        setError(`${showError(caught)}${persisted ? "。结果核验完成前，请勿再次提交。" : ""}`);
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
  return <section className="opening-detail-section" aria-label="供给计划">
    <header><div><h3>供给计划</h3><p>登记补货或跨区域供给计划、参考号和预计日期；实际分配、发运及收货分别跟踪。</p></div>
      {canCreate && <Button disabled={otherWriteBusy || running || blocked} onClick={() => open("create_supply_task")}>新建供给计划</Button>}
    </header>
    {message && <div className="alert alert-info">{message}</div>}
    {error && <div className="alert alert-error">{error}</div>}
    {blocked && <div className="alert alert-warning">供给操作结果待核验，当前页面暂停其他写入。
      <Button disabled={running || read.kind !== "valid"} onClick={() => void recover()}>{running ? "正在核验" : "核验原供给操作"}</Button>
    </div>}
    {detail?.supply_tasks.length ? <div className="table-wrap"><table>
      <thead><tr><th>任务</th><th>明细</th><th>计划类型</th><th>计划数量</th><th>参考号</th><th>预计日期</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>{detail.supply_tasks.map((task) => <tr key={task.id}>
        <td>{task.task_no}</td><td>{detail.lines.find((line) => line.request_line_id === task.request_line_id)?.line_no}</td>
        <td>{TYPES[task.supply_type]}</td><td>{task.expected_qty}</td><td>{task.reference_no || "—"}</td>
        <td>{task.expected_date || "—"}</td><td>{STATUSES[task.status]}</td><td>
          {task.allowed_actions.includes("update_supply_task") && <Button disabled={otherWriteBusy || running || blocked} onClick={() => open("update_supply_task", task)}>更新计划</Button>}
          {task.allowed_actions.includes("cancel_supply_task") && <Button tone="secondary" disabled={otherWriteBusy || running || blocked} onClick={() => open("cancel_supply_task", task)}>取消计划</Button>}
        </td></tr>)}</tbody>
    </table></div> : <p>{detail ? "暂无供给计划" : "选择需求查看供给计划"}</p>}
    {form && <Modal title={form.action === "create_supply_task" ? "新建供给计划" : form.action === "cancel_supply_task" ? "取消供给计划" : "更新供给计划"}
      onClose={() => { if (!running && !blocked) setForm(null); }}>
      <div className="form-stack">
        <p>需求 {form.before.request_no} · 本次操作仅修改供给计划。</p>
        {error && <div className="alert alert-error">{error}</div>}
        <Field label="需求明细"><select aria-label="供给需求明细" disabled={running || blocked || Boolean(form.task)} value={form.lineId} onChange={(event) => field("lineId", event.target.value)}>
          {form.before.lines.filter((line) => form.task || remaining(form.before, line.request_line_id) > 0n).map((line) => <option key={line.request_line_id} value={line.request_line_id}>明细 {line.line_no} · 已批准 {line.final_approved_qty}</option>)}
        </select></Field>
        <Field label="供给类型"><select aria-label="供给类型" disabled={running || blocked || Boolean(form.task)} value={form.supplyType} onChange={(event) => field("supplyType", event.target.value as Form["supplyType"])}>
          {Object.entries(TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></Field>
        <Field label="计划数量"><input aria-label="供给计划数量" inputMode="decimal" disabled={running || blocked || Boolean(form.task)} value={form.quantity} onChange={(event) => field("quantity", event.target.value)} /></Field>
        <Field label="参考号"><input aria-label="供给参考号" maxLength={160} disabled={running || blocked || form.action === "cancel_supply_task"} value={form.referenceNo} onChange={(event) => field("referenceNo", event.target.value)} /></Field>
        <Field label="预计日期"><input aria-label="供给预计日期" type="date" disabled={running || blocked || form.action === "cancel_supply_task"} value={form.expectedDate} onChange={(event) => field("expectedDate", event.target.value)} /></Field>
        {form.task && <Field label="计划状态"><select aria-label="供给计划状态" disabled={running || blocked || form.action === "cancel_supply_task"} value={form.status} onChange={(event) => field("status", event.target.value as Form["status"])}>
          {Object.entries(STATUSES).filter(([value]) => form.action === "cancel_supply_task" ? value === "cancelled"
            : value !== "cancelled" && (value !== "open" || form.task?.status === "open")
              && (value !== "reference_registered" || form.task?.status !== "awaiting_supply"))
            .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></Field>}
        <Field label={form.action === "create_supply_task" ? "计划备注" : "处理原因"}><textarea aria-label="供给处理说明" maxLength={4000} disabled={running || blocked} value={form.comment} onChange={(event) => field("comment", event.target.value)} /></Field>
        <div className="form-actions"><Button tone="secondary" disabled={running || blocked} onClick={() => setForm(null)}>返回检查</Button>
          <Button disabled={running || blocked || otherWriteBusy} onClick={() => void submit()}>{running ? "正在核验" : "确认保存供给计划"}</Button>
          {blocked && <Button disabled={running} onClick={() => void recover()}>核验原供给操作</Button>}
        </div>
      </div>
    </Modal>}
  </section>;
}
