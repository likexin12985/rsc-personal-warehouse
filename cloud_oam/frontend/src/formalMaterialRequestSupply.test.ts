import { describe, expect, it } from "vitest";
import {
  supplyMutationMatchesDetail,
  validateSupplyCommandStatus,
  validateSupplyCreateInput,
  validateSupplyMutationResult,
  validateSupplyUpdateInput,
} from "./formalMaterialRequestSupply";

const REQUEST = "10000000-0000-4000-8000-000000000001";
const LINE = "20000000-0000-4000-8000-000000000001";
const TASK = "30000000-0000-4000-8000-000000000001";
const REVISION = "40000000-0000-4000-8000-000000000001";
const INSTANCE = "50000000-0000-4000-8000-000000000001";
function states() {
  return {
    request_status: "approved", allocation_status: "not_allocated", reservation_status: "not_reserved",
    outbound_status: "not_started", shipment_status: "not_started", logistics_signature_status: "not_signed",
    oam_receipt_status: "not_occurred", personal_inbound_status: "not_started", notification_status: "not_started",
    reconciliation_status: "not_started",
  };
}
function create(): any {
  return { expected_request_version: 8, request_line_id: LINE, supply_type: "star_replenishment",
    reference_no: null, expected_qty: "1.001", expected_date: "2026-09-10", note: "跟进补货" };
}
function update(): any {
  return { expected_request_version: 8, expected_task_version: 2, status: "awaiting_supply",
    reference_no: "STAR-PLAN-001", expected_date: null, comment: "跟进预计供应" };
}
function response(): any {
  return { schema_version: "1.0", request_id: REQUEST, action: "create_supply_task", request_version: 9,
    revision_id: REVISION, revision_no: 1, approval_instance_id: INSTANCE, approval_attempt_no: 1,
    current_step_id: null, states: states(), idempotency_replayed: false,
    supply_task_id: TASK, task_no: "SUPPLY-001", task_status: "open", task_version: 0 };
}

describe("supply command wire contracts", () => {
  it("preserves exact quantity, references and explicit optional fields", () => {
    expect(validateSupplyCreateInput(create())).toEqual(create());
    expect(validateSupplyUpdateInput(update(), "update_supply_task")).toEqual(update());
    const maximum = create();
    maximum.reference_no = "A".repeat(160);
    expect(validateSupplyCreateInput(maximum).reference_no).toHaveLength(160);
  });
  it.each([0, true, "1", "0.000", "1.0000", "1e0", "NaN", "-1.000", "1000000000000000.000"])(
    "rejects unsafe quantity %j", (quantity) => {
      expect(() => validateSupplyCreateInput({ ...create(), expected_qty: quantity })).toThrow();
    },
  );
  it.each([true, "8", -1, 1.5])("rejects coerced version %j", (version) => {
    expect(() => validateSupplyCreateInput({ ...create(), expected_request_version: version })).toThrow();
    expect(() => validateSupplyUpdateInput({ ...update(), expected_task_version: version }, "update_supply_task")).toThrow();
  });
  it("rejects unknown fields, unsafe references, missing fields and impossible dates", () => {
    for (const input of [
      { ...create(), reference_no: "A".repeat(161) }, { ...create(), reference_no: "<script>" },
      { ...create(), expected_date: "2026-02-30" }, { ...create(), shipment_status: "shipped" },
      { ...create(), note: "bad\ncontrol" },
    ]) expect(() => validateSupplyCreateInput(input)).toThrow();
    const { reference_no: _ref, ...missing } = create();
    expect(() => validateSupplyCreateInput(missing)).toThrow();
  });
  it("separates plan cancellation from fulfillment and fixes creation quantities", () => {
    expect(() => validateSupplyUpdateInput({ ...update(), expected_qty: "3.000" }, "update_supply_task")).toThrow();
    expect(() => validateSupplyUpdateInput({ ...update(), status: "fulfilled" }, "update_supply_task")).toThrow();
    expect(() => validateSupplyUpdateInput({ ...update(), status: "cancelled" }, "update_supply_task")).toThrow();
    expect(() => validateSupplyUpdateInput(update(), "cancel_supply_task")).toThrow();
    expect(() => validateSupplyUpdateInput({ ...update(), status: "cancelled", comment: "" }, "cancel_supply_task")).toThrow();
    expect(() => validateSupplyUpdateInput({ ...update(), status: "reference_registered", reference_no: null }, "update_supply_task")).toThrow();
    expect(validateSupplyUpdateInput({ ...update(), status: "cancelled" }, "cancel_supply_task").status).toBe("cancelled");
  });
  it("requires exact request and task acknowledgements", () => {
    const expected = { requestId: REQUEST, action: "create_supply_task" as const, previousVersion: 8 };
    expect(validateSupplyMutationResult(response(), expected).supply_task_id).toBe(TASK);
    for (const changes of [
      { request_version: 10 }, { task_version: 1 }, { task_status: "fulfilled" },
      { approval_instance_id: null, approval_attempt_no: null }, { current_step_id: LINE },
      { states: { ...states(), request_status: "cancelled" } }, { task_no: "" },
    ]) expect(() => validateSupplyMutationResult({ ...response(), ...changes }, expected)).toThrow();
    const updated = { ...response(), action: "update_supply_task", task_status: "awaiting_supply", task_version: 3 };
    expect(validateSupplyMutationResult(updated, { ...expected, action: "update_supply_task", taskId: TASK, previousTaskVersion: 2 }).task_version).toBe(3);
    expect(() => validateSupplyMutationResult(updated, { ...expected, action: "update_supply_task", taskId: LINE, previousTaskVersion: 2 })).toThrow();
  });
  it("keeps not-observed unknown and validates historical command facts", () => {
    expect(validateSupplyCommandStatus({ schema_version: "1.0", lookup_status: "not_observed", command: null }).command).toBeNull();
    const { schema_version: _schema, idempotency_replayed: _replay, ...command } = response();
    const status = { schema_version: "1.0", lookup_status: "confirmed", command: { ...command, occurred_at: "2026-09-05T08:00:00Z" } };
    expect(validateSupplyCommandStatus(status).command?.task_version).toBe(0);
    expect(() => validateSupplyCommandStatus({ ...status, lookup_status: "not_observed" })).toThrow();
    expect(() => validateSupplyCommandStatus({ ...status, command: { ...status.command, idempotency_key: "forbidden" } })).toThrow();
  });
  it("requires post-write reread to preserve approval, all axes and the exact new plan", () => {
    const before: any = { request_id: REQUEST, request_version: 8, current_revision_id: REVISION, current_revision_no: 1,
      approval_instance: { instance_id: INSTANCE, attempt_no: 1 }, approval_history: [], lines: [], states: states(), supply_tasks: [] };
    const task = { id: TASK, task_no: "SUPPLY-001", request_line_id: LINE, substitution_decision_id: null,
      supply_type: "star_replenishment", reference_no: null, expected_qty: "1.001", original_equivalent_qty: "1.001",
      expected_date: "2026-09-10", status: "open", version: 0, created_at: "2026-09-05T08:00:00Z",
      updated_at: "2026-09-05T08:00:00Z", allowed_actions: [] };
    const after = { ...before, request_version: 9, supply_tasks: [task] };
    const result = validateSupplyMutationResult(response(), { requestId: REQUEST, action: "create_supply_task", previousVersion: 8 });
    expect(supplyMutationMatchesDetail(result, before, after, create())).toBe(true);
    expect(supplyMutationMatchesDetail(result, before, { ...after, supply_tasks: [{ ...task, expected_qty: "2.000" }] }, create())).toBe(false);
    expect(supplyMutationMatchesDetail(result, before, { ...after, states: { ...states(), shipment_status: "shipped" } }, create())).toBe(false);
    expect(supplyMutationMatchesDetail(result, before, { ...after, request_version: 10 }, create())).toBe(false);
  });
});
