import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { executeWorkOrderMaterialOperation, listWorkOrderMaterialOperations, validateWorkOrderMaterialOperationHistory, validateWorkOrderMaterialOperationInput, validateWorkOrderMaterialOperationResult } from "./workOrderMaterialOperations";

const id = "11111111-1111-4111-8111-111111111111";
const target = "22222222-2222-4222-8222-222222222222";
const workOrder = "33333333-3333-4333-8333-333333333333";
const base = { operator_person_id: id, idempotency_key: "work-order-material-123456", request_id: "web-work-order-123456" };
const line = { material_id: id, stock_account_id: target, quantity: "1.000", serial_ids: [], condition_before: "new" as const, serial_verifications: [] };

describe("work order material operations", () => {
  it("validates operation-specific line coordinates", () => {
    expect(validateWorkOrderMaterialOperationInput({ ...base, lines: [line] }, "consume").lines[0].stock_account_id).toBe(target);
    expect(() => validateWorkOrderMaterialOperationInput({ ...base, lines: [{ ...line, condition_before: "new", target_stock_account_id: target }] }, "recover")).toThrow(ApiError);
    expect(validateWorkOrderMaterialOperationInput({ ...base, lines: [{ material_id: id, target_stock_account_id: target, quantity: "1.000", condition_before: "used", serial_ids: [], serial_verifications: [] }] }, "recover").lines[0].target_stock_account_id).toBe(target);
  });

  it("rejects zero quantities and unknown fields", () => {
    expect(() => validateWorkOrderMaterialOperationInput({ ...base, lines: [{ ...line, quantity: "0.000" }] }, "consume")).toThrow(ApiError);
    expect(() => validateWorkOrderMaterialOperationInput({ ...base, lines: [{ ...line, extra: true }] }, "consume")).toThrow(ApiError);
  });

  it("posts to the operation endpoint with both replay coordinates", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "posted" }), { status: 200, headers: { "content-type": "application/json" } }));
    const result = await executeWorkOrderMaterialOperation(workOrder, "consume", { ...base, lines: [line] }, { "X-Request-ID": "web-work-order-123456", "Idempotency-Key": "work-order-material-123456" });
    expect(result.status).toBe("posted");
    const request = fetcher.mock.calls[0][0] as string;
    expect(request).toContain(`/api/v1/work-orders/${workOrder}/material-operations/consume`);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).get("Idempotency-Key")).toBe("work-order-material-123456");
    fetcher.mockRestore();
  });

  it("validates response coordinates", () => {
    expect(() => validateWorkOrderMaterialOperationResult({ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "posted", extra: true })).toThrow(ApiError);
    expect(validateWorkOrderMaterialOperationResult({ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "reverse", status: "posted" }).operation_type).toBe("reverse");
    expect(() => validateWorkOrderMaterialOperationResult({ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "pending" })).toThrow(ApiError);
    expect(() => validateWorkOrderMaterialOperationResult({ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "replayed" })).toThrow(ApiError);
  });

  it("reads operation history without replay headers", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    expect((await listWorkOrderMaterialOperations(workOrder)).items).toHaveLength(0);
    expect(new Headers(fetcher.mock.calls[0][1]?.headers).has("Idempotency-Key")).toBe(false);
    fetcher.mockRestore();
  });

  it("validates immutable material and serial coordinates in history", () => {
    const result = validateWorkOrderMaterialOperationHistory({ items: [{ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "posted", lines: [{ line_no: 1, material_id: id, stock_account_id: target, quantity: "2.000", condition_before: "new", condition_after: "used", serial_ids: [id] }] }] });
    expect(result.items[0].lines[0].serial_ids).toEqual([id]);
    expect(() => validateWorkOrderMaterialOperationHistory({ items: [{ schema_version: "1.0", operation_id: id, operation_no: "OP-1", work_order_id: workOrder, posting_transaction_id: target, operation_type: "consume", status: "posted", lines: [{ line_no: 1, material_id: id, stock_account_id: target, quantity: "0", condition_before: "new", condition_after: null, serial_ids: [] }] }] })).toThrow(ApiError);
  });
});
