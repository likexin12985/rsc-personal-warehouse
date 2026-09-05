// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  loadFormalOpeningStocktakeDetail,
  loadFormalOpeningStocktakes,
  openOpeningRecount,
  closeOpeningStocktake,
  postOpeningStocktake,
  recordOpeningObservationDisposition,
  submitOpeningRegionReview,
  submitOpeningHeadquartersReview,
} from "../formalOpeningStocktake";
import { loadOpeningRecountAssignees } from "../formalOpeningRecountAssignees";
import type {
  OpeningRecountActor,
  OpeningRecountAssigneeContext,
} from "../formalOpeningRecountAssignees";
import FormalOpeningStocktakesPage from "./FormalOpeningStocktakes";
import { submitDurableOpeningScopeCount } from "../openingCountSubmission";
import { getOpeningCountRecoveryStore } from "../openingCountRecoveryStore";

vi.mock("../openingCountSubmission", () => ({ submitDurableOpeningScopeCount: vi.fn() }));

vi.mock("../formalOpeningStocktake", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../formalOpeningStocktake")>();
  return {
    ...original,
    loadFormalOpeningStocktakes: vi.fn(),
    loadFormalOpeningStocktakeDetail: vi.fn(),
    submitOpeningScopeCount: vi.fn(),
    submitOpeningRegionReview: vi.fn(),
    submitOpeningHeadquartersReview: vi.fn(),
    openOpeningRecount: vi.fn(),
    recordOpeningObservationDisposition: vi.fn(),
    postOpeningStocktake: vi.fn(),
    closeOpeningStocktake: vi.fn(),
  };
});

vi.mock("../formalOpeningRecountAssignees", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../formalOpeningRecountAssignees")>();
  return { ...original, loadOpeningRecountAssignees: vi.fn() };
});

const TASK_ID = "10000000-0000-4000-8000-000000000001";
const REGION_ID = "20000000-0000-4000-8000-000000000002";
const ROUND_ID = "30000000-0000-4000-8000-000000000003";
const SCOPE_ID = "40000000-0000-4000-8000-000000000004";
const LOCATION_ID = "50000000-0000-4000-8000-000000000005";
const OWNER_ID = "60000000-0000-4000-8000-000000000006";
const DIFFERENCE_ID = "70000000-0000-4000-8000-000000000007";
const OBSERVATION_ID = "80000000-0000-4000-8000-000000000008";
const DISPOSITION_ID = "90000000-0000-4000-8000-000000000009";
const MATERIAL_ID = "a0000000-0000-4000-8000-00000000000a";
const ACTOR_PERSON_ID = "b0000000-0000-4000-8000-00000000000b";
const ASSIGNEE_PERSON_ID = "c0000000-0000-4000-8000-00000000000c";
const ASSIGNEE_USER_ID = "formal-user-01";
const ACTOR: OpeningRecountActor = {
  person_id: ACTOR_PERSON_ID,
  authorization_version: 7,
};

function summary(allowedActions: string[] = ["count"]) {
  return {
    task_id: TASK_ID,
    task_no: "OPENING-JS-2026",
    region_org_id: REGION_ID,
    status: "counting",
    blind_count: true,
    current_round_no: 1,
    current_round_status: "counting",
    visible_scope_count: 1,
    completed_scope_count: 0,
    evidence_status: "counting_hidden",
    difference_count: null,
    task_version: 3,
    deadline: null,
    allowed_actions: allowedActions,
  } as any;
}

function hiddenDetail(allowedActions: string[] = ["count"]) {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    task_no: "OPENING-JS-2026",
    region_org_id: REGION_ID,
    status: "counting",
    blind_count: true,
    task_version: 3,
    deadline: null,
    cutoff_at: "2026-08-31T08:00:00Z",
    current_round: {
      round_id: ROUND_ID,
      round_no: 1,
      round_type: "initial",
      status: "counting",
      started_at: "2026-08-31T08:00:00Z",
      submitted_at: null,
    },
    evidence_status: "counting_hidden",
    scopes: [{
      scope_id: SCOPE_ID,
      scope_no: 1,
      location_id: LOCATION_ID,
      owner_org_id: OWNER_ID,
      assigned_to_me: true,
      completion_status: "pending",
      zero_confirmed: null,
      count_line_count: null,
      observation_line_count: null,
      serial_count: null,
      total_counted_qty: null,
      completed_at: null,
    }],
    observations: [],
    differences: [],
    reviews: [],
    allowed_actions: allowedActions,
  } as any;
}

function sealedDetail(allowedActions: string[] = ["review_region"]) {
  return {
    ...hiddenDetail(allowedActions),
    status: "region_review",
    task_version: 4,
    current_round: {
      ...hiddenDetail().current_round,
      status: "submitted",
      submitted_at: "2026-08-31T09:00:00Z",
    },
    evidence_status: "sealed",
    scopes: [{
      ...hiddenDetail().scopes[0],
      completion_status: "completed",
      zero_confirmed: false,
      count_line_count: 1,
      observation_line_count: 1,
      serial_count: 0,
      total_counted_qty: "2.000",
      completed_at: "2026-08-31T09:00:00Z",
    }],
    differences: [{
      difference_id: DIFFERENCE_ID,
      difference_no: 1,
      scope_id: SCOPE_ID,
      difference_type: "excess",
      material_id: null,
      book_qty: "0.000",
      counted_qty: "2.000",
      difference_qty: "2.000",
      affected_qty: "2.000",
      reason_code: null,
      evidence_required: false,
    }],
  } as any;
}

function observationDetail(allowedDispositions: string[] = [
  "resolved_existing_master",
  "pending_verification",
  "requires_recount",
]) {
  const source = sealedDetail([]);
  source.status = "submitted";
  source.differences[0] = {
    ...source.differences[0],
    reason_code: "opening_pending_verification",
    evidence_required: true,
  };
  source.observations = [{
    observation_id: OBSERVATION_ID,
    difference_id: DIFFERENCE_ID,
    observation_no: 1,
    scope_id: SCOPE_ID,
    material_identifier_type: "unknown",
    material_identifier_raw: "现场未知物料-A",
    condition_code: "new",
    availability_bucket: "available",
    counted_qty: "2.000",
    verification_status: "pending_verification",
    material_id: null,
    lot_id: null,
    lot_no_raw: null,
    serial_id: null,
    serial_no_raw: null,
    serial_identifier_type: null,
    disposition: null,
    allowed_dispositions: allowedDispositions,
  }];
  return source;
}

function recountDetail(taskVersion = 5) {
  return {
    ...sealedDetail(["open_recount"]),
    status: "recount_required",
    task_version: taskVersion,
  };
}

function recountContext(detail = recountDetail()): OpeningRecountAssigneeContext {
  return {
    task_id: TASK_ID,
    source_round_id: ROUND_ID,
    scope_id: SCOPE_ID,
    location_id: LOCATION_ID,
    region_org_id: REGION_ID,
    task_version: detail.task_version,
    actor_person_id: ACTOR.person_id,
    actor_authorization_version: ACTOR.authorization_version,
  };
}

function recountAssigneePage(detail = recountDetail()) {
  return {
    ...recountContext(detail),
    schema_version: "1.0" as const,
    items: [{
      user_id: ASSIGNEE_USER_ID,
      person_id: ASSIGNEE_PERSON_ID,
      display_name: "测试工程师",
      employee_no: "TEST-001",
      role_code: "technician" as const,
    }],
    next_after_person_id: null,
  };
}

function page() {
  return {
    schema_version: "1.0" as const,
    items: [summary()],
    next_after_id: null,
  };
}

async function renderAndOpen(
  detail: any,
  actor?: OpeningRecountActor,
): Promise<void> {
  vi.mocked(loadFormalOpeningStocktakes).mockResolvedValue(page());
  vi.mocked(loadFormalOpeningStocktakeDetail).mockResolvedValue(detail);
  render(<FormalOpeningStocktakesPage actor={actor} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看" }));
  expect(await screen.findByLabelText("盘点详情")).toBeTruthy();
  await waitFor(() => expect(screen.queryByLabelText("盘点计数恢复")).toBeNull());
  await waitFor(() => {
    const buttons = within(screen.getByLabelText("盘点详情")).queryAllByRole("button");
    for (const button of buttons) {
      if (button.textContent === "提交计数" && !actor) continue;
      expect((button as HTMLButtonElement).disabled).toBe(false);
    }
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formal opening stocktake PC page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    const held = new Set<string>();
    Object.defineProperty(navigator, "locks", { configurable: true, value: {
      async request(name: string, _options: unknown, work: (lock: object | null) => Promise<unknown>) {
        if (held.has(name)) return work(null);
        held.add(name);
        try { return await work({ name }); } finally { held.delete(name); }
      },
    } });
  });

  async function seedPending(): Promise<void> {
    await getOpeningCountRecoveryStore().withTaskLease(TASK_ID, async (lease) => { lease.persist({
      v: 1, kind: "opening_scope_count", task_id: TASK_ID, round_id: ROUND_ID, round_no: 1,
      scope_id: SCOPE_ID, actor_person_id: ACTOR.person_id, actor_authorization_version: ACTOR.authorization_version,
      trace_request_id: "page-pending-count-0001",
    }); });
  }

  it("keeps preparation hidden by default and requires an explicit manage capability and valid actor", async () => {
    vi.mocked(loadFormalOpeningStocktakes).mockResolvedValue({ items: [], next_after_id: null } as any);
    const page = render(<FormalOpeningStocktakesPage actor={ACTOR} />);
    await screen.findByText("暂无可见正式盘点任务");
    expect(screen.queryByLabelText("期初盘点准备（只读）")).toBeNull();
    page.rerender(<FormalOpeningStocktakesPage canPrepare />);
    expect(screen.queryByLabelText("期初盘点准备（只读）")).toBeNull();
    page.rerender(<FormalOpeningStocktakesPage canPrepare actor={{ ...ACTOR, person_id: "invalid" }} />);
    expect(screen.queryByLabelText("期初盘点准备（只读）")).toBeNull();
    page.rerender(<FormalOpeningStocktakesPage canPrepare actor={ACTOR} />);
    expect(screen.getByLabelText("期初盘点准备（只读）")).toBeTruthy();
    expect(screen.getByRole("button", { name: "展开准备目录" }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: /启动|创建期初盘点/ })).toBeNull();
    page.rerender(<FormalOpeningStocktakesPage canPrepare={false} actor={ACTOR} />);
    expect(screen.queryByLabelText("期初盘点准备（只读）")).toBeNull();
  });

  it.each(["post", "close"] as const)("blocks %s at execution even before a cross-tab storage event arrives", async (action) => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const source = sealedDetail([action]);
    source.status = action === "post" ? "approved" : "posted";
    await renderAndOpen(source, ACTOR);
    await seedPending();
    fireEvent.click(screen.getByRole("button", { name: action === "post" ? "独立过账" : "独立关闭" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("禁止其他写入"));
    expect(postOpeningStocktake).not.toHaveBeenCalled();
    expect(closeOpeningStocktake).not.toHaveBeenCalled();
    expect(getOpeningCountRecoveryStore().read(TASK_ID).kind).toBe("valid");
  });

  it("blocks a review opened before a pending marker appears", async () => {
    const source = sealedDetail(["review_region"]);
    source.differences = [];
    await renderAndOpen(source, ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "区域复核" }));
    fireEvent.change(screen.getByLabelText("本级决定"), { target: { value: "approve" } });
    await seedPending();
    fireEvent.click(screen.getByRole("button", { name: "提交本级复核" }));
    await screen.findByRole("alert");
    expect(submitOpeningRegionReview).not.toHaveBeenCalled();
  });

  it("blocks headquarters review when a pending count appears after its dialog opens", async () => {
    const source = sealedDetail(["review_headquarters"]);
    source.status = "hq_review";
    source.differences = [];
    source.reviews = [{ stage: "region", decision: "approve", reviewed_at: "2026-08-31T09:10:00Z" }];
    await renderAndOpen(source, ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "总部复核" }));
    fireEvent.change(screen.getByLabelText("本级决定"), { target: { value: "approve" } });
    await seedPending();
    fireEvent.click(screen.getByRole("button", { name: "提交本级复核" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("禁止其他写入"));
    expect(submitOpeningHeadquartersReview).not.toHaveBeenCalled();
    expect(getOpeningCountRecoveryStore().read(TASK_ID).kind).toBe("valid");
  });

  it("blocks recount creation when a pending count appears after selecting an authorized assignee", async () => {
    const source = recountDetail();
    vi.mocked(loadOpeningRecountAssignees).mockResolvedValue(recountAssigneePage(source));
    await renderAndOpen(source, ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));
    const assignee = await screen.findByRole("combobox", { name: "范围 1 复盘人员" }) as HTMLSelectElement;
    await waitFor(() => expect(assignee.disabled).toBe(false));
    fireEvent.change(assignee, { target: { value: ASSIGNEE_USER_ID } });
    fireEvent.change(screen.getByLabelText("复盘原因"), { target: { value: "正式授权人员重新实盘" } });
    await seedPending();
    fireEvent.click(screen.getByRole("button", { name: "创建复盘轮次" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("禁止其他写入"));
    expect(openOpeningRecount).not.toHaveBeenCalled();
    expect(getOpeningCountRecoveryStore().read(TASK_ID).kind).toBe("valid");
  });

  it("blocks observation disposition when a pending count appears after its dialog opens", async () => {
    await renderAndOpen(observationDetail(), ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "绑定正式主数据" }));
    fireEvent.change(screen.getByLabelText("正式物料 UUID"), { target: { value: MATERIAL_ID } });
    fireEvent.change(screen.getByLabelText("原因代码"), { target: { value: "MASTER_CONFIRMED" } });
    fireEvent.change(screen.getByLabelText("处置说明"), { target: { value: "人工核验主数据" } });
    await seedPending();
    fireEvent.click(screen.getByRole("button", { name: "确认处置" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("禁止其他写入"));
    expect(recordOpeningObservationDisposition).not.toHaveBeenCalled();
    expect(getOpeningCountRecoveryStore().read(TASK_ID).kind).toBe("valid");
  });

  it("keeps an unknown count draft and does not invoke the coordinator again", async () => {
    vi.mocked(submitDurableOpeningScopeCount).mockImplementation(async () => {
      await seedPending();
      throw new Error("原盘点仍待核验");
    });
    await renderAndOpen(hiddenDetail(), ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "提交计数" }));
    fireEvent.change(screen.getByLabelText("物料标识"), { target: { value: "FIELD-SKU" } });
    fireEvent.change(screen.getByLabelText("实盘数量"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "确认提交计数" }));
    await screen.findByRole("alert");
    expect((screen.getByLabelText("物料标识") as HTMLInputElement).value).toBe("FIELD-SKU");
    expect((screen.getByLabelText("实盘数量") as HTMLInputElement).value).toBe("2");
    fireEvent.click(screen.getByRole("button", { name: "确认提交计数" }));
    expect(submitDurableOpeningScopeCount).toHaveBeenCalledTimes(1);
    expect(getOpeningCountRecoveryStore().read(TASK_ID).kind).toBe("valid");
  });

  it("invalidates a count coordinator's commit guard when another detail view opens", async () => {
    let captured!: () => boolean;
    let finish!: (value: any) => void;
    vi.mocked(submitDurableOpeningScopeCount).mockImplementation((options) => {
      captured = options.canCommit!;
      return new Promise((resolve) => { finish = resolve; });
    });
    await renderAndOpen(hiddenDetail(), ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "提交计数" }));
    fireEvent.click(screen.getByText("确认该范围现场为零库存"));
    fireEvent.click(screen.getByRole("button", { name: "确认提交计数" }));
    await waitFor(() => expect(captured()).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    await screen.findByLabelText("盘点详情");
    expect(captured()).toBe(false);
    finish({ recovered: true, command: {}, detail: sealedDetail([]) });
    await waitFor(() => expect(screen.queryByText(/历史提交已核验/)).toBeNull());
  });

  it("does not carry an unsent count draft into a new round of the same scope", async () => {
    await renderAndOpen(hiddenDetail(), ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "提交计数" }));
    fireEvent.change(screen.getByLabelText("物料标识"), { target: { value: "OLD-ROUND-SKU" } });
    fireEvent.change(screen.getByLabelText("实盘数量"), { target: { value: "9" } });
    const next = hiddenDetail();
    next.current_round = { ...next.current_round, round_id: DIFFERENCE_ID, round_no: 2, round_type: "recount" };
    vi.mocked(loadFormalOpeningStocktakeDetail).mockResolvedValue(next);
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    await screen.findByText("第 2 轮 · 计数中");
    await waitFor(() => expect((screen.getByRole("button", { name: "提交计数" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "提交计数" }));
    expect((screen.getByLabelText("物料标识") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("实盘数量") as HTMLInputElement).value).toBe("");
    expect(submitDurableOpeningScopeCount).not.toHaveBeenCalled();
  });

  it("keeps a blind round quantity-blind and renders only the server-allowed count", async () => {
    await renderAndOpen(hiddenDetail(["count"]));

    const detailSection = screen.getByLabelText("盘点详情");
    expect(within(detailSection).getAllByText("封存前隐藏").length).toBeGreaterThan(0);
    expect(detailSection.textContent).not.toContain("2.000");
    expect(within(detailSection).getByRole("button", { name: "提交计数" })).toBeTruthy();
    expect(within(detailSection).queryByRole("button", { name: "区域复核" })).toBeNull();
    expect(within(detailSection).queryByRole("button", { name: "总部复核" })).toBeNull();
    expect(within(detailSection).queryByRole("button", { name: "独立过账" })).toBeNull();
  });

  it("never infers a start control for an existing task", async () => {
    await renderAndOpen(hiddenDetail([]));

    const detailSection = screen.getByLabelText("盘点详情");
    expect(within(detailSection).queryByRole("button", { name: "启动" })).toBeNull();
    expect(within(detailSection).getByText("当前没有服务端授权操作")).toBeTruthy();
  });

  it("submits a zero count through the exact selected scope and uses the reread detail", async () => {
    const before = hiddenDetail(["count"]);
    const after = sealedDetail([]);
    vi.mocked(submitDurableOpeningScopeCount).mockResolvedValue({
      recovered: false,
      command: {} as any,
      detail: after,
    });
    await renderAndOpen(before, ACTOR);

    fireEvent.click(screen.getByRole("button", { name: "提交计数" }));
    fireEvent.click(screen.getByText("确认该范围现场为零库存"));
    fireEvent.click(screen.getByRole("button", { name: "确认提交计数" }));

    await waitFor(() => expect(submitDurableOpeningScopeCount).toHaveBeenCalledWith({
      taskId: TASK_ID, scopeId: SCOPE_ID, expectedIdentity: ACTOR, canCommit: expect.any(Function),
      input: { physical_observations: [], zero_confirmed: true },
    }));
    expect(await screen.findByText(/历史计数与当前任务详情均已确认/)).toBeTruthy();
  });

  it("shows sealed observations but never invents a disposition button", async () => {
    const detail = observationDetail([]);
    detail.observations[0].disposition = {
      disposition_id: DISPOSITION_ID,
      disposition: "pending_verification",
      resolved_material_id: null,
      resolved_lot_id: null,
      resolved_serial_id: null,
      reason_code: "NEEDS_CONFIRMATION",
      decided_at: "2026-08-31T09:05:00Z",
    };
    await renderAndOpen(detail);

    const detailSection = screen.getByLabelText("盘点详情");
    expect(within(detailSection).getByText("现场未知物料-A")).toBeTruthy();
    expect(within(detailSection).getByText("保留待核实")).toBeTruthy();
    expect(within(detailSection).queryByRole("button", { name: "绑定正式主数据" })).toBeNull();
    expect(within(detailSection).queryByRole("button", { name: "要求复盘" })).toBeNull();
  });

  it("submits only an explicitly allowed disposition without guessing master data", async () => {
    const before = observationDetail();
    const after = observationDetail([]);
    after.observations[0].disposition = {
      disposition_id: DISPOSITION_ID,
      disposition: "resolved_existing_master",
      resolved_material_id: MATERIAL_ID,
      resolved_lot_id: null,
      resolved_serial_id: null,
      reason_code: "MASTER_CONFIRMED",
      decided_at: "2026-08-31T09:05:00Z",
    };
    vi.mocked(recordOpeningObservationDisposition).mockResolvedValue({
      before,
      result: {} as any,
      detail: after,
    });
    await renderAndOpen(before);

    fireEvent.click(screen.getByRole("button", { name: "绑定正式主数据" }));
    expect(screen.getByText(/不会搜索、猜测或自动选择/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("正式物料 UUID"), {
      target: { value: MATERIAL_ID },
    });
    fireEvent.change(screen.getByLabelText("原因代码"), {
      target: { value: "MASTER_CONFIRMED" },
    });
    fireEvent.change(screen.getByLabelText("处置说明"), {
      target: { value: "人工核对正式物料目录" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认处置" }));

    await waitFor(() => expect(recordOpeningObservationDisposition).toHaveBeenCalledWith(
      TASK_ID,
      OBSERVATION_ID,
      {
        disposition: "resolved_existing_master",
        reason_code: "MASTER_CONFIRMED",
        comment: "人工核对正式物料目录",
        resolved_material_id: MATERIAL_ID,
        resolved_lot_id: null,
        resolved_serial_id: null,
      },
    ));
    expect(await screen.findByText(/详情已强制重读确认/)).toBeTruthy();
  });

  it("shows and submits only the region review returned by allowed_actions", async () => {
    const before = sealedDetail(["review_region"]);
    const after = { ...sealedDetail([]), status: "hq_review" };
    vi.mocked(submitOpeningRegionReview).mockResolvedValue({
      before,
      result: {} as any,
      detail: after,
    });
    await renderAndOpen(before);

    const detailSection = screen.getByLabelText("盘点详情");
    expect(within(detailSection).getByRole("button", { name: "区域复核" })).toBeTruthy();
    expect(within(detailSection).queryByRole("button", { name: "总部复核" })).toBeNull();
    fireEvent.click(within(detailSection).getByRole("button", { name: "区域复核" }));
    fireEvent.change(screen.getByLabelText("本级决定"), { target: { value: "approve" } });
    expect(screen.getByLabelText("差异 1 决定").textContent).toBe("接受过账");
    fireEvent.click(screen.getByRole("button", { name: "提交本级复核" }));

    await waitFor(() => expect(submitOpeningRegionReview).toHaveBeenCalledWith(
      TASK_ID,
      {
        decision: "approve",
        items: [{
          difference_id: DIFFERENCE_ID,
          decision: "accept_for_posting",
          comment: "",
        }],
        comment: "",
      },
    ));
    expect(await screen.findByText(/总部复核仍为独立状态/)).toBeTruthy();
  });

  it("restricts a first-round verified observation review to an explicit recount", async () => {
    const before = observationDetail([]);
    before.status = "region_review";
    before.allowed_actions = ["review_region"];
    before.observations[0] = {
      ...before.observations[0],
      verification_status: "verified",
      material_id: MATERIAL_ID,
    };
    await renderAndOpen(before);

    fireEvent.click(screen.getByRole("button", { name: "区域复核" }));
    const select = screen.getByLabelText("本级决定");
    expect(within(select).queryByRole("option", { name: "通过" })).toBeNull();
    expect(within(select).getByRole("option", { name: "要求复盘" })).toBeTruthy();
    fireEvent.change(select, { target: { value: "recount" } });
    expect(screen.getByLabelText("差异 1 决定").textContent).toBe("要求复盘");
  });

  it("keeps posting and closing as independent states after a server-approved post", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const before = {
      ...sealedDetail(["post"]),
      status: "approved",
      task_version: 8,
      reviews: [
        { stage: "region", decision: "approve", reviewed_at: "2026-08-31T09:10:00Z" },
        { stage: "headquarters", decision: "approve", reviewed_at: "2026-08-31T09:20:00Z" },
      ],
    };
    const after = { ...before, status: "posted", task_version: 9, allowed_actions: ["close"] };
    vi.mocked(postOpeningStocktake).mockResolvedValue({
      before,
      result: {} as any,
      detail: after,
    });
    await renderAndOpen(before);

    fireEvent.click(screen.getByRole("button", { name: "独立过账" }));

    await waitFor(() => expect(postOpeningStocktake).toHaveBeenCalledWith(TASK_ID));
    const lifecycle = screen.getByLabelText("盘点独立状态");
    expect(within(lifecycle).getByText("已过账")).toBeTruthy();
    expect(within(lifecycle).getByText("未关闭")).toBeTruthy();
    expect(await screen.findByText(/关闭状态仍需独立执行/)).toBeTruthy();
  });

  it("fails closed instead of silently filtering a duplicate next page", async () => {
    vi.mocked(loadFormalOpeningStocktakes)
      .mockResolvedValueOnce({ ...page(), next_after_id: TASK_ID })
      .mockResolvedValueOnce(page());
    render(<FormalOpeningStocktakesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "加载下一页" }));

    expect((await screen.findByRole("alert")).textContent).toContain("跨页返回重复任务");
    expect(screen.getAllByRole("button", { name: "查看" })).toHaveLength(1);
  });

  it("fixes control differences to pending verification and requires a comment", async () => {
    const before = sealedDetail(["review_region"]);
    before.differences[0].difference_type = "control_unassigned";
    const after = { ...sealedDetail([]), status: "hq_review" };
    vi.mocked(submitOpeningRegionReview).mockResolvedValue({
      before,
      result: {} as any,
      detail: after,
    });
    await renderAndOpen(before);

    fireEvent.click(screen.getByRole("button", { name: "区域复核" }));
    fireEvent.change(screen.getByLabelText("本级决定"), { target: { value: "approve" } });
    expect(screen.getByLabelText("差异 1 决定").textContent).toBe("待控制账核验");
    fireEvent.click(screen.getByRole("button", { name: "提交本级复核" }));
    expect(submitOpeningRegionReview).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("差异 1 说明"), {
      target: { value: "等待 OAM 控制账映射核实" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交本级复核" }));

    await waitFor(() => expect(submitOpeningRegionReview).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        decision: "approve",
        items: [{
          difference_id: DIFFERENCE_ID,
          decision: "pending_verification",
          comment: "等待 OAM 控制账映射核实",
        }],
      }),
    ));
  });

  it("opens recount with an explicitly selected user id and fresh task/round anchors", async () => {
    const before = recountDetail();
    vi.mocked(loadOpeningRecountAssignees).mockResolvedValue(
      recountAssigneePage(before),
    );
    vi.mocked(openOpeningRecount).mockResolvedValue({
      before,
      result: {} as any,
      detail: hiddenDetail([]),
    });
    await renderAndOpen(before, ACTOR);

    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));
    const assignee = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(assignee.disabled).toBe(false));
    expect(assignee.value).toBe("");
    fireEvent.change(assignee, { target: { value: ASSIGNEE_USER_ID } });
    fireEvent.change(screen.getByLabelText("复盘原因"), {
      target: { value: "区域复核确认需要重新实盘" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建复盘轮次" }));

    await waitFor(() => expect(openOpeningRecount).toHaveBeenCalledWith(
      TASK_ID,
      {
        assignments: [{
          scope_id: SCOPE_ID,
          assignee_user_id: ASSIGNEE_USER_ID,
        }],
        reason: "区域复核确认需要重新实盘",
      },
      { task_version: before.task_version, source_round_id: ROUND_ID },
    ));
    const body = vi.mocked(openOpeningRecount).mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("task_version");
    expect(body).not.toHaveProperty("source_round_id");
    expect(ASSIGNEE_USER_ID).not.toBe(ASSIGNEE_PERSON_ID);
  });

  it("does not submit with no selected scope, no directory option, or an unchecked scope", async () => {
    const before = recountDetail();
    vi.mocked(loadOpeningRecountAssignees).mockResolvedValue({
      ...recountAssigneePage(before),
      items: [],
    });
    await renderAndOpen(before, ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    fireEvent.change(screen.getByLabelText("复盘原因"), {
      target: { value: "必须显式选择范围和人员" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建复盘轮次" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "请选择至少一个复盘范围",
    );
    expect(openOpeningRecount).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));
    expect(await screen.findByText("本页没有合格人员，请管理员核对正式授权。")).toBeTruthy();
    fireEvent.submit(screen.getByRole("dialog", {
      name: "发起独立复盘轮次",
    }).querySelector("form")!);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(
      "复盘人员选择已失效",
    ));
    expect(openOpeningRecount).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));
    fireEvent.click(screen.getByRole("button", { name: "创建复盘轮次" }));
    expect(openOpeningRecount).not.toHaveBeenCalled();
  });

  it("keeps the recount POST disabled without a current formal actor", async () => {
    const before = recountDetail();
    await renderAndOpen(before);

    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));

    expect(screen.getByRole("alert").textContent).toContain("当前身份、任务或轮次尚未核验");
    expect((screen.getByRole("button", {
      name: "创建复盘轮次",
    }) as HTMLButtonElement).disabled).toBe(true);
    expect(loadOpeningRecountAssignees).not.toHaveBeenCalled();
    expect(openOpeningRecount).not.toHaveBeenCalled();
  });

  it("invalidates a selected assignee when the task version changes before submit", async () => {
    const before = recountDetail(5);
    const changed = recountDetail(6);
    vi.mocked(loadOpeningRecountAssignees)
      .mockResolvedValueOnce(recountAssigneePage(before))
      .mockResolvedValueOnce(recountAssigneePage(changed));
    await renderAndOpen(before, ACTOR);
    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "范围 1" }));
    const select = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(select.disabled).toBe(false));
    fireEvent.change(select, { target: { value: ASSIGNEE_USER_ID } });
    fireEvent.change(screen.getByLabelText("复盘原因"), {
      target: { value: "旧版本不得提交" },
    });

    vi.mocked(loadFormalOpeningStocktakeDetail).mockResolvedValueOnce(changed);
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "发起复盘" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "发起复盘" }));
    await waitFor(() => expect(loadOpeningRecountAssignees).toHaveBeenCalledTimes(2));
    const refreshedSelect = await screen.findByRole("combobox", {
      name: "范围 1 复盘人员",
    }) as HTMLSelectElement;
    await waitFor(() => expect(refreshedSelect.disabled).toBe(false));
    expect(refreshedSelect.value).toBe("");
    fireEvent.submit(screen.getByRole("dialog", {
      name: "发起独立复盘轮次",
    }).querySelector("form")!);

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain(
      "复盘人员选择已失效",
    ));
    expect(openOpeningRecount).not.toHaveBeenCalled();
  });
});
