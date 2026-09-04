import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { __openingMutationIntentTestOnly as stocktake } from "./formalOpeningStocktake";
import { __openingReconciliationIntentTestOnly as reconciliation } from "./formalOpeningReconciliation";

const stocktakeCases = [
  ["count", "opening_count_state_invalid"],
  ["review_region", "opening_review_stage_state_invalid"],
  ["review_headquarters", "opening_review_stage_state_invalid"],
  ["open_recount", "opening_recount_state_invalid"],
  ["dispose_observation", "opening_observation_disposition_state_invalid"],
  ["post", "opening_finalize_state_invalid"],
  ["close", "opening_finalize_state_invalid"],
  ["close", "opening_close_reconciliation_pending"],
] as const;
const reconciliationCases = [
  ["start", "opening_reconciliation_task_state_invalid"],
  ["start", "opening_reconciliation_not_required"],
  ["explain", "opening_reconciliation_explain_state_invalid"],
  ["explain", "opening_reconciliation_item_set_mismatch"],
  ["approve", "opening_reconciliation_approve_state_invalid"],
  ["approve", "opening_reconciliation_explanation_incomplete"],
] as const;

function assertExactClassifier(classify: (error: unknown) => boolean, code: string) {
  expect(classify(new ApiError(412, "no effect", { category: "precondition_failed", code }))).toBe(true);
  for (const status of [400, 401, 403, 404, 409, 422, 429, 500, 503]) {
    expect(classify(new ApiError(status, "retain", { category: "precondition_failed", code }))).toBe(false);
  }
  for (const category of [undefined, "invalid_request", "conflict", "database_guard_rejected"]) {
    expect(classify(new ApiError(412, "retain", { category, code }))).toBe(false);
  }
  for (const invalidCode of [undefined, "unknown_code", "database_guard_rejected", "unsafe-code"]) {
    expect(classify(new ApiError(412, "retain", { category: "precondition_failed", code: invalidCode }))).toBe(false);
  }
  expect(classify(new ApiError(412, "local error"))).toBe(false);
  expect(classify(new TypeError("network unknown"))).toBe(false);
  expect(classify({ status: 412, category: "precondition_failed", code, responseReceived: true })).toBe(false);
  for (const headerCode of ["idempotency_key_invalid", "x_request_id_invalid"]) {
    expect(classify(new ApiError(400, "header rejected", { category: "invalid_request", code: headerCode }))).toBe(true);
    expect(classify(new ApiError(412, "retain", { category: "invalid_request", code: headerCode }))).toBe(false);
    expect(classify(new ApiError(400, "retain", { category: "precondition_failed", code: headerCode }))).toBe(false);
  }
}

describe("opening first-direct-POST rejection classifiers", () => {
  it.each(stocktakeCases)("requires the exact stocktake action/status/category/code: %s %s", (action, code) => {
    if (!stocktake) throw new Error("test hook unavailable");
    const classify = stocktake.isDefinitiveRejection;
    assertExactClassifier((error) => classify(error, action), code);
    for (const [, otherCode] of stocktakeCases) {
      const allowed = stocktakeCases.some(([candidateAction, candidateCode]) => (
        candidateAction === action && candidateCode === otherCode
      ));
      expect(stocktake.isDefinitiveRejection(new ApiError(412, "rejected", {
        category: "precondition_failed", code: otherCode,
      }), action)).toBe(allowed);
    }
  });

  it.each(reconciliationCases)("requires the exact reconciliation action/status/category/code: %s %s", (action, code) => {
    if (!reconciliation) throw new Error("test hook unavailable");
    const classify = reconciliation.isDefinitiveRejection;
    assertExactClassifier((error) => classify(error, action), code);
    for (const [otherAction, otherCode] of reconciliationCases) {
      expect(reconciliation.isDefinitiveRejection(new ApiError(412, "rejected", {
        category: "precondition_failed", code: otherCode,
      }), action)).toBe(otherAction === action);
    }
  });
});
