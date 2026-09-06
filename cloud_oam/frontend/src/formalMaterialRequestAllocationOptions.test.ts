import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  validateMaterialRequestAllocationOptionPage,
} from "./formalMaterialRequestAllocationOptions";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";

const PERSON_ID = "10000000-0000-4000-8000-000000000001";
const REQUEST_ID = "20000000-0000-4000-8000-000000000001";
const LINE_ID = "30000000-0000-4000-8000-000000000001";
const MATERIAL_ID = "40000000-0000-4000-8000-000000000001";

function page() {
  return {
    schema_version: "1.0",
    request_id: REQUEST_ID,
    request_line_id: LINE_ID,
    request_version: 3,
    current_revision_id: LINE_ID,
    current_revision_no: 2,
    material_id: MATERIAL_ID,
    final_approved_qty: "2.000",
    cancelled_qty: "0.000",
    allocatable_qty: "2.000",
    projection_status: "ready",
    opening_balance_status: "established",
    projected_at: "2026-09-07T10:00:00+08:00",
    ledger_cursor: 7,
    items: [],
  };
}

describe("formal material-request allocation options", () => {
  it("accepts the strict empty candidate page", () => {
    expect(validateMaterialRequestAllocationOptionPage(page()).items).toHaveLength(0);
  });

  it("rejects unknown fields and non-ready projections", () => {
    expect(() => validateMaterialRequestAllocationOptionPage({ ...page(), extra: true })).toThrow(ApiError);
    expect(() => validateMaterialRequestAllocationOptionPage({ ...page(), projection_status: "stale" })).toThrow(ApiError);
  });

  it("uses the exact read-only endpoint and no-store cache policy", async () => {
    const requester = vi.fn().mockResolvedValue(page());
    const adapter = createFormalMaterialRequestAdapter(
      { person_id: PERSON_ID, authorization_version: 1 },
      requester,
    );
    await expect(adapter.listAllocationOptions(REQUEST_ID, LINE_ID)).resolves.toMatchObject({
      request_id: REQUEST_ID,
      request_line_id: LINE_ID,
    });
    expect(requester).toHaveBeenCalledWith(
      `/v1/material-requests/${REQUEST_ID}/allocation-options?request_line_id=${LINE_ID}`,
      {
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      },
    );
  });
});
