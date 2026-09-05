import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "./api";
import {
  loadOpeningStartOptions, mergeOpeningStartOptions, openingPreparationContext, openingPreparationContextKey,
  validateOpeningStartOptionPage, type OpeningPreparationStage,
} from "./formalOpeningStartOptions";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), apiNoReplay: vi.fn() }));
const id = (number: number) => `10000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const actor = { person_id: id(1), authorization_version: 7 };
const stages: OpeningPreparationStage[] = ["regions", "asset-owners", "locations", "assignees"];
function context(stage: OpeningPreparationStage) {
  return openingPreparationContext(actor, stage, stage === "regions" ? {}
    : stage === "asset-owners" ? { region_org_id: id(2) }
      : stage === "locations" ? { region_org_id: id(2), owner_org_id: id(3) }
        : { region_org_id: id(2), owner_org_id: id(3), location_id: id(4) });
}
function option(stage: OpeningPreparationStage, number = 5): Record<string, unknown> {
  if (stage === "regions") return { region_org_id: id(number), code: "REGION", name: "测试区域", province_code: "330000" };
  if (stage === "asset-owners") return { owner_org_id: id(number), code: "OWNER", name: "资产区域" };
  if (stage === "locations") return { location_id: id(number), code: "LOCATION", name: "测试个人仓", location_type: "personal",
    physical_owner_org_id: id(2), physical_owner_name: "实物归属区域", custodian_person_id: id(7), custodian_name: "测试保管人" };
  return { person_id: id(number), assignee_user_id: `formal-user-${number}`, name: "测试执行人" };
}
function wire(stage: OpeningPreparationStage, items = [option(stage)]): Record<string, unknown> {
  const { stage: _stage, ...anchors } = context(stage);
  return { ...anchors, schema_version: "1.0", start_ready: false, control_evidence_status: "control_evidence_not_evaluated",
    items, [stage === "assignees" ? "next_after_person_id" : "next_after_id"]: null };
}
beforeEach(() => vi.mocked(apiNoReplay).mockReset());

describe("strict read-only opening preparation directories", () => {
  it.each(stages)("loads %s with fixed GET/no-replay transport and no command coordinates", async (stage) => {
    vi.mocked(apiNoReplay).mockResolvedValue(wire(stage));
    const result = await loadOpeningStartOptions(context(stage));
    const [path, init] = vi.mocked(apiNoReplay).mock.calls[0];
    expect(path.split("?")[0]).toBe(`/v1/stocktakes/opening/start-options/${stage}`);
    const query = new URLSearchParams(path.split("?")[1]);
    expect(query.get("limit")).toBe("50");
    for (const [key, value] of Object.entries(context(stage))) {
      if (["stage", "actor_person_id", "authorization_version"].includes(key)) expect(query.has(key)).toBe(false);
      else expect(query.get(key)).toBe(value);
    }
    expect(init).toEqual({ method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" } });
    expect(result.context).toEqual(context(stage));
    expect(result.items[0].stage).toBe(stage);
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.context)).toBe(true);
    expect(Object.isFrozen(result.items)).toBe(true);
    expect(Object.isFrozen(result.items[0])).toBe(true);
  });

  it.each(stages)("rejects readiness claims, extra evidence and changed %s identity anchors", (stage) => {
    for (const patch of [
      { schema_version: "2.0" }, { start_ready: true }, { start_ready: "false" },
      { control_evidence_status: "validated" }, { control_qty: "0.000" }, { sync_run_id: id(8) },
      { scope_id: id(8) }, { actor_person_id: id(9) }, { authorization_version: 8 },
      { authorization_version: "7" }, { authorization_version: 0 }, { next_after_id: undefined, mobile: "private" },
    ]) expect(() => validateOpeningStartOptionPage({ ...wire(stage), ...patch }, context(stage))).toThrow("不一致");
    const missing = wire(stage);
    delete missing.start_ready;
    expect(() => validateOpeningStartOptionPage(missing, context(stage))).toThrow("不一致");
  });

  it.each(stages)("checks every selected %s coordinate, including valid-shaped wrong UUIDs", (stage) => {
    for (const field of ["region_org_id", "owner_org_id", "location_id"]) {
      const value = wire(stage);
      if (Object.hasOwn(value, field)) {
        expect(() => validateOpeningStartOptionPage({ ...value, [field]: id(99) }, context(stage))).toThrow("不一致");
      } else {
        expect(() => validateOpeningStartOptionPage({ ...value, [field]: id(99) }, context(stage))).toThrow("不一致");
      }
    }
  });

  it.each(stages)("rejects malformed %s identifiers, labels and extra item fields", (stage) => {
    const row = option(stage);
    const idField = stage === "regions" ? "region_org_id" : stage === "asset-owners" ? "owner_org_id"
      : stage === "locations" ? "location_id" : "person_id";
    for (const patch of [
      { [idField]: "00000000-0000-0000-0000-000000000000" }, { [idField]: "../other" },
      { name: "" }, { name: " padded " }, { name: "private\ntext" }, { name: "x".repeat(201) },
      { book_qty: "0.000" }, { role_codes: ["admin"] }, { phone: "private" },
    ]) expect(() => validateOpeningStartOptionPage(wire(stage, [{ ...row, ...patch }]), context(stage))).toThrow("不一致");
  });

  it("keeps assets, physical owner and custody separate and rejects unsafe personal locations", () => {
    const page = validateOpeningStartOptionPage(wire("locations"), context("locations"));
    expect(page.context.owner_org_id).toBe(id(3));
    expect(page.items[0]).toMatchObject({ physicalOwnerId: id(2), custodianPersonId: id(7) });
    for (const patch of [
      { location_type: "quarantine" }, { physical_owner_org_id: "bad" },
      { physical_owner_name: "" }, { custodian_person_id: null }, { custodian_name: null },
      { custodian_person_id: null, custodian_name: null }, { custodian_name: "x".repeat(121) },
    ]) expect(() => validateOpeningStartOptionPage(wire("locations", [{ ...option("locations"), ...patch }]), context("locations"))).toThrow();
    expect(validateOpeningStartOptionPage(wire("locations", [{ ...option("locations"), location_type: "region",
      custodian_person_id: null, custodian_name: null }]), context("locations")).items[0]).toMatchObject({ custodianPersonId: null });
  });

  it("returns the real string user anchor and rejects guessed or unsafe user references", () => {
    const page = validateOpeningStartOptionPage(wire("assignees"), context("assignees"));
    expect(page.items[0]).toMatchObject({ id: id(5), userId: "formal-user-5" });
    for (const user of ["", "x".repeat(37), "user name", "用户", "/prefix", "name?query", "name\n"]) {
      expect(() => validateOpeningStartOptionPage(wire("assignees", [{ ...option("assignees"), assignee_user_id: user }]), context("assignees"))).toThrow();
    }
    expect(() => validateOpeningStartOptionPage(wire("assignees", [option("assignees"), {
      ...option("assignees", 6), assignee_user_id: "formal-user-5",
    }]), context("assignees"))).toThrow();
  });

  it.each(stages)("accepts only ascending unique %s pages and a last-returned cursor", async (stage) => {
    const cursor = stage === "assignees" ? "next_after_person_id" : "next_after_id";
    const first = { ...wire(stage), [cursor]: id(5) };
    const valid = validateOpeningStartOptionPage(first, context(stage), null, 1);
    expect(valid.nextAfterId).toBe(id(5));
    for (const value of [
      { ...first, [cursor]: id(6) }, { ...first, [cursor]: id(4) },
      { ...first, items: [] }, wire(stage, [option(stage), option(stage)]),
      wire(stage, [option(stage, 6), option(stage)]),
    ]) expect(() => validateOpeningStartOptionPage(value, context(stage), null, 1)).toThrow();
    expect(() => validateOpeningStartOptionPage(first, context(stage))).toThrow();
    expect(() => validateOpeningStartOptionPage(wire(stage), context(stage), id(5))).toThrow();
    vi.mocked(apiNoReplay).mockResolvedValue(wire(stage, []));
    const end = await loadOpeningStartOptions(context(stage), id(5));
    expect(end.items).toEqual([]);
    expect(end.nextAfterId).toBeNull();
    expect(vi.mocked(apiNoReplay).mock.calls[0][0]).toContain(`${stage === "assignees" ? "after_person_id" : "after_id"}=${id(5)}`);
  });

  it.each(stages)("rejects invalid %s request context before transport", async (stage) => {
    for (const change of [{ actor_person_id: "bad" }, { authorization_version: 0 }, { stage: "__proto__" }, { injected: id(1) }]) {
      await expect(loadOpeningStartOptions({ ...context(stage), ...change } as any)).rejects.toThrow();
    }
    for (const limit of [0, 101, 1.5, true, "2", NaN]) await expect(loadOpeningStartOptions(context(stage), null, limit as any)).rejects.toThrow();
    await expect(loadOpeningStartOptions(context(stage), "../cursor")).rejects.toThrow();
    expect(apiNoReplay).not.toHaveBeenCalled();
  });

  it("requires exact actor keys and selected coordinates and normalizes UUID casing", () => {
    expect(() => openingPreparationContext({ ...actor, token: "private" } as any, "regions")).toThrow();
    expect(() => openingPreparationContext(actor, "assignees", { region_org_id: id(2) })).toThrow();
    expect(() => openingPreparationContext(actor, "regions", { owner_org_id: id(3) })).toThrow();
    const capital = { ...actor, person_id: "AB000000-0000-4000-8000-000000000001" };
    expect(openingPreparationContext(capital, "regions").actor_person_id).toBe(capital.person_id.toLowerCase());
    expect(openingPreparationContextKey(context("locations"))).not.toBe(openingPreparationContextKey(context("assignees")));
  });

  it("fails closed on cross-page repeats, changed contexts and leaked continuation positions", () => {
    const first = validateOpeningStartOptionPage(wire("assignees"), context("assignees"));
    const second = validateOpeningStartOptionPage(wire("assignees", [option("assignees", 6)]), context("assignees"), id(5));
    expect(mergeOpeningStartOptions(first.items, second, context("assignees"), id(5))).toHaveLength(2);
    for (const page of [first, { ...second, context: { ...second.context, authorization_version: 8 } },
      { ...second, nextAfterId: id(7) }, { ...second, items: [{ ...second.items[0], userId: "formal-user-5" }] }]) {
      expect(() => mergeOpeningStartOptions(first.items, page as any, context("assignees"), id(5))).toThrow();
    }
    expect(() => mergeOpeningStartOptions(first.items, second, context("assignees"), id(4))).toThrow();
  });
});
