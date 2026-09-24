import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "./api";
import { loadControlBatches, validateControlBatchPage } from "./formalOpeningControlDirectory";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), apiNoReplay: vi.fn() }));
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const actor = { person_id: id(1), authorization_version: 7 };
function page() { return { schema_version: "rsc.opening_control_batches.v1", actor_person_id: actor.person_id,
  authorization_version: actor.authorization_version, region_org_id: id(2), start_ready: false, admission_status: "not_evaluated",
  next_after_id: null, items: [{ publication_id: id(3), source_system_id: id(4), source_name: "控制来源",
    captured_at: "2026-09-20T01:00:00Z", published_at: "2026-09-20T01:01:00Z", valid_until: "2026-09-20T01:45:00Z",
    record_count: 0, is_latest: false }] }; }
beforeEach(() => { vi.mocked(apiNoReplay).mockReset(); });
describe("redacted published batch directory", () => {
  it("retains historical proven-zero summaries without claiming start eligibility", async () => {
    vi.mocked(apiNoReplay).mockResolvedValue(page());
    const result = await loadControlBatches(actor, id(2));
    expect(result.items[0]).toMatchObject({ recordCount: 0, isLatest: false });
    expect(Object.isFrozen(result.items)).toBe(true);
    const [url, options] = vi.mocked(apiNoReplay).mock.calls[0];
    expect(url).toBe(`/v1/stocktakes/opening/start-options/control-batches?region_org_id=${id(2)}&limit=50`);
    expect(options).toMatchObject({ method: "GET", cache: "no-store" });
    expect(options?.body).toBeUndefined();
  });
  it.each(["person", "version", "region", "start", "admission", "cursor", "private"])("refuses changed %s response", (kind) => {
    const value: any = page();
    if (kind === "person") value.actor_person_id = id(99);
    if (kind === "version") value.authorization_version += 1;
    if (kind === "region") value.region_org_id = id(99);
    if (kind === "start") value.start_ready = true;
    if (kind === "admission") value.admission_status = "ready";
    if (kind === "cursor") value.next_after_id = id(99);
    if (kind === "private") value.items[0].payload_jsonb = { storage_key: "private" };
    expect(() => validateControlBatchPage(value, actor, id(2))).toThrow();
  });
  it.each(["order", "count", "boolean", "time", "naive", "nil", "label"])("refuses corrupt %s data", (kind) => {
    const value: any = page();
    if (kind === "order") value.items.push({ ...value.items[0] });
    if (kind === "count") value.items[0].record_count = true;
    if (kind === "boolean") value.items[0].is_latest = 1;
    if (kind === "time") value.items[0].valid_until = value.items[0].published_at;
    if (kind === "naive") value.items[0].captured_at = "2026-09-20T01:00:00";
    if (kind === "nil") value.items[0].publication_id = "00000000-0000-0000-0000-000000000000";
    if (kind === "label") value.items[0].source_name = " control ";
    expect(() => validateControlBatchPage(value, actor, id(2))).toThrow();
  });
  it("enforces ordered pagination and the last visible cursor", () => {
    const value: any = page();
    value.items = Array.from({ length: 50 }, (_, index) => ({ ...value.items[0], publication_id: id(index + 3) }));
    value.next_after_id = id(52);
    expect(validateControlBatchPage(value, actor, id(2)).nextAfterId).toBe(id(52));
    expect(() => validateControlBatchPage(value, actor, id(2), id(3))).toThrow();
    value.items.push({ ...value.items[0], publication_id: id(53) });
    expect(() => validateControlBatchPage(value, actor, id(2))).toThrow();
  });
  it("does not retry failed reads", async () => {
    vi.mocked(apiNoReplay).mockRejectedValue(new Error("network"));
    await expect(loadControlBatches(actor, id(2))).rejects.toThrow("network");
    expect(apiNoReplay).toHaveBeenCalledTimes(1);
  });
});
