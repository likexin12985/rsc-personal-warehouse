// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FormalFileUploadClient } from "../FormalFileUploadField";
import type { FormalFilePurpose, FormalUploadFile } from "../formalFileUpload";
import type { FormalStocktakeAdapter } from "../formalStocktakeAdapter";
import {
  createFormalStocktakePostRecoveryStore,
  type FormalStocktakePostLockManager,
} from "../formalStocktakePostRecoveryStore";
import FormalStocktakesPage from "./FormalStocktakes";

const TASK = "10000000-0000-4000-8000-000000000001";
const PERSON = "01000000-0000-4000-8000-000000000001";
const REGION = "20000000-0000-4000-8000-000000000001";
const SCOPE = "30000000-0000-4000-8000-000000000001";
const SCOPE_2 = "30000000-0000-4000-8000-000000000002";
const LOCATION = "40000000-0000-4000-8000-000000000001";
const LOCATION_2 = "40000000-0000-4000-8000-000000000002";
const ROUND = "50000000-0000-4000-8000-000000000001";
const EVIDENCE_1 = "90000000-0000-4000-8000-000000000001";
const EVIDENCE_2 = "90000000-0000-4000-8000-000000000002";
const FILE_SHA = "cd".repeat(32);
const ASSIGNEE_USER = "engineer-001";

class PostMemoryStorage {
  private readonly values = new Map<string, string>();
  get length() { return this.values.size; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

const postLocks: FormalStocktakePostLockManager = {
  async request(_name, _options, callback) { return callback({}); },
};

function axes() { return { count_status: "not_started", difference_status: "not_ready", region_review_status: "not_ready", headquarters_review_status: "not_ready", recount_status: "not_required", posting_status: "not_posted", reconciliation_status: "not_reconciled", closure_status: "open" }; }
function detail() { return { schema_version: "1.0", task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", region_org_id: REGION, status: "draft", version: 0, blind_count: true, current_round_no: 0, cutoff_ledger_cursor: null, cutoff_at: null, issued_at: null, frozen_at: null, submitted_at: null, posted_at: null, closed_at: null, cancelled_at: null, deadline: null, note: "", state_axes: axes(), close_control: { latest_reconciliation: null, close_completion: null }, scopes: [{ scope_id: SCOPE, scope_no: 1, scope_mode: "location_all", owner_org_id: REGION, location_id: LOCATION, custodian_person_id_snapshot: PERSON, material_id: null, condition_code: null, availability_bucket: null, assigned_to_me: true, freeze: null, snapshot_visibility: "not_started", snapshot_accounts: [], allowed_actions: [] }], rounds: [], allowed_actions: ["start"] } as any; }
function page() { return { schema_version: "1.0", items: [{ task_id: TASK, task_no: "ST-SELF-001", task_type: "personal", region_org_id: REGION, status: "draft", version: 0, blind_count: true, current_round_no: 0, current_round_status: null, cutoff_ledger_cursor: null, cutoff_at: null, visible_scope_count: 1, current_round_visible_completed_scope_count: 0, freeze_status: "not_started", state_axes: axes(), deadline: null, allowed_actions: ["start"] }], next_after_id: null } as any; }

function adapter(canCount: boolean): FormalStocktakeAdapter {
  const access = { schema_version: "1.0" as const, person_id: PERSON, authorization_version: 7, can_read: true, can_count: canCount, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: false, can_close: false };
  const detailRead = vi.fn(async () => detail());
  const client: FormalStocktakeAdapter = {
    loadAccess: vi.fn(async () => access),
    loadAccessNoReplay: vi.fn(async () => access),
    loadIdentityNoReplay: vi.fn(async () => ({ person_id: PERSON, name: "工程师", employee_no: "E001", organization_code: "ORG", organization_name: "区域", account_status: "active", employment_status: "active", access_mode: "active", authorization_version: 7, role_codes: ["technician"] })),
    list: vi.fn(async () => page()),
    detail: detailRead,
    detailNoReplay: vi.fn(async () => detailRead()),
    countCommandStatus: vi.fn(async (_taskId, roundId, scopeId, operation, actorPersonId, actorAuthorizationVersion, traceRequestId) => ({
      schema_version: "1.0", task_id: TASK, round_id: roundId, scope_id: scopeId,
      actor_person_id: actorPersonId, actor_authorization_version: actorAuthorizationVersion,
      trace_request_id: traceRequestId, operation, lookup_status: "not_observed", command: null,
    })),
    listRegions: vi.fn(async () => ({ items: [], next_after_id: null })),
    listLocations: vi.fn(async () => ({ items: [], next_after_id: null })),
    listAssignees: vi.fn(async () => ({ items: [], next_after_person_id: null })),
    execute: vi.fn(),
  };
  return client;
}

function postingAdapter(canPost: boolean, allowed: boolean): FormalStocktakeAdapter {
  const approved = { ...detail(), status: "approved", version: 5, current_round_no: 1, allowed_actions: allowed ? ["post"] : [] } as any;
  const posted = { ...approved, status: "posted", version: 6, posted_at: "2026-09-01T10:30:00+08:00", state_axes: { ...axes(), posting_status: "recorded" }, allowed_actions: [] } as any;
  return {
    loadAccess: vi.fn(async () => ({ schema_version: "1.0" as const, person_id: PERSON, authorization_version: 7, can_read: true, can_count: false, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: canPost, can_reconcile: false, can_close: false })),
    list: vi.fn(async () => page()),
    detail: vi.fn(async () => approved),
    listRegions: vi.fn(async () => ({ items: [], next_after_id: null })),
    listLocations: vi.fn(async () => ({ items: [], next_after_id: null })),
    listAssignees: vi.fn(async () => ({ items: [], next_after_person_id: null })),
    execute: vi.fn(async () => ({ result: {}, detail: posted })),
  };
}

function countDetail(scopeCount = 1) {
  const first = { ...detail().scopes[0], snapshot_visibility: "hidden", allowed_actions: ["submit_initial_count"] };
  const scopes = scopeCount === 1 ? [first] : [first, { ...first, scope_id: SCOPE_2, scope_no: 2, location_id: LOCATION_2 }];
  return { ...detail(), status: "counting", version: 1, current_round_no: 1, cutoff_ledger_cursor: 10, cutoff_at: "2026-09-01T08:00:00+08:00", issued_at: "2026-09-01T07:00:00+08:00", frozen_at: "2026-09-01T08:00:00+08:00", state_axes: { ...axes(), count_status: "counting" }, scopes, rounds: [{ round_id: ROUND, round_no: 1, round_type: "initial", status: "counting", started_at: "2026-09-01T08:00:00+08:00", submitted_at: null, submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: false, difference_completion: null, visible_differences: [], region_review: null, headquarters_review: null, recount_cause: null, posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: [] }], allowed_actions: [] } as any;
}

function uploadClient(fileIds = [EVIDENCE_1]): FormalFileUploadClient {
  let index = 0;
  return {
    prepare: vi.fn(async (file: FormalUploadFile, purpose: FormalFilePurpose) => {
      expect(purpose).toBe("stocktake_evidence");
      return Object.freeze({ file, purpose, original_filename: file.name, size_bytes: file.size, mime_type: file.type, sha256: FILE_SHA, intent_headers: { "Idempotency-Key": `file-intent-${"a".repeat(36)}`, "X-Request-ID": `web-${"b".repeat(36)}` }, complete_headers: { "X-Request-ID": `web-${"c".repeat(36)}` } });
    }),
    execute: vi.fn(async (prepared) => ({ file_id: fileIds[Math.min(index++, fileIds.length - 1)], purpose: prepared.purpose, status: "available" as const, verified_at: "2026-09-01T08:01:00Z", sha256: prepared.sha256, size_bytes: prepared.size_bytes, mime_type: prepared.mime_type })),
  };
}

function evidenceFile(): File {
  return new File([new Uint8Array([4, 5, 6])], "盘点照片.jpg", { type: "image/jpeg" });
}

afterEach(() => {
  cleanup();
  // Durable count/review sentinels are intentionally sticky in production;
  // each component test gets a fresh browser-storage boundary.
  try { localStorage.clear(); } catch { /* jsdom storage may be unavailable */ }
});

beforeEach(() => {
  // Production requires Web Locks for cross-tab single-writer recovery.  The
  // jsdom fixture provides the same exclusive-lock seam explicitly.
  Object.defineProperty(navigator, "locks", { configurable: true, value: postLocks });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function numberedPage(first: number, count: number, next: number | null) {
  const id = (index: number) => `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
  return { ...page(), items: Array.from({ length: count }, (_, offset) => ({ ...page().items[0], task_id: id(first + offset), task_no: `ST-${String(first + offset).padStart(3, "0")}` })), next_after_id: next === null ? null : id(next) };
}

describe("daily stocktake task discovery and input provenance", () => {
  it("loads and opens task 51, filters only loaded tasks and keeps a selected later-page task on refresh", async () => {
    const client = adapter(true);
    const second = numberedPage(51, 1, null);
    vi.mocked(client.list).mockImplementation(async (cursor) => cursor ? second : numberedPage(1, 50, 51));
    vi.mocked(client.detail).mockResolvedValue({ ...detail(), task_id: second.items[0].task_id, task_no: "ST-051" });
    render(<FormalStocktakesPage adapter={client} />);
    expect(await screen.findByText(/已加载 50 项/)).toBeTruthy();
    const more = screen.getByRole("button", { name: "加载更多任务" });
    fireEvent.click(more); fireEvent.click(more);
    expect(await screen.findByText(/已加载 51 项/)).toBeTruthy();
    expect(client.list).toHaveBeenCalledTimes(2);
    expect(client.list).toHaveBeenLastCalledWith(second.items[0].task_id);
    fireEvent.change(screen.getByLabelText("筛选已加载任务号"), { target: { value: "st-051" } });
    expect(screen.getAllByRole("button", { name: "查看" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    expect(await within(await screen.findByLabelText("日常盘点详情")).findByRole("heading", { name: "ST-051" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText(/已加载 50 项/)).toBeTruthy();
    expect(within(screen.getByLabelText("日常盘点详情")).getByRole("heading", { name: "ST-051" })).toBeTruthy();
  });

  it.each(["overlap", "backwards", "empty_cursor", "unauthorized"])("fails closed on %s pagination without retaining stale tasks", async (kind) => {
    const client = adapter(true);
    vi.mocked(client.list).mockResolvedValueOnce(numberedPage(1, 1, 2));
    if (kind === "unauthorized") vi.mocked(client.list).mockRejectedValueOnce(new Error("权限已撤销"));
    else vi.mocked(client.list).mockResolvedValueOnce(kind === "overlap" ? numberedPage(1, 1, null) : kind === "backwards" ? numberedPage(2, 1, 2) : numberedPage(2, 0, 3));
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多任务" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("ST-001")).toBeNull();
    expect(screen.queryByRole("button", { name: "创建个人自盘草稿" })).toBeNull();
    expect(screen.queryByRole("button", { name: "加载更多任务" })).toBeNull();
  });

  it.each(["before", "after"])("rechecks identity %s receiving the next page", async (timing) => {
    const client = adapter(true);
    const original = await client.loadAccess();
    vi.mocked(client.loadAccess).mockClear();
    vi.mocked(client.list).mockResolvedValue(numberedPage(1, 1, 2));
    render(<FormalStocktakesPage adapter={client} />);
    const more = await screen.findByRole("button", { name: "加载更多任务" });
    if (timing === "after") {
      vi.mocked(client.loadAccess).mockResolvedValueOnce(original);
      vi.mocked(client.list).mockResolvedValueOnce(numberedPage(2, 1, null));
    }
    vi.mocked(client.loadAccess).mockResolvedValue({ ...original, authorization_version: 8 });
    fireEvent.click(more);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("ST-001")).toBeNull();
    expect(screen.queryByText("ST-002")).toBeNull();
    expect(client.list).toHaveBeenCalledTimes(timing === "before" ? 1 : 2);
  });

  it("ignores a late next page after refresh replaces its generation", async () => {
    const client = adapter(true);
    const late = deferred<any>();
    vi.mocked(client.list).mockResolvedValueOnce(numberedPage(1, 1, 2)).mockImplementationOnce(() => late.promise).mockResolvedValueOnce(numberedPage(7, 1, null));
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多任务" }));
    await vi.waitFor(() => expect(client.list).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("ST-007")).toBeTruthy();
    await act(async () => late.resolve(numberedPage(2, 1, null)));
    expect(screen.queryByText("ST-002")).toBeNull();
    expect(screen.getByText("ST-007")).toBeTruthy();
  });

  it.each(["list", "detail", "write"])("does not publish late %s results into another adapter or leave it busy", async (operation) => {
    const old = adapter(true);
    const next = adapter(true);
    const late = deferred<any>();
    vi.mocked(next.list).mockResolvedValue(numberedPage(9, 1, null));
    if (operation === "list") vi.mocked(old.list).mockImplementation(() => late.promise);
    if (operation === "detail") vi.mocked(old.detail).mockImplementation(() => late.promise);
    if (operation === "write") vi.mocked(old.execute).mockImplementation(() => late.promise);
    const rendered = render(<FormalStocktakesPage adapter={old} />);
    if (operation === "detail") fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    if (operation === "write") fireEvent.click(await screen.findByRole("button", { name: "创建个人自盘草稿" }));
    await vi.waitFor(() => expect(operation === "list" ? old.list : operation === "detail" ? old.detail : old.execute).toHaveBeenCalled());
    rendered.rerender(<FormalStocktakesPage adapter={next} />);
    expect(await screen.findByText("ST-009")).toBeTruthy();
    await act(async () => late.resolve(operation === "list" ? page() : operation === "detail" ? detail() : { result: {}, detail: detail() }));
    expect(screen.queryByLabelText("日常盘点详情")).toBeNull();
    expect(screen.queryByText("ST-SELF-001")).toBeNull();
    expect((screen.getByRole("button", { name: "查看" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "刷新" }) as HTMLButtonElement).disabled).toBe(false);
    expect(next.list).toHaveBeenCalledTimes(1);
  });

  it("does not report a committed write as uncertain when its list refresh fails", async () => {
    const client = adapter(true);
    vi.mocked(client.execute).mockResolvedValue({ result: {}, detail: detail() });
    vi.mocked(client.list).mockResolvedValueOnce(page()).mockRejectedValueOnce(new Error("列表暂不可用"));
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "创建个人自盘草稿" }));
    expect(await screen.findByText("列表暂不可用")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "按原坐标重试" })).toBeNull();
    expect(screen.queryByText(/上一笔写请求结果不确定/)).toBeNull();
    expect(client.execute).toHaveBeenCalledTimes(1);
  });

  it("retains the unresolved write barrier when the old adapter later reports uncertainty", async () => {
    const old = adapter(true);
    const next = adapter(true);
    const late = deferred<any>();
    vi.mocked(old.execute).mockImplementation(() => late.promise);
    const rendered = render(<FormalStocktakesPage adapter={old} />);
    fireEvent.click(await screen.findByRole("button", { name: "创建个人自盘草稿" }));
    await vi.waitFor(() => expect(old.execute).toHaveBeenCalledTimes(1));
    rendered.rerender(<FormalStocktakesPage adapter={next} />);
    const create = await screen.findByRole("button", { name: "创建个人自盘草稿" });
    await act(async () => late.reject(Object.assign(new Error("原请求结果未知"), { write_result_uncertain: true, stocktake_retry_state: "unknown" })));
    expect((create as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(create);
    expect(next.execute).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("日常盘点详情")).toBeNull();
  });

  it("does not start a list refresh after an unmounted write completes", async () => {
    const client = adapter(true);
    const late = deferred<any>();
    vi.mocked(client.execute).mockImplementation(() => late.promise);
    const rendered = render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "创建个人自盘草稿" }));
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(1));
    rendered.unmount();
    await act(async () => late.resolve({ result: {}, detail: detail() }));
    expect(client.list).toHaveBeenCalledTimes(1);
  });

  it.each([
    { method: "manual", materialType: "sku_code", serial: "", serialType: "serial_no", expectedSerialType: null },
    { method: "manual", materialType: "qr_code", serial: "", serialType: "serial_no", expectedSerialType: null },
    { method: "manual", materialType: "sku_code", serial: "SN-001", serialType: "serial_no", expectedSerialType: "serial_no" },
    { method: "manual", materialType: "qr_code", serial: "QR-SERIAL-001", serialType: "qr_code", expectedSerialType: "qr_code" },
    { method: "scan", materialType: "unknown", serial: "QR-SERIAL-001", serialType: "unknown", expectedSerialType: "unknown" },
  ])("preserves $method/$materialType/$serialType without resolving client-side IDs", async (example) => {
    const client = adapter(true);
    const counting = countDetail();
    vi.mocked(client.detail).mockResolvedValue(counting);
    vi.mocked(client.execute).mockResolvedValue({ result: {}, detail: counting });
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    fireEvent.click(await screen.findByRole("button", { name: "录入初盘" }));
    const form = within(screen.getByLabelText("盘点计数表单"));
    fireEvent.change(form.getByLabelText("标识录入方式"), { target: { value: example.method } });
    fireEvent.change(form.getByLabelText("物料标识类型"), { target: { value: example.materialType } });
    fireEvent.change(form.getByLabelText("SN 标识类型"), { target: { value: example.serialType } });
    fireEvent.change(form.getByLabelText("现场物料标识"), { target: { value: "QR-MATERIAL-NOT-SKU" } });
    fireEvent.change(form.getByLabelText("现场 SN 标识"), { target: { value: example.serial } });
    fireEvent.change(form.getByLabelText("现场实盘数量"), { target: { value: "1" } });
    fireEvent.click(form.getByRole("button", { name: "加入观察行" }));
    fireEvent.click(form.getByRole("button", { name: "提交并封存范围" }));
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(1));
    const observations = vi.mocked(client.execute).mock.calls[0][0].body.physical_observations;
    expect(observations).toEqual([expect.objectContaining({ material_id: null, material_identifier_raw: "QR-MATERIAL-NOT-SKU", material_identifier_type: example.materialType, serial_id: null, serial_no_raw: example.serial || null, serial_identifier_type: example.expectedSerialType, count_method: example.method, counted_qty: "1.000" })]);
  });

  it("rejects a multi-unit SN before adding an observation", async () => {
    const client = adapter(true);
    vi.mocked(client.detail).mockResolvedValue(countDetail());
    const alert = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    fireEvent.click(await screen.findByRole("button", { name: "录入初盘" }));
    fireEvent.change(screen.getByLabelText("现场物料标识"), { target: { value: "SKU-001" } });
    fireEvent.change(screen.getByLabelText("现场 SN 标识"), { target: { value: "SN-001" } });
    fireEvent.change(screen.getByLabelText("现场实盘数量"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "加入观察行" }));
    expect(alert).toHaveBeenCalledWith("SN 必须逐件盘点，每行数量为 1");
    expect((screen.getByRole("button", { name: "提交并封存范围" }) as HTMLButtonElement).disabled).toBe(true);
    expect(client.execute).not.toHaveBeenCalled();
    alert.mockRestore();
  });
});

describe("formal non-opening stocktake PC page", () => {
  it("mounts the separate daily-stocktake entry and renders eight independent axes", async () => {
    const client = adapter(true);
    render(<FormalStocktakesPage adapter={client} />);
    expect(await screen.findByRole("heading", { name: "日常盘点" })).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");
    const axesPanel = within(panel).getByLabelText("盘点独立状态轴");
    for (const label of ["计数", "差异", "区域复核", "总部复核", "复盘", "过账", "内部对账", "关闭"]) {
      expect(within(axesPanel).getByText(label)).toBeTruthy();
    }
    expect(within(panel).getByText(/closed.*posted/)).toBeTruthy();
    expect(within(panel).getByRole("button", { name: "启动个人自盘" })).toBeTruthy();
  });

  it("does not render a task action when access permission is absent despite allowed_actions", async () => {
    render(<FormalStocktakesPage adapter={adapter(false)} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");
    expect(within(panel).queryByRole("button", { name: "启动个人自盘" })).toBeNull();
  });

  it("submits selected recount scopes only with the controlled option user id", async () => {
    const nextDetail = { ...detail(), status: "recount_required", version: 5, current_round_no: 1, state_axes: { ...axes(), recount_status: "required" }, allowed_actions: [], rounds: [{ round_id: ROUND, round_no: 1, round_type: "initial", status: "submitted", started_at: "2026-09-01T08:00:00+08:00", submitted_at: "2026-09-01T09:00:00+08:00", submission: null, visible_scope_completions: [], visible_count_lines: [], visible_observations: [], differences_visible: true, difference_completion: null, visible_differences: [], region_review: null, headquarters_review: null, recount_cause: null, posting: { status: "not_posted", posting_ids: [], posting_fact_count: 0, visible_total_quantity: "0.000", covers_all_task_scopes: false, inventory_transaction_count: 0, first_posted_at: null, last_posted_at: null }, allowed_actions: ["open_recount"] }] } as any;
    const client: FormalStocktakeAdapter = {
      loadAccess: vi.fn(async () => ({ schema_version: "1.0" as const, person_id: PERSON, authorization_version: 7, can_read: true, can_count: true, can_manage: true, can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: false, can_close: false })),
      list: vi.fn(async () => page()), detail: vi.fn(async () => nextDetail), listRegions: vi.fn(async () => ({ items: [], next_after_id: null })), listLocations: vi.fn(async () => ({ items: [], next_after_id: null })),
      listAssignees: vi.fn(async () => ({ items: [{ assignee_user_id: ASSIGNEE_USER, person_id: PERSON, name: "工程师", employee_no: "E001", role_codes: ["technician"] as const }], next_after_person_id: null })),
      execute: vi.fn(async () => ({ result: {}, detail: nextDetail })),
    };
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");
    fireEvent.click(within(panel).getByRole("checkbox"));
    expect(await within(panel).findByLabelText("范围 1 复盘人员")).toBeTruthy();
    fireEvent.change(within(panel).getByLabelText("复盘原因"), { target: { value: "区域复核要求复盘" } });
    fireEvent.click(within(panel).getByRole("button", { name: "按所选范围开复盘" }));
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalled());
    const intent = vi.mocked(client.execute).mock.calls[0][0];
    expect(intent.body.assignments).toEqual([{ scope_id: SCOPE, assignee_user_id: ASSIGNEE_USER }]);
    expect(JSON.stringify(intent.body)).not.toContain("person_id");
  });

  it("binds only an available stocktake_evidence upload to the scope count intent", async () => {
    const counting = countDetail();
    const uploads = uploadClient();
    const client: FormalStocktakeAdapter = {
      ...adapter(true),
      detail: vi.fn(async () => counting),
      execute: vi.fn(async () => ({ result: {}, detail: counting })),
    };
    render(<FormalStocktakesPage adapter={client} fileUploadClient={uploads} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");
    fireEvent.click(within(panel).getByRole("button", { name: "录入初盘" }));
    const count = within(panel).getByLabelText("盘点计数表单");
    expect(within(count).queryByText(/已有正式 file_id/)).toBeNull();
    fireEvent.change(within(count).getByLabelText("选择并上传盘点证据"), { target: { files: [evidenceFile()] } });
    expect(await within(count).findByText("状态：available（已完成严格确认）")).toBeTruthy();
    expect(within(count).getByText("盘点照片.jpg")).toBeTruthy();
    expect(within(count).getByText(/3 B · SHA-256/).textContent).toContain(FILE_SHA);
    const zeroButton = within(count).getByRole("button", { name: "确认本范围零库存" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(zeroButton.disabled).toBe(false));
    fireEvent.click(zeroButton);
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(1));
    const intent = vi.mocked(client.execute).mock.calls[0][0];
    expect(intent).toMatchObject({ action: "submit_initial_count", scopeId: SCOPE, body: { evidence_file_ids: [EVIDENCE_1], zero_confirmed: true } });
    expect(uploads.prepare).toHaveBeenCalledTimes(1);
    expect(uploads.execute).toHaveBeenCalledTimes(1);
  });

  it("prevents one file hash from being claimed by a second scope count intent", async () => {
    const counting = countDetail(2);
    const uploads = uploadClient([EVIDENCE_1, EVIDENCE_2]);
    const execute = vi.fn(async () => { throw new Error("明确拒绝测试"); });
    const client: FormalStocktakeAdapter = { ...adapter(true), detail: vi.fn(async () => counting), execute };
    const alert = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    render(<FormalStocktakesPage adapter={client} fileUploadClient={uploads} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");

    fireEvent.click(within(panel).getAllByRole("button", { name: "录入初盘" })[0]);
    let count = within(panel).getByLabelText("盘点计数表单");
    fireEvent.change(within(count).getByLabelText("选择并上传盘点证据"), { target: { files: [evidenceFile()] } });
    expect(await within(count).findByText("状态：available（已完成严格确认）")).toBeTruthy();
    const firstZeroButton = within(count).getByRole("button", { name: "确认本范围零库存" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(firstZeroButton.disabled).toBe(false));
    fireEvent.click(firstZeroButton);
    await vi.waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
    fireEvent.click(within(count).getByRole("button", { name: "取消" }));
    await vi.waitFor(() => expect((within(panel).getAllByRole("button", { name: "录入初盘" })[0] as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(within(panel).getAllByRole("button", { name: "录入初盘" })[1]);
    count = within(panel).getByLabelText("盘点计数表单");
    fireEvent.change(within(count).getByLabelText("选择并上传盘点证据"), { target: { files: [evidenceFile()] } });
    expect(await within(count).findByText("状态：available（已完成严格确认）")).toBeTruthy();
    const secondZeroButton = within(count).getByRole("button", { name: "确认本范围零库存" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(secondZeroButton.disabled).toBe(false));
    fireEvent.click(secondZeroButton);
    expect(alert).toHaveBeenCalledWith("同一盘点证据只能用于一个范围计数写意图");
    expect(execute).toHaveBeenCalledTimes(1);
  });

  it("requires both HQ gates and explicit confirmation before the independent posting command", async () => {
    const client = postingAdapter(true, true);
    const postAccess = await client.loadAccess();
    client.loadIdentityNoReplay = vi.fn(async () => ({ person_id: PERSON, name: "总部管理员", employee_no: "HQ001", organization_code: "HQ", organization_name: "蔚来总部", account_status: "active", employment_status: "active", access_mode: "active", authorization_version: 7, role_codes: ["admin"] }));
    client.loadAccessNoReplay = vi.fn(async () => postAccess);
    client.detailNoReplay = vi.fn(async () => { throw new Error("not_observed must not read detail"); });
    client.postingCommandStatus = vi.fn(async (_taskId, actorPersonId, actorAuthorizationVersion, traceRequestId) => ({ schema_version: "1.0", task_id: TASK, actor_person_id: actorPersonId, actor_authorization_version: actorAuthorizationVersion, trace_request_id: traceRequestId, operation: "post_differences", lookup_status: "not_observed", command: null }));
    const execute = vi.fn(async (intent: any, options?: any) => {
      await options?.beforeWrite?.({ intent, access: postAccess, before: null });
      return { result: {}, detail: { ...detail(), status: "posted", version: 6 } };
    });
    client.execute = execute;
    const postRecoveryStore = createFormalStocktakePostRecoveryStore({ storage: new PostMemoryStorage(), locks: postLocks });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<FormalStocktakesPage adapter={client} postRecoveryStore={postRecoveryStore} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    const panel = await screen.findByLabelText("日常盘点详情");
    const button = within(panel).getByRole("button", { name: "确认差异过账" });
    fireEvent.click(button);
    expect(client.execute).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/不可变过账完成事实.*不会关闭任务.*不会发送通知.*同步 OAM/));

    confirm.mockReturnValue(true);
    fireEvent.click(button);
    await vi.waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
    const intent = execute.mock.calls[0][0];
    expect(intent).toMatchObject({ action: "post", path: `/v1/stocktakes/${TASK}/post-differences`, taskId: TASK, expectedTaskVersion: 5, body: { expected_task_version: 5 } });
    expect(intent.path).not.toContain("opening");
    expect(intent.path).not.toContain("close");
    expect(await screen.findByText(/所有新写已停止/)).toBeTruthy();
    expect(client.postingCommandStatus).toHaveBeenCalledTimes(1);
    confirm.mockRestore();
  });

  it("hides posting when either the HQ permission or server allowed_action is absent", async () => {
    render(<FormalStocktakesPage adapter={postingAdapter(false, true)} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    expect(within(await screen.findByLabelText("日常盘点详情")).queryByRole("button", { name: "确认差异过账" })).toBeNull();
    cleanup();

    render(<FormalStocktakesPage adapter={postingAdapter(true, false)} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    expect(within(await screen.findByLabelText("日常盘点详情")).queryByRole("button", { name: "确认差异过账" })).toBeNull();
  });

  it("keeps internal reconciliation and close as independently confirmed HQ actions", async () => {
    const reconciliationId = "a0000000-0000-4000-8000-000000000001";
    const closeId = "b0000000-0000-4000-8000-000000000001";
    const posted = { ...detail(), status: "posted", version: 6, posted_at: "2026-09-01T10:30:00+08:00", state_axes: { ...axes(), posting_status: "recorded" }, allowed_actions: ["reconcile"] } as any;
    const reconciled = { ...posted, version: 7, state_axes: { ...posted.state_axes, reconciliation_status: "recorded" }, close_control: { latest_reconciliation: { completion_id: reconciliationId, reconciliation_no: 1, reconciliation_ledger_cursor: 20, reconciled_task_version: 7, reconciled_at: "2026-09-01T10:45:00+08:00" }, close_completion: null }, allowed_actions: ["close"] } as any;
    const closed = { ...reconciled, status: "closed", version: 8, closed_at: "2026-09-01T11:00:00+08:00", state_axes: { ...reconciled.state_axes, closure_status: "closed" }, close_control: { ...reconciled.close_control, close_completion: { completion_id: closeId, reconciliation_completion_id: reconciliationId, closed_task_version: 8, closed_at: "2026-09-01T11:00:00+08:00" } }, allowed_actions: [] } as any;
    let stage: "posted" | "reconciled" | "closed" = "posted";
    const client: FormalStocktakeAdapter = {
      ...adapter(false),
      loadAccess: vi.fn(async () => ({ schema_version: "1.0" as const, person_id: PERSON, authorization_version: 7, can_read: true, can_count: false, can_manage: false, can_review_region: false, can_review_headquarters: false, can_post: false, can_reconcile: true, can_close: true })),
      detail: vi.fn(async () => stage === "posted" ? posted : stage === "reconciled" ? reconciled : closed),
      execute: vi.fn(async (intent) => {
        if (intent.action === "reconcile") { stage = "reconciled"; return { result: {}, detail: reconciled }; }
        if (intent.action === "close") { stage = "closed"; return { result: {}, detail: closed }; }
        throw new Error("unexpected action");
      }),
    };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<FormalStocktakesPage adapter={client} />);
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    let panel = await screen.findByLabelText("日常盘点详情");
    const reconcileButton = within(panel).getByRole("button", { name: "执行独立内部对账" });
    fireEvent.click(reconcileButton);
    expect(client.execute).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/保持已过账.*不会关闭任务.*发送通知.*同步 OAM/));
    confirm.mockReturnValue(true);
    fireEvent.click(reconcileButton);
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(1));
    expect(vi.mocked(client.execute).mock.calls[0][0]).toMatchObject({ action: "reconcile", path: `/v1/stocktakes/${TASK}/reconcile`, expectedTaskVersion: 6, body: { expected_task_version: 6 } });

    panel = await screen.findByLabelText("日常盘点详情");
    const closeButton = within(panel).getByRole("button", { name: "依据当前对账证明关闭" });
    confirm.mockReturnValue(false);
    fireEvent.click(closeButton);
    expect(client.execute).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenLastCalledWith(expect.stringMatching(/关闭是独立动作.*重新对账.*不会发送通知.*同步 OAM/));
    confirm.mockReturnValue(true);
    fireEvent.click(closeButton);
    await vi.waitFor(() => expect(client.execute).toHaveBeenCalledTimes(2));
    expect(vi.mocked(client.execute).mock.calls[1][0]).toMatchObject({ action: "close", path: `/v1/stocktakes/${TASK}/close`, expectedTaskVersion: 7, body: { expected_task_version: 7 } });
    confirm.mockRestore();
  });
});
