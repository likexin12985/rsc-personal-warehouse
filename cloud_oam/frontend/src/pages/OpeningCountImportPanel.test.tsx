// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OpeningCountImportPanel from "./OpeningCountImportPanel";
import type { OpeningImportWorkflow, OpeningImportWorkflowResult, OpeningImportManagementResult, OpeningImportSealResult } from "../openingCountImportWorkflow";
import type { OpeningImportRecordRead } from "../openingCountImportRecoveryStore";
import type { OpeningStocktakeTaskDetail } from "../formalOpeningStocktake";

const id = (n: number) => `92000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const actor = { person_id: id(4), authorization_version: 7 };
const detail = { task_id: id(1), status: "counting", allowed_actions: ["count"],
  current_round: { round_id: id(2), status: "counting" },
  scopes: [{ scope_id: id(3), scope_no: 1, location_id: id(8), assigned_to_me: true, completion_status: "pending" }],
} as OpeningStocktakeTaskDetail;
function ready(): OpeningImportWorkflowResult {
  const record = { v: 1 as const, task_id: id(1), round_id: id(2), scope_id: id(3), actor_person_id: id(4),
    actor_authorization_version: 7, source_sha256: "ab".repeat(32), size_bytes: 12, upload_key: "opening-upload-123456789",
    import_key: "opening-import-123456789", file_id: id(5), job_id: id(6), phase: "job_bound" as const };
  return { record, review: { job_id: id(6), task_id: id(1), round_id: id(2), scope_id: id(3),
    actor_person_id: id(4), authorization_version: 7, source_file_id: id(5), source_sha256: record.source_sha256,
    size_bytes: 12, original_filename: "原盘点.xlsx", can_confirm: true, result: { job_id: id(6), status: "awaiting_confirmation",
      completion_id: null, row_count: 2, error_count: 0, error_file_available: false, failure_code: null, replayed: false } } };
}
afterEach(cleanup);
function fixture(initial: OpeningImportWorkflowResult | null = null, enabled = true) {
  let value: OpeningImportRecordRead = initial ? { kind: "valid", value: initial.record } : { kind: "missing" };
  const managed = (): OpeningImportManagementResult => ({ record: initial?.record ?? ready().record,
    review: { schema_version: "rsc.opening_import_management_recovery.v1",
      task_id: id(1), round_id: id(2), scope_id: id(3), actor_person_id: id(4), authorization_version: 7,
      source_file_id: id(5), source_sha256: ready().record.source_sha256, size_bytes: 12, job_id: id(6),
      reviewer_person_id: id(9), reviewer_authorization_version: 12,
      status: "succeeded", completion_id: id(7), terminal_audit_id: id(10), terminal_verified: true, automatic_retry_allowed: false } });
  const sealed = (): OpeningImportSealResult => ({ record: value.kind === "valid" ? value.value : ready().record,
    proof: { schema_version: "rsc.opening_import_seal.v1", task_id: id(1), round_id: id(2), scope_id: id(3),
      actor_person_id: id(4), authorization_version: 7, source_file_id: id(5), source_sha256: ready().record.source_sha256, size_bytes: 12,
      reviewer_person_id: id(9), reviewer_authorization_version: 12, seal_id: id(11), terminal_audit_id: id(12),
      permanent_nonexecution: true, automatic_retry_allowed: false, source_object_may_exist: true } });
  const workflow = {
    store: { read: () => value }, available: vi.fn(async () => enabled),
    start: vi.fn(async () => { const next = ready(); value = { kind: "valid", value: next.record }; return next; }),
    resume: vi.fn(async () => initial ?? ready()),
    decide: vi.fn(async (_task: string, _review: unknown, action: "confirm" | "cancel") => {
      let next = ready();
      next = { ...next, record: { ...next.record, phase: action === "confirm" ? "confirmation_requested" : "cancellation_requested" } };
      next = { ...next, review: { ...next.review, can_confirm: false, result: { ...next.review.result,
        status: action === "confirm" ? "succeeded" : "cancelled", completion_id: action === "confirm" ? id(7) : null,
        failure_code: action === "cancel" ? "opening_import_cancelled" : null } } };
      value = { kind: "valid", value: next.record }; return next;
    }),
    finish: vi.fn(async () => { value = { kind: "missing" }; return { ...ready().review.result, status: "succeeded", completion_id: id(7) }; }),
    inspectManagement: vi.fn(async () => managed()),
    finishManagement: vi.fn(async () => { value = { kind: "missing" }; return managed().review; }),
    requestSeal: vi.fn(async () => {
      if (value.kind !== "valid") throw new Error("missing");
      value = { kind: "valid", value: { ...value.value, phase: "seal_requested" } }; return sealed();
    }),
    inspectSeal: vi.fn(async () => sealed()),
    finishSeal: vi.fn(async () => { const proof = sealed().proof; value = { kind: "missing" }; return proof; }),
    bindAcceptedSeal: vi.fn(async () => {
      const bound = { ...ready().record, phase: "seal_conflict" as const }; value = { kind: "valid", value: bound }; return bound;
    }),
    discardPrepared: vi.fn(async () => { value = { kind: "missing" }; }),
    errorDownload: vi.fn(async () => ({ job_id: id(6), file_id: id(5), filename: "errors.xlsx", sha256: "ab".repeat(32), size_bytes: 12,
      download: { method: "GET", url: "https://private.example.test/errors?signature=opaque", expires_at: new Date(Date.now() + 60000).toISOString() } })),
  };
  const onFinished = vi.fn(), onBlockedChange = vi.fn(), onManagementFinished = vi.fn(), onSealFinished = vi.fn();
  const props = { detail, actor, onFinished, onBlockedChange, onManagementFinished, onSealFinished, workflow: workflow as unknown as OpeningImportWorkflow };
  return { workflow, props, onFinished, onBlockedChange, onManagementFinished, onSealFinished, managed, sealed, setRead: (next: OpeningImportRecordRead) => { value = next; } };
}
async function start() {
  await waitFor(() => expect((screen.getByLabelText("导入盘点范围") as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("导入盘点范围"), { target: { value: id(3) } });
  fireEvent.change(screen.getByLabelText("导入 XLSX 文件"), { target: { files: [new File(["test"], "count.xlsx")] } });
  fireEvent.click(screen.getByRole("button", { name: "上传并预校验" }));
  await screen.findByText("预校验通过，等待确认");
}
describe("opening import product panel", () => {
  const manager = { person_id: id(9), authorization_version: 12 };
  function unresolved(phase: "intent_requested" | "seal_requested" = "intent_requested") {
    return { ...ready().record, file_id: null, job_id: null, phase };
  }
  it("requires separate review of the original request and permanent stop proof before releasing a block", async () => {
    const f = fixture(); f.setRead({ kind: "valid", value: unresolved() });
    render(<OpeningCountImportPanel {...f.props} actor={manager} canCount={false} canManage />);
    const request = await screen.findByRole("button", { name: "永久停止原导入" }) as HTMLButtonElement;
    expect(request.disabled).toBe(true);
    await waitFor(() => expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(request);
    await screen.findByText("原导入已永久停止");
    expect(f.workflow.requestSeal).toHaveBeenCalledWith(id(1), unresolved());
    expect(f.onSealFinished).not.toHaveBeenCalled(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByText(/文件可能仍然存在/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "永久停止原导入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "恢复原导入" })).toBeNull();
    const finish = screen.getByRole("button", { name: "复核停止结果并解除本地阻塞" }) as HTMLButtonElement;
    expect(finish.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(finish);
    await waitFor(() => expect(f.onSealFinished).toHaveBeenCalledTimes(1));
    expect(f.onBlockedChange).toHaveBeenLastCalledWith(false);
    expect(f.onFinished).not.toHaveBeenCalled(); expect(f.onManagementFinished).not.toHaveBeenCalled();
    expect(f.workflow.start).not.toHaveBeenCalled(); expect(f.workflow.decide).not.toHaveBeenCalled();
  });
  it("keeps an unknown stop result across reload with read-only recovery and no ordinary resume", async () => {
    const f = fixture(); f.setRead({ kind: "valid", value: unresolved("seal_requested") });
    f.workflow.inspectSeal.mockRejectedValue(new Error("404"));
    render(<OpeningCountImportPanel {...f.props} actor={manager} canCount={false} canManage />);
    const inspect = await screen.findByRole("button", { name: "核验原停止结果" }) as HTMLButtonElement;
    await waitFor(() => expect(inspect.disabled).toBe(false)); fireEvent.click(inspect);
    await screen.findByText(/本次停止结果尚未核验/);
    expect(screen.queryByRole("button", { name: "永久停止原导入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "复核停止结果并解除本地阻塞" })).toBeNull();
    expect(f.workflow.requestSeal).not.toHaveBeenCalled(); expect(f.workflow.finishSeal).not.toHaveBeenCalled();
    expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
  });
  it.each(["storage", "identity", "permission"])("invalidates the displayed stop proof after %s changes", async change => {
    const f = fixture(); f.setRead({ kind: "valid", value: unresolved("seal_requested") });
    const page = render(<OpeningCountImportPanel {...f.props} actor={manager} canCount={false} canManage />);
    const inspect = await screen.findByRole("button", { name: "核验原停止结果" });
    await waitFor(() => expect((inspect as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(inspect);
    await screen.findByText("原导入已永久停止"); fireEvent.click(screen.getByRole("checkbox"));
    if (change === "storage") fireEvent(window, new StorageEvent("storage"));
    else page.rerender(<OpeningCountImportPanel {...f.props} actor={{ ...manager, authorization_version: change === "identity" ? 13 : 12 }} canCount={false} canManage={change !== "permission"} />);
    expect(screen.queryByRole("button", { name: "复核停止结果并解除本地阻塞" })).toBeNull();
    expect(f.workflow.finishSeal).not.toHaveBeenCalled();
  });
  it("keeps the record when the final stop proof cannot be verified", async () => {
    const f = fixture(); f.setRead({ kind: "valid", value: unresolved("seal_requested") });
    f.workflow.finishSeal.mockRejectedValue(new Error("revoked"));
    render(<OpeningCountImportPanel {...f.props} actor={manager} canCount={false} canManage />);
    const inspect = await screen.findByRole("button", { name: "核验原停止结果" });
    await waitFor(() => expect((inspect as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(inspect);
    await screen.findByText("原导入已永久停止"); fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "复核停止结果并解除本地阻塞" }));
    await screen.findByText(/本次停止结果尚未核验/);
    expect(f.onSealFinished).not.toHaveBeenCalled(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
  });
  it("does not offer stop or stop recovery to a counting-only user", async () => {
    const f = fixture(); f.setRead({ kind: "valid", value: unresolved("seal_requested") });
    render(<OpeningCountImportPanel {...f.props} />);
    await screen.findByText(/原导入已请求永久停止/);
    expect(screen.queryByRole("button", { name: "永久停止原导入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "核验原停止结果" })).toBeNull();
    expect(screen.queryByRole("button", { name: "恢复原导入" })).toBeNull();
  });
  it("lets a manager bind an accepted active task without releasing its block", async () => {
    const original = { ...ready(), record: { ...ready().record, phase: "seal_requested" as const, job_id: null } };
    const f = fixture(original), shown = f.managed();
    f.workflow.inspectManagement.mockResolvedValue({ ...shown, review: { ...shown.review, status: "awaiting_confirmation",
      completion_id: null, terminal_audit_id: null, terminal_verified: false } });
    render(<OpeningCountImportPanel {...f.props} actor={manager} canCount={false} canManage />);
    const inspect = await screen.findByRole("button", { name: "管理核验原导入" });
    await waitFor(() => expect((inspect as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(inspect);
    const bind = await screen.findByRole("button", { name: "关联已受理的原任务" }) as HTMLButtonElement;
    expect(bind.disabled).toBe(true); fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(bind);
    await screen.findByText(/原任务已受理，永久停止未生效/);
    expect(f.workflow.bindAcceptedSeal).toHaveBeenCalledTimes(1); expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
    expect(f.onSealFinished).not.toHaveBeenCalled(); expect(screen.queryByRole("button", { name: "永久停止原导入" })).toBeNull();
  });
  async function inspectAsManager(f: ReturnType<typeof fixture>) {
    const page = render(<OpeningCountImportPanel {...f.props} actor={{ person_id: id(9), authorization_version: 12 }} canCount={false} canManage />);
    const inspect = await screen.findByRole("button", { name: "管理核验原导入" });
    await waitFor(() => expect((inspect as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(inspect); return page;
  }
  it("gives a management-only reviewer a distinct explicit terminal recovery flow", async () => {
    const f = fixture(ready()); await inspectAsManager(f);
    await screen.findByText("管理核验：实盘计数已提交");
    expect(screen.queryByRole("button", { name: "恢复原导入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "确认导入计数" })).toBeNull();
    expect(screen.queryByRole("button", { name: "获取错误报告" })).toBeNull();
    expect(screen.queryByText("原盘点.xlsx")).toBeNull();
    const finish = screen.getByRole("button", { name: "复核终态并解除本地阻塞" }) as HTMLButtonElement;
    expect(finish.disabled).toBe(true); expect(f.workflow.finishManagement).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(finish);
    await waitFor(() => expect(f.onManagementFinished).toHaveBeenCalledTimes(1));
    expect(f.workflow.finishManagement).toHaveBeenCalledWith(id(1), f.managed());
    expect(f.onFinished).not.toHaveBeenCalled(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(false);
    expect(f.workflow.start).not.toHaveBeenCalled(); expect(f.workflow.decide).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "上传并预校验" })).toBeNull();
  });
  it("shows active management inspection without a clear or retry action", async () => {
    const f = fixture(ready()); const shown = f.managed();
    f.workflow.inspectManagement.mockResolvedValue({ ...shown, review: { ...shown.review, status: "prevalidating",
      completion_id: null, terminal_audit_id: null, terminal_verified: false } });
    await inspectAsManager(f); await screen.findByText("管理核验：正在预校验");
    expect(screen.queryByRole("button", { name: "复核终态并解除本地阻塞" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
  });
  it("retains a local block when terminal revalidation fails", async () => {
    const f = fixture(ready()); f.workflow.finishManagement.mockRejectedValue(new Error("forbidden"));
    await inspectAsManager(f); await screen.findByText("管理核验：实盘计数已提交");
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", { name: "复核终态并解除本地阻塞" }));
    await screen.findByText(/本次操作结果尚未核验/);
    expect(f.onManagementFinished).not.toHaveBeenCalled(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("button", { name: "复核终态并解除本地阻塞" })).toBeNull();
  });
  it.each(["storage", "identity", "permission"])("hides management proof after %s changes", async change => {
    const f = fixture(ready()); const page = await inspectAsManager(f);
    await screen.findByText("管理核验：实盘计数已提交"); fireEvent.click(screen.getByRole("checkbox"));
    if (change === "storage") fireEvent(window, new StorageEvent("storage"));
    else page.rerender(<OpeningCountImportPanel {...f.props} canCount={false}
      actor={{ person_id: id(9), authorization_version: change === "identity" ? 13 : 12 }} canManage={change !== "permission"} />);
    expect(screen.queryByRole("button", { name: "复核终态并解除本地阻塞" })).toBeNull();
    expect(f.workflow.finishManagement).not.toHaveBeenCalled();
  });
  it("does not offer management access without the management capability", async () => {
    const f = fixture(ready()); render(<OpeningCountImportPanel {...f.props} />);
    await screen.findByText(/已有导入记录/);
    expect(screen.queryByRole("button", { name: "管理核验原导入" })).toBeNull();
  });
  it("offers an explicit unsent-draft exit even with imports disabled, without pretending a server cancellation", async () => {
    const f = fixture(null, false);
    f.setRead({ kind: "valid", value: { ...ready().record, phase: "prepared", file_id: null, job_id: null } });
    const page = render(<OpeningCountImportPanel {...f.props} />);
    await screen.findByText(/导入当前未启用/);
    fireEvent.click(screen.getByRole("button", { name: "放弃未发送草稿" }));
    await waitFor(() => expect(page.container.textContent).toBe(""));
    expect(f.workflow.discardPrepared).toHaveBeenCalledWith(id(1));
    expect(f.workflow.decide).not.toHaveBeenCalled(); expect(f.workflow.start).not.toHaveBeenCalled();
    expect(f.onFinished).not.toHaveBeenCalled(); expect(f.onBlockedChange).toHaveBeenLastCalledWith(false);
  });
  it.each(["intent_requested", "upload_started", "job_requested"] as const)("does not offer discard for %s", async phase => {
    const f = fixture(); f.setRead({ kind: "valid", value: { ...ready().record, phase,
      file_id: phase === "intent_requested" ? null : id(5), job_id: null } });
    render(<OpeningCountImportPanel {...f.props} />);
    await screen.findByText(/已有导入记录/);
    expect(screen.queryByRole("button", { name: "放弃未发送草稿" })).toBeNull();
    expect(f.workflow.discardPrepared).not.toHaveBeenCalled();
  });
  it("requires scope, file and explicit review checkbox before count confirmation", async () => {
    const f = fixture(); render(<OpeningCountImportPanel {...f.props} />);
    expect((screen.getByRole("button", { name: "上传并预校验" }) as HTMLButtonElement).disabled).toBe(true);
    await start();
    expect(f.workflow.start).toHaveBeenCalledWith(id(1), id(2), id(3), expect.any(File));
    expect(f.workflow.decide).not.toHaveBeenCalled();
    const confirm = screen.getByRole("button", { name: "确认导入计数" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText("原盘点.xlsx")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(confirm);
    await screen.findByText("实盘计数已提交");
    expect(f.workflow.decide).toHaveBeenCalledTimes(1);
    expect(f.workflow.decide.mock.calls[0][2]).toBe("confirm");
    expect(screen.getByText(/尚不代表区域复核/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "已核验，返回盘点任务" }));
    await waitFor(() => expect(f.onFinished).toHaveBeenCalledTimes(1));
  });
  it("after reload only recovers the existing task and hides repeat confirm for an uncertain confirmation", async () => {
    const original = ready(); const pending: OpeningImportWorkflowResult = { ...original, record: { ...original.record, phase: "confirmation_requested" } };
    const f = fixture(pending); render(<OpeningCountImportPanel {...f.props} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "恢复原导入" }) as HTMLButtonElement).disabled).toBe(false));
    expect(f.workflow.start).not.toHaveBeenCalled(); expect(f.workflow.decide).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "恢复原导入" }));
    await screen.findByText(/原确认结果待核验/);
    expect(screen.queryByRole("button", { name: "确认导入计数" })).toBeNull();
    expect(f.workflow.resume).toHaveBeenCalledWith(id(1), undefined);
    expect(f.workflow.decide).not.toHaveBeenCalled();
  });
  it("retains unknown results with recovery only and never offers discard/restart", async () => {
    const f = fixture(ready()); f.workflow.resume.mockRejectedValue(new Error("private network detail"));
    render(<OpeningCountImportPanel {...f.props} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "恢复原导入" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "恢复原导入" }));
    await screen.findByText(/本次操作结果尚未核验/);
    expect(screen.queryByText("private network detail")).toBeNull();
    expect(screen.queryByRole("button", { name: "上传并预校验" })).toBeNull();
    expect(screen.queryByRole("button", { name: "已核验，返回盘点任务" })).toBeNull();
    expect(f.workflow.finish).not.toHaveBeenCalled();
  });
  it("hides previous identity's source and signed link immediately after account change", async () => {
    const original = ready(); const failed: OpeningImportWorkflowResult = { ...original, review: { ...original.review, can_confirm: false,
      result: { ...original.review.result, status: "failed", error_count: 1, error_file_available: true, failure_code: "opening_import_prevalidation_failed" } } };
    const f = fixture(failed); const page = render(<OpeningCountImportPanel {...f.props} />);
    await waitFor(() => expect((screen.getByRole("button", { name: "恢复原导入" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "恢复原导入" }));
    fireEvent.click(await screen.findByRole("button", { name: "获取错误报告" }));
    const link = await screen.findByRole("link", { name: "下载错误报告" });
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
    page.rerender(<OpeningCountImportPanel {...f.props} actor={{ ...actor, person_id: id(9) }} />);
    expect(screen.queryByRole("link", { name: "下载错误报告" })).toBeNull();
    expect(screen.queryByText("原盘点.xlsx")).toBeNull();
    expect(screen.getByText(/原导入属于不同身份/)).toBeTruthy();
  });
  it("renders no importer when disabled and no recovery record exists", async () => {
    const f = fixture(null, false); const page = render(<OpeningCountImportPanel {...f.props} />);
    await waitFor(() => expect(page.container.textContent).toBe(""));
    expect(f.workflow.start).not.toHaveBeenCalled();
  });
  it("keeps pending records visible and blocked when the feature is disabled", async () => {
    const f = fixture(ready(), false); render(<OpeningCountImportPanel {...f.props} />);
    await screen.findByText(/导入当前未启用/);
    expect((screen.getByRole("button", { name: "恢复原导入" }) as HTMLButtonElement).disabled).toBe(true);
    expect(f.onBlockedChange).toHaveBeenCalledWith(true);
  });
  it("storage changes invalidate a displayed review instead of confirming stale data", async () => {
    const f = fixture(); render(<OpeningCountImportPanel {...f.props} />); await start();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent(window, new StorageEvent("storage"));
    expect(screen.queryByRole("button", { name: "确认导入计数" })).toBeNull();
    expect(f.workflow.decide).not.toHaveBeenCalled();
  });
});
