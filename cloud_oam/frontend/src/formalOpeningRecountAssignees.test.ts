import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import {
  loadOpeningRecountAssignees, openingRecountAssigneeContext, openingRecountAssigneeContextKey,
  validateOpeningRecountAssigneePage, type OpeningRecountAssigneeContext,
} from "./formalOpeningRecountAssignees";

vi.mock("./api", async (original) => ({ ...await original<typeof import("./api")>(), api: vi.fn() }));
const id = (prefix: string) => `${prefix}0000000-0000-4000-8000-000000000001`;
const context: OpeningRecountAssigneeContext = {
  task_id: id("1"), source_round_id: id("2"), scope_id: id("3"), location_id: id("4"),
  region_org_id: id("5"), task_version: 9, actor_person_id: id("6"), actor_authorization_version: 3,
};
const option = { user_id: "formal-user-01", person_id: id("7"), display_name: "测试工程师",
  employee_no: "TEST-001", role_code: "technician" as const };
const page = () => ({ ...context, schema_version: "1.0", items: [option], next_after_person_id: null });
beforeEach(() => vi.mocked(api).mockReset());

describe("task-bound opening recount assignee directory", () => {
  it("uses only an exact read-only task/round/scope path and version", async () => {
    vi.mocked(api).mockResolvedValue(page());
    const result = await loadOpeningRecountAssignees(context);
    expect(api).toHaveBeenCalledExactlyOnceWith(
      `/v1/stocktakes/opening/${context.task_id}/rounds/${context.source_round_id}/scopes/${context.scope_id}/assignees?expected_task_version=9&limit=100`,
      { cache: "no-store", headers: { "Cache-Control": "no-store" } },
    );
    expect(result.items[0].user_id).toBe("formal-user-01");
    expect(result.items[0].person_id).toBe(id("7"));
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.items)).toBe(true);
    expect(Object.isFrozen(result.items[0])).toBe(true);
  });

  it.each(Object.keys(context) as (keyof OpeningRecountAssigneeContext)[])(
    "rejects a stale or cross-object %s anchor", (field) => {
      const changed = { ...page(), [field]: typeof context[field] === "number" ? 99 : id("9") };
      expect(() => validateOpeningRecountAssigneePage(changed, context)).toThrow("不一致");
    },
  );

  it("rejects extra sensitive fields, unsupported schema and invalid personnel contracts", () => {
    for (const changed of [
      { ...page(), acl: [] }, { ...page(), schema_version: "2.0" },
      { ...page(), items: [{ ...option, mobile: "TEST-PHONE-MUST-NOT-BE-EXPOSED" }] },
      { ...page(), items: [{ ...option, role_code: "super_admin" }] },
      { ...page(), items: [{ ...option, user_id: " " }] },
      { ...page(), items: [{ ...option, user_id: "x".repeat(37) }] },
      { ...page(), items: [{ ...option, user_id: "user with space" }] },
      { ...page(), items: [{ ...option, display_name: "unsafe\nname" }] },
      { ...page(), items: [{ ...option, employee_no: "" }] },
      { ...page(), items: [{ ...option, person_id: "00000000-0000-0000-0000-000000000000" }] },
    ]) expect(() => validateOpeningRecountAssigneePage(changed, context)).toThrow("不一致");
  });

  it("accepts only the actual qualifying role, including authorized administrators and managers", () => {
    for (const role_code of ["admin", "provincial_manager", "technician"] as const) {
      expect(validateOpeningRecountAssigneePage({ ...page(), items: [{ ...option, role_code }] }, context).items[0].role_code).toBe(role_code);
    }
  });

  it("never accepts an empty page cursor that could reveal a filtered-out person", async () => {
    vi.mocked(api).mockResolvedValue({ ...page(), items: [], next_after_person_id: null });
    const result = await loadOpeningRecountAssignees(context, id("7"));
    expect(result.items).toEqual([]);
    expect(result.next_after_person_id).toBeNull();
    expect(vi.mocked(api).mock.calls[0][0]).toContain(`after_person_id=${id("7")}`);
    for (const next of [id("8"), id("7"), id("6")]) {
      expect(() => validateOpeningRecountAssigneePage({ ...page(), items: [], next_after_person_id: next }, context, id("7"))).toThrow("不一致");
    }
  });

  it("requires a full-page cursor to equal its last authorized person", () => {
    const value = { ...page(), next_after_person_id: option.person_id };
    expect(validateOpeningRecountAssigneePage(value, context, null, 1).next_after_person_id).toBe(option.person_id);
    expect(() => validateOpeningRecountAssigneePage(value, context)).toThrow("不一致");
    expect(() => validateOpeningRecountAssigneePage({ ...value, next_after_person_id: id("8") }, context, null, 1)).toThrow("不一致");
  });

  it("rejects duplicate users/persons, unsorted rows, oversized pages and backward cursors", () => {
    const second = { ...option, user_id: "formal-user-02", person_id: id("8") };
    for (const changed of [
      { ...page(), items: [option, option] },
      { ...page(), items: [option, { ...second, user_id: option.user_id }] },
      { ...page(), items: [second, option] },
      { ...page(), next_after_person_id: id("6") },
    ]) expect(() => validateOpeningRecountAssigneePage(changed, context)).toThrow("不一致");
    expect(() => validateOpeningRecountAssigneePage({ ...page(), items: [option, second] }, context, null, 1)).toThrow("不一致");
    expect(() => validateOpeningRecountAssigneePage(page(), context, option.person_id)).toThrow("不一致");
  });

  it("validates query anchors before transport without creating write coordinates", async () => {
    for (const changed of [
      { ...context, task_id: "../legacy" }, { ...context, task_version: -1 },
      { ...context, task_version: "9" }, { ...context, actor_authorization_version: 0 },
    ]) await expect(loadOpeningRecountAssignees(changed as OpeningRecountAssigneeContext)).rejects.toThrow("不一致");
    await expect(loadOpeningRecountAssignees(context, "../cursor")).rejects.toThrow("不一致");
    expect(api).not.toHaveBeenCalled();
  });

  it("derives the selector context only from a sealed current task and its exact scope", () => {
    const detail = {
      task_id: context.task_id, region_org_id: context.region_org_id, task_version: context.task_version,
      status: "recount_required", current_round: { round_id: context.source_round_id, status: "submitted" },
      evidence_status: "sealed", allowed_actions: ["open_recount"],
      scopes: [{ scope_id: context.scope_id, location_id: context.location_id }],
    } as Parameters<typeof openingRecountAssigneeContext>[0];
    const actor = { person_id: context.actor_person_id, authorization_version: context.actor_authorization_version };
    expect(openingRecountAssigneeContext(detail, context.scope_id, actor)).toEqual(context);
    for (const changed of [
      { ...detail, status: "counting" }, { ...detail, allowed_actions: [] },
      { ...detail, evidence_status: "counting_hidden" }, { ...detail, current_round: null },
      { ...detail, scopes: [] },
    ]) expect(() => openingRecountAssigneeContext(changed as typeof detail, context.scope_id, actor)).toThrow("不一致");
    expect(openingRecountAssigneeContextKey(context)).not.toBe(openingRecountAssigneeContextKey({ ...context, task_version: 10 }));
  });
});
