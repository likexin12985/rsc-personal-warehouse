import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, mutationHeaders } from "./api";
import {
  __openingReconciliationIntentTestOnly,
  approveOpeningReconciliation,
  explainOpeningReconciliation,
  loadOpeningReconciliationDetail,
  loadOpeningReconciliations,
  OpeningReconciliationHandoffError,
  startOpeningReconciliation,
  validateOpeningReconciliationDetail,
  validateOpeningReconciliationPage,
} from "./formalOpeningReconciliation";
import { loadFormalOpeningStocktakeDetail } from "./formalOpeningStocktake";


vi.mock("./api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./api")>();
  return {
    ...original,
    api: vi.fn(),
    mutationHeaders: vi.fn(() => ({
      headers: {
        "Idempotency-Key": "opening-reconciliation-test-safe-key",
        "X-Request-ID": "web-opening-reconciliation-test",
      },
    })),
  };
});

vi.mock("./formalOpeningStocktake", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./formalOpeningStocktake")>();
  return {
    ...original,
    loadFormalOpeningStocktakeDetail: vi.fn(),
  };
});

const RUN_ID = "10000000-0000-4000-8000-000000000001";
const TASK_ID = "20000000-0000-4000-8000-000000000002";
const REGION_ID = "30000000-0000-4000-8000-000000000003";
const SOURCE_ID = "40000000-0000-4000-8000-000000000004";
const ROUND_ID = "50000000-0000-4000-8000-000000000005";
const POSTING_ID = "60000000-0000-4000-8000-000000000006";
const ITEM_ID = "70000000-0000-4000-8000-000000000007";
const DIFFERENCE_ID = "80000000-0000-4000-8000-000000000008";
const CONTROL_LINE_ID = "90000000-0000-4000-8000-000000000009";
const MATERIAL_ID = "a0000000-0000-4000-8000-00000000000a";
const FILE_ID = "b0000000-0000-4000-8000-00000000000b";

function differenceItem() {
  return {
    reconciliation_item_id: ITEM_ID,
    stocktake_difference_id: DIFFERENCE_ID,
    control_snapshot_line_id: CONTROL_LINE_ID,
    business_key: "OAM-JS-SKU-001",
    material_id: MATERIAL_ID,
    external_qty: "5.000",
    local_qty: "3.000",
    difference: "2.000",
    status: "difference",
    version: 0,
    explanation: "",
    evidence_reference: "",
    evidence_file_id: null,
    explained_at: null,
  };
}

function baseSummary() {
  return {
    reconciliation_run_id: RUN_ID,
    task_id: TASK_ID,
    task_no: "OPENING-JS-2026",
    region_org_id: REGION_ID,
    status: "differences",
    version: 0,
    item_count: 1,
    explained_item_count: 0,
    resolved_item_count: 0,
    external_snapshot_at: "2026-08-31T08:00:00Z",
    local_ledger_cursor: "23",
    created_at: "2026-08-31T09:00:00Z",
    approved_at: null,
    allowed_actions: ["explain"],
  };
}

function differenceDetail() {
  return {
    ...baseSummary(),
    schema_version: "1.0",
    source_system_id: SOURCE_ID,
    round_id: ROUND_ID,
    posting_id: POSTING_ID,
    difference_manifest_sha256: "a".repeat(64),
    approval_comment: "",
    items: [differenceItem()],
  };
}

function explainedDetail() {
  return {
    ...differenceDetail(),
    version: 1,
    explained_item_count: 1,
    allowed_actions: ["approve"],
    items: [{
      ...differenceItem(),
      status: "explained",
      version: 1,
      explanation: "省仓交接单数量尚未在控制账完成归属",
      evidence_reference: "EVIDENCE-JS-2026-001",
      evidence_file_id: FILE_ID,
      explained_at: "2026-08-31T09:30:00Z",
    }],
  };
}

function approvedDetail() {
  return {
    ...explainedDetail(),
    status: "approved",
    version: 2,
    explained_item_count: 0,
    resolved_item_count: 1,
    approved_at: "2026-08-31T10:00:00Z",
    allowed_actions: [],
    approval_comment: "总部复核证据完整，同意解决控制账差异",
    items: [{
      ...explainedDetail().items[0],
      status: "resolved",
      version: 2,
    }],
  };
}

function page() {
  return {
    schema_version: "1.0",
    items: [baseSummary()],
    next_after_id: RUN_ID,
  };
}

function explanationInput() {
  return [{
    reconciliation_item_id: ITEM_ID,
    explanation: "省仓交接单数量尚未在控制账完成归属",
    evidence_reference: "EVIDENCE-JS-2026-001",
    evidence_file_id: FILE_ID,
  }];
}

function resetTestState(): void {
  if (!__openingReconciliationIntentTestOnly) {
    throw new Error("formal opening reconciliation test control is unavailable");
  }
  __openingReconciliationIntentTestOnly.reset();
  vi.mocked(api).mockReset();
  vi.mocked(mutationHeaders).mockClear();
  vi.mocked(loadFormalOpeningStocktakeDetail).mockReset();
}

beforeEach(resetTestState);
afterEach(resetTestState);

describe("formal opening reconciliation contract", () => {
  it("accepts a coherent page and an approved detail", () => {
    expect(validateOpeningReconciliationPage(page()).items[0].task_id).toBe(TASK_ID);
    expect(validateOpeningReconciliationDetail(approvedDetail()).status).toBe("approved");
  });

  it("accepts re-explanation only when the server returns the explain action", () => {
    expect(validateOpeningReconciliationDetail({
      ...explainedDetail(),
      allowed_actions: ["explain", "approve"],
    }).allowed_actions).toEqual(["explain", "approve"]);
  });

  it.each([
    ["duplicate action", { allowed_actions: ["explain", "explain"] }, /重复允许操作/],
    ["premature approval", { allowed_actions: ["approve"] }, /批准操作与汇总状态不一致/],
    ["partial explanation", { explained_item_count: 1, item_count: 2, allowed_actions: [] }, /汇总数量关系无效/],
    ["approval evidence", { status: "approved", resolved_item_count: 1, explained_item_count: 0, approved_at: null, allowed_actions: [] }, /批准状态证据不完整/],
  ])("rejects an incoherent summary: %s", (_name, patch, message) => {
    expect(() => validateOpeningReconciliationPage({
      ...page(),
      items: [{ ...baseSummary(), ...patch }],
    })).toThrow(message as RegExp);
  });

  it("rejects arithmetic drift and evidence attached to an unexplained item", () => {
    expect(() => validateOpeningReconciliationDetail({
      ...differenceDetail(),
      items: [{ ...differenceItem(), difference: "1.000" }],
    })).toThrow(/差异计算不一致/);
    expect(() => validateOpeningReconciliationDetail({
      ...differenceDetail(),
      items: [{ ...differenceItem(), explanation: "不应存在的原因" }],
    })).toThrow(/夹带解释证据/);
  });

  it("rejects duplicate task/run pages and cursor drift", () => {
    const secondRun = "11000000-0000-4000-8000-000000000001";
    expect(() => validateOpeningReconciliationPage({
      schema_version: "1.0",
      items: [baseSummary(), { ...baseSummary(), reconciliation_run_id: secondRun }],
      next_after_id: secondRun,
    })).toThrow(/重复任务或对账批次/);
    expect(() => validateOpeningReconciliationPage({
      ...page(),
      next_after_id: "12000000-0000-4000-8000-000000000001",
    })).toThrow(/游标与本页末项不一致/);
  });

  it("loads only the formal list and exact detail endpoints", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page())
      .mockResolvedValueOnce(differenceDetail());

    await loadOpeningReconciliations();
    await loadOpeningReconciliationDetail(RUN_ID);

    expect(api).toHaveBeenNthCalledWith(1, "/v1/reconciliations/opening?limit=20");
    expect(api).toHaveBeenNthCalledWith(2, `/v1/reconciliations/opening/${RUN_ID}`);
    expect(vi.mocked(api).mock.calls.some(([path]) => String(path).includes("/integrations/oam"))).toBe(false);
  });
});

describe("formal opening reconciliation mutations", () => {
  it("fresh-reads the task version, creates once and proves the exact run", async () => {
    vi.mocked(loadFormalOpeningStocktakeDetail).mockResolvedValue({
      task_id: TASK_ID,
      task_version: 9,
      status: "posted",
    } as never);
    vi.mocked(api)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        reconciliation_run_id: RUN_ID,
        task_id: TASK_ID,
        status: "differences",
        version: 0,
        item_count: 1,
        created_at: "2026-08-31T09:00:00Z",
        replayed: false,
      })
      .mockResolvedValueOnce(differenceDetail());

    const result = await startOpeningReconciliation(TASK_ID);

    expect(loadFormalOpeningStocktakeDetail).toHaveBeenCalledWith(TASK_ID);
    expect(api).toHaveBeenNthCalledWith(
      1,
      `/v1/reconciliations/opening/tasks/${TASK_ID}`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_task_version: 9 }),
      }),
    );
    expect(api).toHaveBeenNthCalledWith(2, `/v1/reconciliations/opening/${RUN_ID}`);
    expect(result.detail.reconciliation_run_id).toBe(RUN_ID);
    expect(mutationHeaders).toHaveBeenCalledWith("opening-reconciliation-start");
  });

  it("explains every item with exact run/item versions and proves submitted evidence", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(differenceDetail())
      .mockResolvedValueOnce({
        schema_version: "1.0",
        reconciliation_run_id: RUN_ID,
        task_id: TASK_ID,
        status: "differences",
        version: 1,
        explained_item_count: 1,
        explained_at: "2026-08-31T09:30:00Z",
        replayed: false,
      })
      .mockResolvedValueOnce(explainedDetail());

    await explainOpeningReconciliation(RUN_ID, explanationInput());

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/reconciliations/opening/${RUN_ID}/explanations`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_version: 0,
          items: [{
            reconciliation_item_id: ITEM_ID,
            expected_version: 0,
            explanation: "省仓交接单数量尚未在控制账完成归属",
            evidence_reference: "EVIDENCE-JS-2026-001",
            evidence_file_id: FILE_ID,
          }],
        }),
      }),
    );
    expect(api).toHaveBeenNthCalledWith(3, `/v1/reconciliations/opening/${RUN_ID}`);
  });

  it("approves only a server-authorized fully explained run and never closes the task", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(explainedDetail())
      .mockResolvedValueOnce({
        schema_version: "1.0",
        reconciliation_run_id: RUN_ID,
        task_id: TASK_ID,
        status: "approved",
        version: 2,
        resolved_item_count: 1,
        approved_at: "2026-08-31T10:00:00Z",
        replayed: false,
      })
      .mockResolvedValueOnce(approvedDetail());

    await approveOpeningReconciliation(
      RUN_ID,
      "总部复核证据完整，同意解决控制账差异",
    );

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/reconciliations/opening/${RUN_ID}/approve`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_version: 1,
          comment: "总部复核证据完整，同意解决控制账差异",
        }),
      }),
    );
    expect(vi.mocked(api).mock.calls.some(([path]) => String(path).endsWith("/close"))).toBe(false);
  });

  it("stops before mutation when allowed_actions omits the requested action", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      ...differenceDetail(),
      allowed_actions: [],
    });

    await expect(explainOpeningReconciliation(RUN_ID, explanationInput()))
      .rejects.toThrow(/服务端未授权/);
    expect(api).toHaveBeenCalledTimes(1);
    expect(mutationHeaders).not.toHaveBeenCalled();
  });

  it("reuses exact coordinates after an uncertain failure", async () => {
    const result = {
      schema_version: "1.0",
      reconciliation_run_id: RUN_ID,
      task_id: TASK_ID,
      status: "differences",
      version: 1,
      explained_item_count: 1,
      explained_at: "2026-08-31T09:30:00Z",
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(differenceDetail())
      .mockRejectedValueOnce(new ApiError(503, "outcome unknown", {}))
      .mockResolvedValueOnce(differenceDetail())
      .mockResolvedValueOnce(differenceDetail())
      .mockResolvedValueOnce(result)
      .mockResolvedValueOnce(explainedDetail());

    await expect(explainOpeningReconciliation(RUN_ID, explanationInput()))
      .rejects.toThrow("outcome unknown");
    await explainOpeningReconciliation(RUN_ID, explanationInput());

    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api).mock.calls[1][1]?.headers)
      .toEqual(vi.mocked(api).mock.calls[4][1]?.headers);
  });

  it("recovers an accepted response without a second POST when final proof read fails", async () => {
    const result = {
      schema_version: "1.0",
      reconciliation_run_id: RUN_ID,
      task_id: TASK_ID,
      status: "differences",
      version: 1,
      explained_item_count: 1,
      explained_at: "2026-08-31T09:30:00Z",
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(differenceDetail())
      .mockResolvedValueOnce(result)
      .mockRejectedValueOnce(new TypeError("final proof lost"))
      .mockResolvedValueOnce(explainedDetail());

    await expect(explainOpeningReconciliation(RUN_ID, explanationInput()))
      .rejects.toThrow("final proof lost");
    const recovered = await explainOpeningReconciliation(RUN_ID, explanationInput());

    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST"))
      .toHaveLength(1);
    expect(recovered.result).toEqual(result);
  });

  it("hands off instead of self-confirming when another request advances the run", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(differenceDetail())
      .mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValueOnce(explainedDetail())
      .mockResolvedValueOnce(explainedDetail());

    await expect(explainOpeningReconciliation(RUN_ID, explanationInput()))
      .rejects.toThrow("response lost");
    const error = await explainOpeningReconciliation(RUN_ID, explanationInput())
      .catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(OpeningReconciliationHandoffError);
    expect(error).toMatchObject({
      code: "opening_reconciliation_handoff_required",
      target_id: RUN_ID,
      action: "explain",
      path: `/v1/reconciliations/opening/${RUN_ID}/explanations`,
    });
    const differentActionError = await approveOpeningReconciliation(RUN_ID, "总部准备批准该批次")
      .catch((reason: unknown) => reason);
    expect(differentActionError).toBeInstanceOf(OpeningReconciliationHandoffError);
    const originalHeaders = new Headers(vi.mocked(api).mock.calls[1][1]?.headers);
    const originalKey = originalHeaders.get("Idempotency-Key");
    expect(originalKey).toBeTruthy();
    for (const handoff of [error, differentActionError] as OpeningReconciliationHandoffError[]) {
      expect(handoff.request_id).toBe(originalHeaders.get("X-Request-ID"));
      expect(handoff).not.toHaveProperty("idempotency_key");
      for (const rendered of [String(handoff), handoff.message, handoff.stack, JSON.stringify(handoff)]) {
        expect(rendered).not.toContain(originalKey);
        expect(rendered).not.toContain("idempotency_key");
      }
    }
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });
});
