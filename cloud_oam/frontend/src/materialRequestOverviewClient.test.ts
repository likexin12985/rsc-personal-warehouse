import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { parseMaterialRequestOverview, readMaterialRequestOverview } from "./materialRequestOverviewClient";
import example from "./test-fixtures/overview-approved.json";

vi.mock("./api", () => ({ api: vi.fn() }));
beforeEach(() => vi.mocked(api).mockReset());

describe("backend-generated overview contract", () => {
  it("keeps approved requests independent from personal inbound", () => {
    const result = parseMaterialRequestOverview(example);
    expect(result.counts.approval_level_3.approved).toBe(1);
    expect(result.counts.personal_inbound_status.posted).toBe(0);
  });
  it.each(["unknown", "missing", "inflated", "fractional", "false-zero"])("rejects %s counts", kind => {
    const data = structuredClone(example);
    if (kind === "unknown") (data.counts.request_status as Record<string, number>).unexpected = 0;
    if (kind === "missing") delete (data.counts as Partial<typeof data.counts>).oam_receipt_status;
    if (kind === "inflated") data.counts.personal_inbound_status.posted = 1;
    if (kind === "fractional") data.counts.request_status.approved = 0.5;
    if (kind === "false-zero") data.matched_requests = 0;
    expect(() => parseMaterialRequestOverview(data)).toThrow();
  });
  it("rejects a response for another filter", () => {
    expect(() => parseMaterialRequestOverview(example, { created_from: "2026-10-01T00:00:00+08:00" })).toThrow();
  });
  it("sends only a no-store GET and carries cancellation", async () => {
    vi.mocked(api).mockResolvedValue(example);
    const signal = new AbortController().signal;
    await readMaterialRequestOverview({}, signal);
    expect(api).toHaveBeenCalledWith("/v1/reports/material-requests", { cache: "no-store", signal });
  });
  it("rejects invalid query coordinates before the request", async () => {
    await expect(readMaterialRequestOverview({ organization_id: "invalid" })).rejects.toThrow();
    await expect(readMaterialRequestOverview({ created_from: "2026-10-01" })).rejects.toThrow();
    expect(api).not.toHaveBeenCalled();
  });
});
