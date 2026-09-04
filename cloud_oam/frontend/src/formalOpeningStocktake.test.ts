import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, mutationHeaders } from "./api";
import {
  __openingMutationIntentTestOnly,
  closeOpeningStocktake,
  executeVersionedOpeningTerminalAction,
  formalOpeningTaskPath,
  loadFormalOpeningStocktakeDetail,
  loadFormalOpeningStocktakes,
  openOpeningRecount,
  OpeningMutationHandoffError,
  openingReviewItemPolicy,
  postOpeningStocktake,
  recordOpeningObservationDisposition,
  submitOpeningRegionReview,
  submitOpeningScopeCount,
  validateOpeningStocktakeTaskDetail,
  validateOpeningStocktakeTaskPage,
} from "./formalOpeningStocktake";


vi.mock("./api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./api")>();
  let sequence = 0;
  return {
    ...original,
    api: vi.fn(),
    mutationHeaders: vi.fn(() => {
      sequence += 1;
      return {
        headers: {
          "Idempotency-Key": `opening-post-${sequence.toString().padStart(3, "0")}-safe-key`,
          "X-Request-ID": `web-opening-${sequence.toString().padStart(3, "0")}`,
        },
      };
    }),
  };
});

const TASK_ID = "10000000-0000-4000-8000-000000000001";
const REGION_ID = "20000000-0000-4000-8000-000000000002";
const ROUND_ID = "30000000-0000-4000-8000-000000000003";
const SCOPE_ID = "40000000-0000-4000-8000-000000000004";
const SECOND_SCOPE_ID = "41000000-0000-4000-8000-000000000004";
const LOCATION_ID = "50000000-0000-4000-8000-000000000005";
const OWNER_ID = "60000000-0000-4000-8000-000000000006";
const DIFFERENCE_ID = "70000000-0000-4000-8000-000000000007";
const OBSERVATION_ID = "80000000-0000-4000-8000-000000000008";
const DISPOSITION_ID = "90000000-0000-4000-8000-000000000009";
const MATERIAL_ID = "a0000000-0000-4000-8000-00000000000a";

function resetOpeningMutationTestState(): void {
  if (!__openingMutationIntentTestOnly) {
    throw new Error("formal opening mutation test control is unavailable");
  }
  __openingMutationIntentTestOnly.reset();
  vi.mocked(api).mockReset();
  vi.mocked(mutationHeaders).mockClear();
}

beforeEach(resetOpeningMutationTestState);
afterEach(resetOpeningMutationTestState);

function detail(taskVersion: number) {
  return { task_id: TASK_ID, task_version: taskVersion };
}

function validateDetail(value: unknown) {
  return value as ReturnType<typeof detail>;
}

function hiddenDetail() {
  return {
    schema_version: "1.0",
    task_id: TASK_ID,
    task_no: "OPENING-JS-2026",
    region_org_id: REGION_ID,
    status: "counting",
    blind_count: true,
    task_version: 3,
    deadline: "2026-09-01T08:00:00Z",
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
    allowed_actions: ["count"],
  };
}

function sealedDetail() {
  return {
    ...hiddenDetail(),
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
    allowed_actions: ["review_region"],
  };
}

function pendingObservation(overrides: Record<string, unknown> = {}) {
  return {
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
    allowed_dispositions: [
      "resolved_existing_master",
      "pending_verification",
      "requires_recount",
    ],
    ...overrides,
  };
}

function dispositionDetail() {
  const source: any = {
    ...sealedDetail(),
    status: "submitted",
    allowed_actions: [],
    observations: [pendingObservation()],
  };
  source.differences = [{
    ...source.differences[0],
    reason_code: "opening_pending_verification",
    evidence_required: true,
  }];
  return source;
}

function dispositionSummary(
  disposition: "resolved_existing_master" | "pending_verification" | "requires_recount",
) {
  return {
    disposition_id: DISPOSITION_ID,
    disposition,
    resolved_material_id: disposition === "resolved_existing_master" ? MATERIAL_ID : null,
    resolved_lot_id: null,
    resolved_serial_id: null,
    reason_code: "MASTER_CONFIRMED",
    decided_at: "2026-08-31T09:05:00Z",
  };
}

describe("formal opening stocktake read validation", () => {
  it("uses the backend-bounded page size of twenty", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      schema_version: "1.0",
      items: [],
      next_after_id: null,
    });

    await loadFormalOpeningStocktakes();

    expect(api).toHaveBeenCalledWith("/v1/stocktakes/opening?limit=20");
  });

  it("rejects a detail payload for a different task than the requested path", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      ...hiddenDetail(),
      task_id: "20000000-0000-4000-8000-000000000099",
    });

    await expect(loadFormalOpeningStocktakeDetail(TASK_ID)).rejects.toThrow(
      /目标任务或版本不一致/,
    );
  });

  it("accepts a quantity-blind counting detail without exposing derived evidence", () => {
    const result = validateOpeningStocktakeTaskDetail(hiddenDetail());

    expect(result.evidence_status).toBe("counting_hidden");
    expect(result.scopes[0].total_counted_qty).toBeNull();
    expect(result.differences).toEqual([]);
  });

  it("accepts sealed scope and difference evidence", () => {
    const result = validateOpeningStocktakeTaskDetail(sealedDetail());

    expect(result.scopes[0].total_counted_qty).toBe("2.000");
    expect(result.differences[0].difference_qty).toBe("2.000");
  });

  it("accepts a sealed one-to-one observation and exact role disposition policy", () => {
    const result = validateOpeningStocktakeTaskDetail(dispositionDetail());

    expect(result.observations[0].difference_id).toBe(DIFFERENCE_ID);
    expect(result.observations[0].allowed_dispositions).toEqual([
      "resolved_existing_master",
      "pending_verification",
      "requires_recount",
    ]);
  });

  it("requires the observations field and rejects observations before sealing", () => {
    const missing: any = hiddenDetail();
    delete missing.observations;
    expect(() => validateOpeningStocktakeTaskDetail(missing)).toThrow(
      /缺少字段 observations/,
    );

    const leaking: any = hiddenDetail();
    leaking.observations = [pendingObservation()];
    expect(() => validateOpeningStocktakeTaskDetail(leaking)).toThrow(
      /封存前不得展示现场观察证据/,
    );
  });

  it("rejects duplicate observation identities and duplicate difference bindings", () => {
    const duplicateIdentity: any = dispositionDetail();
    duplicateIdentity.observations.push(pendingObservation());
    expect(() => validateOpeningStocktakeTaskDetail(duplicateIdentity)).toThrow(
      /重复现场观察/,
    );

    const duplicateBinding: any = dispositionDetail();
    duplicateBinding.observations.push(pendingObservation({
      observation_id: "81000000-0000-4000-8000-000000000008",
      observation_no: 2,
      allowed_dispositions: [],
    }));
    expect(() => validateOpeningStocktakeTaskDetail(duplicateBinding)).toThrow(
      /多个现场观察绑定同一差异/,
    );
  });

  it("rejects an observation outside visible scopes or bound to the wrong difference", () => {
    const wrongScope: any = dispositionDetail();
    wrongScope.observations[0].scope_id = "82000000-0000-4000-8000-000000000008";
    expect(() => validateOpeningStocktakeTaskDetail(wrongScope)).toThrow(
      /观察不属于当前可见范围/,
    );

    const wrongDifference: any = dispositionDetail();
    wrongDifference.observations[0].difference_id = "83000000-0000-4000-8000-000000000008";
    expect(() => validateOpeningStocktakeTaskDetail(wrongDifference)).toThrow(
      /未绑定当前可见差异/,
    );

    const crossScope: any = dispositionDetail();
    crossScope.scopes.push({
      ...crossScope.scopes[0],
      scope_id: SECOND_SCOPE_ID,
      scope_no: 2,
    });
    crossScope.observations[0].scope_id = SECOND_SCOPE_ID;
    expect(() => validateOpeningStocktakeTaskDetail(crossScope)).toThrow(
      /观察与差异的盘点范围不一致/,
    );
  });

  it("rejects observation disposition permissions outside their exact task state", () => {
    const wrongState: any = dispositionDetail();
    wrongState.status = "region_review";
    expect(() => validateOpeningStocktakeTaskDetail(wrongState)).toThrow(
      /允许处置与任务、轮次或复核状态不一致/,
    );

    const wrongRoleShape: any = dispositionDetail();
    wrongRoleShape.observations[0].allowed_dispositions = ["resolved_existing_master"];
    expect(() => validateOpeningStocktakeTaskDetail(wrongRoleShape)).toThrow(
      /不符合正式角色策略/,
    );
  });

  it("fails closed when a blind counting response leaks a scope quantity", () => {
    const source: any = hiddenDetail();
    source.scopes[0].total_counted_qty = "2.000";

    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /封存前不得展示或推算实盘数量/,
    );
  });

  it("fails closed when a blind counting response leaks book or difference evidence", () => {
    const source: any = hiddenDetail();
    source.differences = sealedDetail().differences;

    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /封存前不得展示或推算账面数与差异数/,
    );
  });

  it("allows a blind completed scope timestamp while keeping every quantity field hidden", () => {
    const source: any = hiddenDetail();
    source.scopes[0].completion_status = "completed";
    source.scopes[0].completed_at = "2026-08-31T08:30:00Z";

    expect(validateOpeningStocktakeTaskDetail(source).scopes[0].completed_at)
      .toBe("2026-08-31T08:30:00Z");
  });

  it("rejects an allowed action inconsistent with task state", () => {
    const source: any = hiddenDetail();
    source.allowed_actions = ["post"];

    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /允许操作与任务状态不一致/,
    );
  });

  it("rejects start because starting is not an existing-task allowed action", () => {
    const source: any = hiddenDetail();
    source.allowed_actions = ["start"];

    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /未知 allowed_action/,
    );
  });

  it("rejects a difference outside every visible scope", () => {
    const source: any = sealedDetail();
    source.differences[0].scope_id = "80000000-0000-4000-8000-000000000008";

    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /差异不属于当前可见范围/,
    );
  });

  it("validates list evidence visibility and rejects duplicate server actions", () => {
    const summary = {
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
      allowed_actions: ["count"],
    };
    expect(validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [summary],
      next_after_id: null,
    }).items).toHaveLength(1);

    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [{ ...summary, allowed_actions: ["count", "count"] }],
      next_after_id: null,
    })).toThrow(/重复允许操作/);
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [{ ...summary, difference_count: 1 }],
      next_after_id: null,
    })).toThrow(/差异数量与证据封存状态不一致/);
  });

  it("fails closed for oversized, unsorted or cursor-inconsistent pages", () => {
    const rows = Array.from({ length: 21 }, (_, index) => ({
      ...validateOpeningStocktakeTaskPage({
        schema_version: "1.0",
        items: [{
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
          allowed_actions: ["count"],
        }],
        next_after_id: null,
      }).items[0],
      task_id: `10000000-0000-4000-8000-${(index + 1).toString().padStart(12, "0")}`,
    }));
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: rows,
      next_after_id: null,
    })).toThrow(/超过分页上限/);
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [rows[1], rows[0]],
      next_after_id: null,
    })).toThrow(/严格递增/);
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [rows[0]],
      next_after_id: rows[1].task_id,
    })).toThrow(/游标与本页末项不一致/);
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.0",
      items: [],
      next_after_id: rows[0].task_id,
    })).toThrow(/游标与本页末项不一致/);
  });

  it("rejects a next page that does not advance beyond its request cursor", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      schema_version: "1.0",
      items: [{
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
        allowed_actions: ["count"],
      }],
      next_after_id: null,
    });

    await expect(loadFormalOpeningStocktakes(TASK_ID)).rejects.toThrow(
      /未越过请求游标/,
    );
  });

  it("rejects unsupported response versions and inconsistent round timestamps", () => {
    expect(() => validateOpeningStocktakeTaskPage({
      schema_version: "1.1",
      items: [],
      next_after_id: null,
    })).toThrow(/版本不受支持/);

    const source: any = sealedDetail();
    source.current_round.submitted_at = null;
    expect(() => validateOpeningStocktakeTaskDetail(source)).toThrow(
      /提交时间与状态不一致/,
    );
  });
});

describe("formal opening stocktake mutation policy", () => {
  it("rejects a non-UUID task before transport", () => {
    expect(() => formalOpeningTaskPath("../legacy-stocktake")).toThrow(
      /任务标识无效/,
    );
    expect(api).not.toHaveBeenCalled();
  });

  it("rereads the detail, sends its exact version and rereads after success", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(detail(7))
      .mockResolvedValueOnce({
        task_id: TASK_ID,
        round_id: ROUND_ID,
        resulting_task_status: "posted",
        task_version: 8,
      })
      .mockResolvedValueOnce(detail(8));

    const result = await executeVersionedOpeningTerminalAction({
      taskId: TASK_ID,
      action: "post",
      expectedRoundId: () => ROUND_ID,
      validateDetail,
      validateResult: (value) => value as {
        task_id: string;
        round_id: string;
        resulting_task_status: "posted";
        task_version: number;
      },
    });

    expect(api).toHaveBeenNthCalledWith(1, `/v1/stocktakes/opening/${TASK_ID}`);
    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/stocktakes/opening/${TASK_ID}/post`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_version: 7 }),
      }),
    );
    expect(api).toHaveBeenNthCalledWith(3, `/v1/stocktakes/opening/${TASK_ID}`);
    expect(result.before.task_version).toBe(7);
    expect(result.detail.task_version).toBe(8);
  });

  it("creates independent request coordinates for every write attempt", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(detail(8))
      .mockResolvedValueOnce({
        task_id: TASK_ID,
        resulting_task_status: "closed",
        task_version: 9,
      })
      .mockResolvedValueOnce(detail(9))
      .mockResolvedValueOnce(detail(9))
      .mockResolvedValueOnce({
        task_id: TASK_ID,
        resulting_task_status: "closed",
        task_version: 10,
      })
      .mockResolvedValueOnce(detail(10));

    for (let attempt = 0; attempt < 2; attempt += 1) {
      await executeVersionedOpeningTerminalAction({
        taskId: TASK_ID,
        action: "close",
        validateDetail,
        validateResult: (value) => value as {
          task_id: string;
          resulting_task_status: "closed";
          task_version: number;
        },
      });
    }

    expect(mutationHeaders).toHaveBeenCalledTimes(2);
    expect(mutationHeaders).toHaveBeenNthCalledWith(1, "opening-close");
    expect(mutationHeaders).toHaveBeenNthCalledWith(2, "opening-close");
    const firstWrite = vi.mocked(api).mock.calls[1][1];
    const secondWrite = vi.mocked(api).mock.calls[4][1];
    expect(firstWrite?.headers).not.toEqual(secondWrite?.headers);
  });

  it("stops before mutation when a fresh detail targets another task", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      task_id: "20000000-0000-4000-8000-000000000002",
      task_version: 7,
    });

    await expect(executeVersionedOpeningTerminalAction({
      taskId: TASK_ID,
      action: "post",
      expectedRoundId: () => ROUND_ID,
      validateDetail,
      validateResult: (value) => value as {
        task_id: string;
        round_id: string;
        resulting_task_status: "posted";
        task_version: number;
      },
    })).rejects.toThrow(/目标任务或版本不一致/);
    expect(api).toHaveBeenCalledTimes(1);
    expect(mutationHeaders).not.toHaveBeenCalled();
  });

  it.each([
    [
      "task",
      {
        task_id: "20000000-0000-4000-8000-000000000099",
        round_id: ROUND_ID,
        resulting_task_status: "posted",
        task_version: 8,
      },
      /目标任务不一致/,
    ],
    [
      "action",
      {
        task_id: TASK_ID,
        round_id: ROUND_ID,
        resulting_task_status: "closed",
        task_version: 8,
      },
      /请求动作不一致/,
    ],
    [
      "round",
      {
        task_id: TASK_ID,
        round_id: "31000000-0000-4000-8000-000000000003",
        resulting_task_status: "posted",
        task_version: 8,
      },
      /目标轮次不一致/,
    ],
    [
      "version",
      {
        task_id: TASK_ID,
        round_id: ROUND_ID,
        resulting_task_status: "posted",
        task_version: 9,
      },
      /请求版本不一致/,
    ],
  ] as const)("rejects a terminal response with a wrong %s anchor", async (
    _anchor,
    response,
    expected,
  ) => {
    vi.mocked(api)
      .mockResolvedValueOnce(detail(7))
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce(detail(7));

    await expect(executeVersionedOpeningTerminalAction({
      taskId: TASK_ID,
      action: "post",
      expectedRoundId: () => ROUND_ID,
      validateDetail,
      validateResult: (value) => value as {
        task_id: string;
        round_id: string;
        resulting_task_status: "posted" | "closed";
        task_version: number;
      },
    })).rejects.toThrow(expected);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("stores an accepted terminal response as a clone and revalidates it on recovery", async () => {
    const recoveryTaskId = "13000000-0000-4000-8000-000000000001";
    const before = { task_id: recoveryTaskId, task_version: 7 };
    const after = { task_id: recoveryTaskId, task_version: 8 };
    const response = {
      task_id: recoveryTaskId,
      round_id: ROUND_ID,
      resulting_task_status: "posted" as const,
      task_version: 8,
    };
    const validateResult = vi.fn((value: unknown) => value as typeof response);
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(response)
      .mockRejectedValueOnce(new TypeError("terminal final detail lost"))
      .mockResolvedValueOnce(after);

    const execute = () => executeVersionedOpeningTerminalAction({
      taskId: recoveryTaskId,
      action: "post" as const,
      expectedRoundId: () => ROUND_ID,
      validateDetail: (value: unknown) => value as typeof before,
      validateResult,
    });
    await expect(execute()).rejects.toThrow("terminal final detail lost");
    response.task_version = 99;
    response.resulting_task_status = "posted";

    const recovered = await execute();

    expect(recovered.result.task_version).toBe(8);
    expect(validateResult).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST"))
      .toHaveLength(1);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });

  it("binds a count to the freshly read round and assigned scope without a router-only version", async () => {
    const before = hiddenDetail();
    const after = sealedDetail();
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        task_id: TASK_ID,
        round_id: ROUND_ID,
        scope_id: SCOPE_ID,
        task_status: "region_review",
        round_status: "submitted",
        scope_completed: true,
        round_sealed: true,
        has_pending_verification: false,
        replayed: false,
      })
      .mockResolvedValueOnce(after);

    await submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [],
      zero_confirmed: true,
    });

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/scopes/${SCOPE_ID}/count`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ physical_observations: [], zero_confirmed: true }),
      }),
    );
    expect(JSON.parse(String(vi.mocked(api).mock.calls[1][1]?.body))).not.toHaveProperty(
      "expected_version",
    );
    expect(api).toHaveBeenNthCalledWith(3, `/v1/stocktakes/opening/${TASK_ID}`);
  });

  it("uses the freshly read task version only for terminal posting", async () => {
    const before = {
      ...sealedDetail(),
      status: "approved",
      allowed_actions: ["post"],
      task_version: 9,
    };
    const after = {
      ...sealedDetail(),
      status: "posted",
      allowed_actions: ["close"],
      task_version: 10,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        task_id: TASK_ID,
        round_id: ROUND_ID,
        posting_id: "80000000-0000-4000-8000-000000000008",
        inventory_transaction_id: null,
        resulting_task_status: "posted",
        task_version: 10,
        total_quantity: "2.000",
        established_scope_count: 1,
        pending_control_difference_count: 0,
        ledger_cursor: 12,
        replayed: false,
      })
      .mockResolvedValueOnce(after);

    await postOpeningStocktake(TASK_ID);

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/stocktakes/opening/${TASK_ID}/post`,
      expect.objectContaining({ body: JSON.stringify({ expected_version: 9 }) }),
    );
  });

  it("reuses the same terminal coordinates after an uncertain transport failure", async () => {
    const before = {
      ...sealedDetail(),
      status: "approved",
      allowed_actions: ["post"],
      task_version: 11,
    };
    const after = {
      ...sealedDetail(),
      status: "posted",
      allowed_actions: ["close"],
      task_version: 12,
    };
    const postResult = {
      schema_version: "1.0",
      task_id: TASK_ID,
      round_id: ROUND_ID,
      posting_id: "80000000-0000-4000-8000-000000000008",
      inventory_transaction_id: null,
      resulting_task_status: "posted",
      task_version: 12,
      total_quantity: "2.000",
      established_scope_count: 1,
      pending_control_difference_count: 0,
      ledger_cursor: 13,
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockRejectedValueOnce(new TypeError("response lost"))
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(postResult)
      .mockResolvedValueOnce(after);

    await expect(postOpeningStocktake(TASK_ID)).rejects.toThrow("response lost");
    await postOpeningStocktake(TASK_ID);

    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api).mock.calls[1][1]?.headers)
      .toEqual(vi.mocked(api).mock.calls[4][1]?.headers);
    expect(api).toHaveBeenNthCalledWith(3, `/v1/stocktakes/opening/${TASK_ID}`);
  });

  it("retains terminal coordinates when the POST succeeds but final proof reread fails", async () => {
    const before = {
      ...sealedDetail(),
      status: "approved",
      allowed_actions: ["post"],
      task_version: 21,
    };
    const after = {
      ...sealedDetail(),
      status: "posted",
      allowed_actions: ["close"],
      task_version: 22,
    };
    const postResult = {
      schema_version: "1.0",
      task_id: TASK_ID,
      round_id: ROUND_ID,
      posting_id: "80000000-0000-4000-8000-000000000008",
      inventory_transaction_id: null,
      resulting_task_status: "posted",
      task_version: 22,
      total_quantity: "2.000",
      established_scope_count: 1,
      pending_control_difference_count: 0,
      ledger_cursor: 14,
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(postResult)
      .mockRejectedValueOnce(new TypeError("final proof lost"))
      .mockResolvedValueOnce(after);

    await expect(postOpeningStocktake(TASK_ID)).rejects.toThrow("final proof lost");
    const recovered = await postOpeningStocktake(TASK_ID);

    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(4);
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST"))
      .toHaveLength(1);
    expect(recovered.result).toEqual(postResult);
    expect(recovered.detail.task_version).toBe(22);
  });

  it("blocks a terminal replay and every other same-task write after another request advances it", async () => {
    const handoffTaskId = "11000000-0000-4000-8000-000000000001";
    const before = {
      ...sealedDetail(),
      task_id: handoffTaskId,
      status: "approved",
      allowed_actions: ["post"],
      task_version: 31,
    };
    const advanced = {
      ...sealedDetail(),
      task_id: handoffTaskId,
      status: "posted",
      allowed_actions: ["close"],
      task_version: 32,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockRejectedValueOnce(new TypeError("terminal response lost"))
      .mockResolvedValueOnce(advanced)
      .mockResolvedValueOnce(advanced);

    await expect(postOpeningStocktake(handoffTaskId)).rejects.toThrow(
      "terminal response lost",
    );
    const retryError = await postOpeningStocktake(handoffTaskId).catch(
      (error: unknown) => error,
    );
    expect(retryError).toBeInstanceOf(OpeningMutationHandoffError);
    expect(retryError).toMatchObject({
      code: "opening_mutation_handoff_required",
      task_id: handoffTaskId,
      action: "post",
      path: `/v1/stocktakes/opening/${handoffTaskId}/post`,
    });
    expect(String((retryError as Error).message)).toMatch(
      /request_id=web-opening-\d{3} reason=state_advanced/,
    );
    expect(String((retryError as Error).message)).not.toContain("expected_version");

    const differentActionError = await closeOpeningStocktake(handoffTaskId).catch(
      (error: unknown) => error,
    );
    expect(differentActionError).toBeInstanceOf(OpeningMutationHandoffError);
    expect((differentActionError as OpeningMutationHandoffError).request_id)
      .toBe((retryError as OpeningMutationHandoffError).request_id);
    const originalHeaders = new Headers(vi.mocked(api).mock.calls[1][1]?.headers);
    const originalKey = originalHeaders.get("Idempotency-Key");
    expect(originalKey).toMatch(/^opening-post-\d{3}-safe-key$/);
    for (const handoff of [retryError, differentActionError] as OpeningMutationHandoffError[]) {
      expect(handoff.request_id).toBe(originalHeaders.get("X-Request-ID"));
      expect(handoff).not.toHaveProperty("idempotency_key");
      for (const rendered of [String(handoff), handoff.message, handoff.stack, JSON.stringify(handoff)]) {
        expect(rendered).not.toContain(originalKey);
        expect(rendered).not.toContain("idempotency_key");
      }
    }
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(4);
  });

  it("never self-confirms an uncertain count from a scope advanced by another request", async () => {
    const handoffTaskId = "12000000-0000-4000-8000-000000000001";
    const before = { ...hiddenDetail(), task_id: handoffTaskId };
    const advanced = { ...sealedDetail(), task_id: handoffTaskId };
    const input = { physical_observations: [], zero_confirmed: true };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockRejectedValueOnce(new ApiError(503, "count outcome unknown", {}))
      .mockResolvedValueOnce(advanced)
      .mockResolvedValueOnce(advanced);

    await expect(submitOpeningScopeCount(
      handoffTaskId,
      SCOPE_ID,
      input,
    )).rejects.toThrow("count outcome unknown");
    const retryError = await submitOpeningScopeCount(
      handoffTaskId,
      SCOPE_ID,
      input,
    ).catch((error: unknown) => error);

    expect(retryError).toBeInstanceOf(OpeningMutationHandoffError);
    expect(retryError).toMatchObject({
      code: "opening_mutation_handoff_required",
      task_id: handoffTaskId,
      action: "count",
      path: `/v1/stocktakes/opening/${handoffTaskId}/rounds/${ROUND_ID}/scopes/${SCOPE_ID}/count`,
    });
    expect(String((retryError as Error).message)).not.toContain("zero_confirmed");
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(4);
  });

  it("clears coordinates only after a first direct action-specific no-effect rejection", async () => {
    const before = hiddenDetail();
    const after = sealedDetail();
    const countResult = {
      schema_version: "1.0",
      task_id: TASK_ID,
      round_id: ROUND_ID,
      scope_id: SCOPE_ID,
      task_status: "region_review",
      round_status: "submitted",
      scope_completed: true,
      round_sealed: true,
      has_pending_verification: false,
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockRejectedValueOnce(new ApiError(412, "invalid count", {
        category: "precondition_failed", code: "opening_count_state_invalid",
      }))
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(countResult)
      .mockResolvedValueOnce(after);

    await expect(submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [],
      zero_confirmed: true,
    })).rejects.toThrow("invalid count");
    await submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [],
      zero_confirmed: true,
    });

    expect(mutationHeaders).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api).mock.calls[1][1]?.headers)
      .not.toEqual(vi.mocked(api).mock.calls[3][1]?.headers);
    expect(api).toHaveBeenCalledTimes(5);
  });

  it.each([
    new ApiError(401, "retain", {}),
    new ApiError(403, "retain", {}),
    new ApiError(404, "retain", {}),
    new ApiError(409, "retain", { category: "conflict", code: "idempotency_conflict" }),
    new ApiError(422, "retain", {}),
    new ApiError(429, "retain", {}),
    new ApiError(500, "retain", {}),
    new ApiError(412, "retain"),
    new ApiError(412, "retain", { category: "precondition_failed", code: "database_guard_rejected" }),
    new ApiError(412, "retain", { category: "precondition_failed", code: "opening_recount_state_invalid" }),
    new ApiError(400, "retain", { category: "invalid_request", code: "unknown_rejection" }),
  ])("retains coordinates for non-whitelisted count rejection %#", async (error) => {
    vi.mocked(api).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") throw error;
      return hiddenDetail();
    });
    const execute = () => submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [], zero_confirmed: true,
    });
    await expect(execute()).rejects.toBe(error);
    await expect(execute()).rejects.toBe(error);
    const writes = vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(2);
    expect(writes[0][1]?.headers).toEqual(writes[1][1]?.headers);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });

  it("never clears an unknown count when its later same-coordinate retry is rejected", async () => {
    let attempts = 0;
    vi.mocked(api).mockImplementation(async (_path, init) => {
      if (init?.method !== "POST") return hiddenDetail();
      attempts += 1;
      throw attempts === 1 ? new TypeError("unknown") : new ApiError(412, "later rejection", {
        category: "precondition_failed", code: "opening_count_state_invalid",
      });
    });
    const execute = () => submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [], zero_confirmed: true,
    });
    await expect(execute()).rejects.toThrow("unknown");
    await expect(execute()).rejects.toThrow("later rejection");
    await expect(execute()).rejects.toThrow("later rejection");
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    const writes = vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(3);
    for (const [, init] of writes) expect(init?.headers).toEqual(writes[0][1]?.headers);
  });

  it("blocks an overlapping count while the exact first POST is unresolved", async () => {
    let rejectPost!: (error: unknown) => void;
    const post = new Promise<never>((_resolve, reject) => { rejectPost = reject; });
    vi.mocked(api).mockImplementation(async (_path, init) => (
      init?.method === "POST" ? post : hiddenDetail()
    ));
    const execute = () => submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [], zero_confirmed: true,
    });
    const first = execute().catch((error: unknown) => error);
    await vi.waitFor(() => expect(mutationHeaders).toHaveBeenCalledTimes(1));
    await expect(execute()).rejects.toThrow("正在核验");
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    const error = new TypeError("first remains unknown");
    rejectPost(error);
    expect(await first).toBe(error);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });

  it("does not mistake a typed error from result validation for a direct POST rejection", async () => {
    const error = new ApiError(412, "validator failure", {
      category: "precondition_failed", code: "opening_finalize_state_invalid",
    });
    vi.mocked(api).mockImplementation(async () => detail(3));
    const execute = () => executeVersionedOpeningTerminalAction({
      taskId: TASK_ID, action: "post", expectedRoundId: () => ROUND_ID,
      validateDetail, validateResult: () => { throw error; },
    });
    await expect(execute()).rejects.toBe(error);
    await expect(execute()).rejects.toBe(error);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });

  it("blocks overlapping recovery until the original mandatory detail read completes", async () => {
    const countResult = {
      schema_version: "1.0", task_id: TASK_ID, round_id: ROUND_ID, scope_id: SCOPE_ID,
      task_status: "region_review", round_status: "submitted", scope_completed: true,
      round_sealed: true, has_pending_verification: false, replayed: false,
    };
    let finishRead!: (value: unknown) => void;
    let reads = 0;
    let writes = 0;
    vi.mocked(api).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") {
        writes += 1;
        return countResult;
      }
      reads += 1;
      if (reads === 2) {
        return new Promise((resolve) => { finishRead = resolve; });
      }
      return hiddenDetail();
    });
    const execute = () => submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [], zero_confirmed: true,
    });
    const first = execute();
    await vi.waitFor(() => expect(finishRead).toBeTypeOf("function"));
    await expect(execute()).rejects.toThrow("正在核验");
    await expect(closeOpeningStocktake(TASK_ID)).rejects.toThrow("正在核验");
    expect(reads).toBe(2);
    expect(writes).toBe(1);
    finishRead(sealedDetail());
    expect((await first).detail.status).toBe("region_review");
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
  });

  it("blocks a second invocation before its GET can outlive a first strong rejection", async () => {
    let rejectPost!: (error: unknown) => void;
    let reads = 0;
    vi.mocked(api).mockImplementation(async (_path, init) => {
      if (init?.method === "POST") return new Promise((_resolve, reject) => { rejectPost = reject; });
      reads += 1;
      return hiddenDetail();
    });
    const execute = () => submitOpeningScopeCount(TASK_ID, SCOPE_ID, {
      physical_observations: [], zero_confirmed: true,
    });
    const first = execute().catch((error: unknown) => error);
    await vi.waitFor(() => expect(rejectPost).toBeTypeOf("function"));
    await expect(execute()).rejects.toThrow("正在核验");
    const rejection = new ApiError(412, "no effect", {
      category: "precondition_failed", code: "opening_count_state_invalid",
    });
    rejectPost(rejection);
    expect(await first).toBe(rejection);
    expect(reads).toBe(1);
    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it.each(["count", "generic_terminal", "observation"] as const)(
    "serializes %s before the first preflight GET", async (action) => {
      let rejectRead!: (error: unknown) => void;
      vi.mocked(api).mockImplementation(async () => new Promise((_resolve, reject) => { rejectRead = reject; }));
      const execute = () => action === "count"
        ? submitOpeningScopeCount(TASK_ID, SCOPE_ID, { physical_observations: [], zero_confirmed: true })
        : action === "observation"
          ? recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
              disposition: "pending_verification", reason_code: "needs_review", comment: "保持原现场观察待核验",
              resolved_material_id: null, resolved_lot_id: null, resolved_serial_id: null,
            })
          : executeVersionedOpeningTerminalAction({
              taskId: TASK_ID, action: "close", validateDetail,
              validateResult: (value) => value as { task_id: string; resulting_task_status: "closed"; task_version: number },
            });
      const first = execute().catch((error: unknown) => error);
      await vi.waitFor(() => expect(rejectRead).toBeTypeOf("function"));
      await expect(execute()).rejects.toThrow("正在核验");
      expect(api).toHaveBeenCalledTimes(1);
      expect(mutationHeaders).not.toHaveBeenCalled();
      const failure = new TypeError("preflight unavailable");
      rejectRead(failure);
      expect(await first).toBe(failure);
    },
  );

  it("rejects a terminal success whose mandatory reread does not contain the post", async () => {
    const before = {
      ...sealedDetail(),
      status: "approved",
      allowed_actions: ["post"],
      task_version: 14,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        task_id: TASK_ID,
        round_id: ROUND_ID,
        posting_id: "80000000-0000-4000-8000-000000000008",
        inventory_transaction_id: null,
        resulting_task_status: "posted",
        task_version: 15,
        total_quantity: "2.000",
        established_scope_count: 1,
        pending_control_difference_count: 0,
        ledger_cursor: 15,
        replayed: false,
      })
      .mockResolvedValueOnce(before);

    await expect(postOpeningStocktake(TASK_ID)).rejects.toThrow(
      /写后详情未包含过账结果/,
    );
    vi.mocked(api).mockResolvedValueOnce({
      ...before,
      status: "posted",
      allowed_actions: ["close"],
      task_version: 15,
    });
    await postOpeningStocktake(TASK_ID);
  });

  it("refuses an action omitted by the freshly read server policy", async () => {
    vi.mocked(api).mockResolvedValueOnce({
      ...sealedDetail(),
      allowed_actions: [],
    });

    await expect(submitOpeningRegionReview(TASK_ID, {
      decision: "approve",
      items: [],
      comment: "",
    })).rejects.toThrow(/服务端未授权/);
    expect(api).toHaveBeenCalledTimes(1);
    expect(mutationHeaders).not.toHaveBeenCalled();
  });

  it("rejects a headquarters review result returned for a region review request", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(sealedDetail())
      .mockResolvedValueOnce({
        schema_version: "1.0",
        review_id: "80000000-0000-4000-8000-000000000008",
        task_id: TASK_ID,
        round_id: ROUND_ID,
        review_stage: "headquarters",
        decision: "approve",
        resulting_task_status: "approved",
        item_count: 0,
        pending_control_count: 0,
        replayed: false,
      });

    await expect(submitOpeningRegionReview(TASK_ID, {
      decision: "approve",
      items: [{
        difference_id: DIFFERENCE_ID,
        decision: "accept_for_posting",
        comment: "",
      }],
      comment: "",
    })).rejects.toThrow(/区域复核结果与请求不一致/);
    expect(api).toHaveBeenCalledTimes(3);
    expect(api).toHaveBeenNthCalledWith(3, `/v1/stocktakes/opening/${TASK_ID}`);
    vi.mocked(api)
      .mockResolvedValueOnce(sealedDetail())
      .mockRejectedValueOnce(new ApiError(422, "discard malformed intent", {}));
    await expect(submitOpeningRegionReview(TASK_ID, {
      decision: "approve",
      items: [{
        difference_id: DIFFERENCE_ID,
        decision: "accept_for_posting",
        comment: "",
      }],
      comment: "",
    })).rejects.toThrow("discard malformed intent");
  });

  it("enforces the server decision matrix for control and ordinary differences", async () => {
    const control: any = sealedDetail();
    control.differences[0].difference_type = "control_unassigned";
    vi.mocked(api).mockResolvedValueOnce(control);
    await expect(submitOpeningRegionReview(TASK_ID, {
      decision: "approve",
      items: [{
        difference_id: DIFFERENCE_ID,
        decision: "pending_verification",
        comment: "",
      }],
      comment: "",
    })).rejects.toThrow(/控制差异必须填写待核实说明/);
    expect(mutationHeaders).not.toHaveBeenCalled();

    vi.clearAllMocks();
    vi.mocked(api).mockResolvedValueOnce(sealedDetail());
    await expect(submitOpeningRegionReview(TASK_ID, {
      decision: "recount",
      items: [{
        difference_id: DIFFERENCE_ID,
        decision: "accept_for_posting",
        comment: "",
      }],
      comment: "要求复盘",
    })).rejects.toThrow(/逐项复核决定与差异类型或本级结论不一致/);
    expect(mutationHeaders).not.toHaveBeenCalled();
  });

  it("derives review items from observation verification, disposition and round semantics", () => {
    const pending: any = dispositionDetail();
    pending.observations[0].allowed_dispositions = [];
    pending.observations[0].disposition = {
      ...dispositionSummary("pending_verification"),
      reason_code: "NEEDS_CONFIRMATION",
    };
    expect(() => openingReviewItemPolicy(
      pending,
      pending.differences[0],
      "approve",
    )).toThrow(/只能进入复盘或驳回/);
    expect(openingReviewItemPolicy(
      pending,
      pending.differences[0],
      "reject",
    )).toEqual(expect.objectContaining({
      decision: "pending_verification",
      comment_required: true,
    }));

    const resolved: any = dispositionDetail();
    resolved.observations[0].allowed_dispositions = [];
    resolved.observations[0].disposition = dispositionSummary("resolved_existing_master");
    expect(() => openingReviewItemPolicy(
      resolved,
      resolved.differences[0],
      "approve",
    )).toThrow(/必须先进入独立复盘/);
    expect(openingReviewItemPolicy(
      resolved,
      resolved.differences[0],
      "recount",
    ).decision).toBe("recount");

    const initialVerified: any = dispositionDetail();
    initialVerified.observations[0] = pendingObservation({
      verification_status: "verified",
      material_id: MATERIAL_ID,
      allowed_dispositions: [],
    });
    expect(() => openingReviewItemPolicy(
      initialVerified,
      initialVerified.differences[0],
      "approve",
    )).toThrow(/初盘现场观察必须先进入独立复盘/);

    const recountVerified: any = dispositionDetail();
    recountVerified.current_round = {
      ...recountVerified.current_round,
      round_no: 2,
      round_type: "recount",
    };
    recountVerified.observations[0] = pendingObservation({
      verification_status: "verified",
      material_id: MATERIAL_ID,
      allowed_dispositions: [],
    });
    expect(openingReviewItemPolicy(
      recountVerified,
      recountVerified.differences[0],
      "approve",
    ).decision).toBe("accept_for_posting");
  });

  it("posts an exact observation disposition and accepts it only after a strong reread", async () => {
    const before: any = dispositionDetail();
    const after: any = dispositionDetail();
    after.observations[0] = {
      ...after.observations[0],
      disposition: dispositionSummary("resolved_existing_master"),
      allowed_dispositions: [],
    };
    const response = {
      schema_version: "1.0",
      disposition_id: DISPOSITION_ID,
      task_id: TASK_ID,
      round_id: ROUND_ID,
      scope_id: SCOPE_ID,
      observation_id: OBSERVATION_ID,
      disposition: "resolved_existing_master",
      resolved_material_id: MATERIAL_ID,
      resolved_lot_id: null,
      resolved_serial_id: null,
      disposition_manifest_sha256: "a".repeat(64),
      replayed: false,
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce(after);

    await recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "resolved_existing_master",
      reason_code: "MASTER_CONFIRMED",
      comment: "人工核对正式物料目录",
      resolved_material_id: MATERIAL_ID,
    });

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/observations/${OBSERVATION_ID}/disposition`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          disposition: "resolved_existing_master",
          reason_code: "MASTER_CONFIRMED",
          comment: "人工核对正式物料目录",
          resolved_material_id: MATERIAL_ID,
          resolved_lot_id: null,
          resolved_serial_id: null,
        }),
      }),
    );
    expect(api).toHaveBeenNthCalledWith(3, `/v1/stocktakes/opening/${TASK_ID}`);
  });

  it("never guesses master IDs and forbids them on unresolved dispositions", async () => {
    await expect(recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "resolved_existing_master",
      reason_code: "MASTER_CONFIRMED",
      comment: "",
    })).rejects.toThrow(/必须填写物料 UUID/);
    await expect(recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "pending_verification",
      reason_code: "NEEDS_CONFIRMATION",
      comment: "继续核实",
      resolved_material_id: MATERIAL_ID,
    })).rejects.toThrow(/不得填写主数据 UUID/);
    await expect(recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "requires_recount",
      reason_code: "RECOUNT_REQUIRED",
      comment: "",
    })).rejects.toThrow(/必须填写说明/);
    expect(api).not.toHaveBeenCalled();
  });

  it("retains observation coordinates when the success response cannot be proven by reread", async () => {
    const before: any = dispositionDetail();
    const after: any = dispositionDetail();
    after.observations[0] = {
      ...after.observations[0],
      disposition: {
        ...dispositionSummary("requires_recount"),
        reason_code: "RECOUNT_REQUIRED",
      },
      allowed_dispositions: [],
    };
    const response = {
      schema_version: "1.0",
      disposition_id: DISPOSITION_ID,
      task_id: TASK_ID,
      round_id: ROUND_ID,
      scope_id: SCOPE_ID,
      observation_id: OBSERVATION_ID,
      disposition: "requires_recount",
      resolved_material_id: null,
      resolved_lot_id: null,
      resolved_serial_id: null,
      disposition_manifest_sha256: "b".repeat(64),
      replayed: false,
    };
    const input = {
      disposition: "requires_recount" as const,
      reason_code: "RECOUNT_REQUIRED",
      comment: "现场标识无法可靠映射，必须复盘",
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(response)
      .mockRejectedValueOnce(new TypeError("final disposition proof lost"))
      .mockResolvedValueOnce(after);

    await expect(recordOpeningObservationDisposition(
      TASK_ID,
      OBSERVATION_ID,
      input,
    )).rejects.toThrow("final disposition proof lost");
    const recovered = await recordOpeningObservationDisposition(
      TASK_ID,
      OBSERVATION_ID,
      input,
    );

    expect(mutationHeaders).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === "POST"))
      .toHaveLength(1);
    expect(recovered.result).toEqual(response);
  });

  it("rejects an observation disposition response for a different target", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(dispositionDetail())
      .mockResolvedValueOnce({
        schema_version: "1.0",
        disposition_id: DISPOSITION_ID,
        task_id: TASK_ID,
        round_id: ROUND_ID,
        scope_id: SCOPE_ID,
        observation_id: "84000000-0000-4000-8000-000000000008",
        disposition: "requires_recount",
        resolved_material_id: null,
        resolved_lot_id: null,
        resolved_serial_id: null,
        disposition_manifest_sha256: "c".repeat(64),
        replayed: false,
      })
      .mockResolvedValueOnce(dispositionDetail());

    await expect(recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "requires_recount",
      reason_code: "RECOUNT_REQUIRED",
      comment: "必须复盘确认",
    })).rejects.toThrow(/结果与精确请求目标不一致/);
    vi.mocked(api)
      .mockResolvedValueOnce(dispositionDetail())
      .mockRejectedValueOnce(new ApiError(422, "discard malformed disposition", {}));
    await expect(recordOpeningObservationDisposition(TASK_ID, OBSERVATION_ID, {
      disposition: "requires_recount",
      reason_code: "RECOUNT_REQUIRED",
      comment: "必须复盘确认",
    })).rejects.toThrow("discard malformed disposition");
  });

  it.each([false, true])("binds recount assignments to visible scopes and the freshly read round (selector=%s)", async (withSelector) => {
    const before = {
      ...sealedDetail(),
      status: "recount_required",
      allowed_actions: ["open_recount"],
    };
    const after = {
      ...hiddenDetail(),
      task_version: 5,
      current_round: {
        ...hiddenDetail().current_round,
        round_id: "90000000-0000-4000-8000-000000000009",
        round_no: 2,
        round_type: "recount",
      },
    };
    vi.mocked(api)
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        recount_case_id: "a0000000-0000-4000-8000-00000000000a",
        task_id: TASK_ID,
        source_round_id: ROUND_ID,
        next_round_id: after.current_round.round_id,
        next_round_no: 2,
        scope_count: 1,
        resulting_task_status: "counting",
        replayed: false,
      })
      .mockResolvedValueOnce(after);

    await openOpeningRecount(TASK_ID, {
      assignments: [{ scope_id: SCOPE_ID, assignee_user_id: "engineer-001" }],
      reason: "区域复核要求重新清点",
    }, withSelector ? { task_version: before.task_version, source_round_id: ROUND_ID } : undefined);

    expect(api).toHaveBeenNthCalledWith(
      2,
      `/v1/stocktakes/opening/${TASK_ID}/rounds/${ROUND_ID}/recount`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(JSON.parse(String(vi.mocked(api).mock.calls[1][1]?.body))).toEqual({
      assignments: [{ scope_id: SCOPE_ID, assignee_user_id: "engineer-001" }],
      reason: "区域复核要求重新清点",
    });
  });

  it.each([
    { task_version: 3, source_round_id: ROUND_ID },
    { task_version: 4, source_round_id: "90000000-0000-4000-8000-000000000009" },
  ])("rejects stale selector coordinates before a recount POST %#", async (expected) => {
    vi.mocked(api).mockResolvedValue({ ...sealedDetail(), status: "recount_required", allowed_actions: ["open_recount"] });
    await expect(openOpeningRecount(TASK_ID, {
      assignments: [{ scope_id: SCOPE_ID, assignee_user_id: "engineer-001" }], reason: "区域复核要求重新清点",
    }, expected)).rejects.toThrow("人员选择绑定");
    expect(api).toHaveBeenCalledTimes(1);
    expect(mutationHeaders).not.toHaveBeenCalled();
  });
});
