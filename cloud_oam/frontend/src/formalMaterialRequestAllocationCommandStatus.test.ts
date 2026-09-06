import { describe, expect, it } from "vitest";

import { validateMaterialRequestAllocationCommandStatus } from "./formalMaterialRequestAllocationCommandStatus";

const ID = (tail: string) => `10000000-0000-4000-8000-${tail.padStart(12, "0")}`;

function command() {
  return {
    request_id: ID("1"), allocation_id: ID("2"), allocation_no: "AL-20260907-ABC",
    request_version: 3, revision_id: ID("3"), revision_no: 1, request_line_id: ID("4"),
    source_stock_account_id: ID("5"), allocated_qty: "1.000", allocation_status: "allocated",
    request_status: "approved", state_axes: {
      request_status: "approved", allocation_status: "allocated", reservation_status: "not_reserved",
      outbound_status: "not_started", shipment_status: "not_started", logistics_signature_status: "not_signed",
      oam_receipt_status: "not_occurred", personal_inbound_status: "not_started",
      notification_status: "not_started", reconciliation_status: "not_started",
    }, idempotency_replayed: true,
  };
}

describe("formal material-request allocation command status", () => {
  it("keeps not_observed as an explicit non-result", () => {
    expect(validateMaterialRequestAllocationCommandStatus({
      schema_version: "1.0", lookup_status: "not_observed", command: null,
    })).toEqual({ schema_version: "1.0", lookup_status: "not_observed", command: null });
  });

  it("validates a confirmed command and rejects state drift", () => {
    const result = validateMaterialRequestAllocationCommandStatus({
      schema_version: "1.0", lookup_status: "confirmed", command: command(),
    });
    expect(result.command?.allocated_qty).toBe("1.000");
    expect(() => validateMaterialRequestAllocationCommandStatus({
      schema_version: "1.0", lookup_status: "confirmed",
      command: { ...command(), state_axes: { ...command().state_axes, request_status: "submitted" } },
    })).toThrow();
    expect(() => validateMaterialRequestAllocationCommandStatus({
      schema_version: "1.0", lookup_status: "not_observed", command: command(),
    })).toThrow();
  });
});
