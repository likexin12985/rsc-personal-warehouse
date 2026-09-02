import { describe, expect, it } from "vitest";

import {
  validateMaterialRequestWorkOrderOptionDetail,
  validateMaterialRequestWorkOrderOptionPage,
  validateMaterialRequestWorkOrderOptionQuery,
} from "./formalMaterialRequestOptions";

const PERSON_ID = "30000000-0000-4000-8000-000000000001";
const OTHER_PERSON_ID = "30000000-0000-4000-8000-000000000002";
const WORK_ORDER_ID = "45000000-0000-4000-8000-000000000001";
const OTHER_WORK_ORDER_ID = "45000000-0000-4000-8000-000000000002";
const IDENTITY = Object.freeze({ person_id: PERSON_ID, authorization_version: 7 });

function item(
  workOrderId = WORK_ORDER_ID,
  workOrderNo = "WO-20260901-0001",
) {
  return {
    work_order_id: workOrderId,
    work_order_no: workOrderNo,
    status: "active",
    source_system_code: "starcharge_oam",
    source_external_id: `external-${workOrderNo}`,
    source_version: "v17",
    source_updated_at: "2026-09-01T07:30:00+08:00",
    synced_at: "2026-09-01T07:31:00+08:00",
    freshness_status: "fresh",
  };
}

function page() {
  return {
    schema_version: "1.0",
    person_id: PERSON_ID,
    authorization_version: 7,
    items: [item()],
    next_after_id: OTHER_WORK_ORDER_ID,
  };
}

function detail() {
  return {
    schema_version: "1.0",
    person_id: PERSON_ID,
    authorization_version: 7,
    item: item(),
  };
}

describe("formal material-request work-order option contract", () => {
  it("accepts only the identity-bound, permission-minimal list and detail projections", () => {
    const result = validateMaterialRequestWorkOrderOptionPage(page(), IDENTITY);
    expect(result).toEqual(page());
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.items)).toBe(true);
    expect(Object.isFrozen(result.items[0])).toBe(true);

    expect(validateMaterialRequestWorkOrderOptionDetail(detail(), {
      ...IDENTITY,
      work_order_id: WORK_ORDER_ID,
    }).item).toEqual(item());
    expect(validateMaterialRequestWorkOrderOptionQuery("WO-20260901")).toBe("WO-20260901");
    expect(validateMaterialRequestWorkOrderOptionQuery("")).toBe("");
  });

  it("fails closed on schema, shape, identifier, status and timestamp drift", () => {
    expect(() => validateMaterialRequestWorkOrderOptionPage({ ...page(), legacy: true }, IDENTITY))
      .toThrow(/精确包含正式字段/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({ ...page(), schema_version: "0.9" }, IDENTITY))
      .toThrow(/版本不受支持/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), work_order_id: "00000000-0000-0000-0000-000000000000" }],
    }, IDENTITY)).toThrow(/work_order_id无效/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), status: "closed" }],
    }, IDENTITY)).toThrow(/状态无效/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), source_updated_at: "2026-09-01T07:30:00" }],
    }, IDENTITY)).toThrow(/包含时区/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{
        ...item(),
        source_updated_at: "2026-09-01T07:32:00+08:00",
        synced_at: "2026-09-01T07:31:00+08:00",
      }],
    }, IDENTITY)).toThrow(/不能晚于本次同步时间/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), freshness_status: "stale" }],
    }, IDENTITY)).toThrow(/不是 fresh/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), source_system_code: "oam" }],
    }, IDENTITY)).toThrow(/不是 starcharge_oam/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [{ ...item(), address: "不得接收" }],
    }, IDENTITY)).toThrow(/精确包含正式字段/);
  });

  it("fails closed on duplicate anchors, identity drift and inexact detail recovery", () => {
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      items: [item(), item(OTHER_WORK_ORDER_ID)],
    }, IDENTITY)).toThrow(/重复记录/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      person_id: OTHER_PERSON_ID,
    }, IDENTITY)).toThrow(/身份或授权版本/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      authorization_version: 8,
    }, IDENTITY)).toThrow(/身份或授权版本/);
    expect(() => validateMaterialRequestWorkOrderOptionPage({
      ...page(),
      next_after_id: WORK_ORDER_ID,
    }, IDENTITY)).toThrow(/游标指向当前页记录/);
    expect(() => validateMaterialRequestWorkOrderOptionDetail(detail(), {
      ...IDENTITY,
      work_order_id: OTHER_WORK_ORDER_ID,
    })).toThrow(/草稿引用不一致/);
  });

  it("rejects ambiguous search input before transport", () => {
    expect(() => validateMaterialRequestWorkOrderOptionQuery(" WO-A")).toThrow(/检索词无效/);
    expect(() => validateMaterialRequestWorkOrderOptionQuery("WO-A\n")).toThrow(/检索词无效/);
    expect(() => validateMaterialRequestWorkOrderOptionQuery("W".repeat(101))).toThrow(/检索词无效/);
  });
});
