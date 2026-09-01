import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { validateInventoryAccountPage, validateInventorySummary } from "./formalInventory";

function unopenedSummary(): Record<string, unknown> {
  return {
    schema_version: "1.0",
    projection_status: "ready",
    opening_balance_status: "not_established",
    projected_at: null,
    ledger_cursor: 0,
    scopes: [{
      scope_type: "person",
      scope_id: "10000000-0000-4000-8000-000000000001",
    }],
    quantity_status: "opening_not_established",
    physical_in_stock_qty: null,
    available_qty: null,
    reserved_qty: null,
    committed_qty: null,
    frozen_qty: null,
    physical_in_transit_qty: null,
    expected_supply_qty: null,
    expected_supply_status: "not_available",
  };
}

function accountPage(established = true): Record<string, unknown> {
  return {
    schema_version: "1.0",
    projection_status: "ready",
    opening_balance_status: established ? "established" : "not_established",
    projected_at: "2026-08-31T08:00:00Z",
    ledger_cursor: 7,
    next_after_id: null,
    items: [{
      stock_account_id: "20000000-0000-4000-8000-000000000001",
      owner_org_id: "20000000-0000-4000-8000-000000000002",
      owner_org_code: "JS",
      owner_org_name: "江苏区域公司",
      location_owner_org_id: "20000000-0000-4000-8000-000000000002",
      location_owner_org_code: "JS",
      location_owner_org_name: "江苏区域公司",
      location_id: "20000000-0000-4000-8000-000000000003",
      location_code: "JS-PERSONAL-001",
      location_name: "工程师个人仓",
      location_type: "personal",
      location_parent_id: "20000000-0000-4000-8000-000000000004",
      custodian_person_id: "20000000-0000-4000-8000-000000000005",
      custodian_person_name: "工程师甲",
      material_id: "20000000-0000-4000-8000-000000000006",
      sku_code: "SKU-001",
      material_name: "测试物料",
      base_unit: "个",
      tracking_mode: "none",
      condition_code: "new",
      availability_bucket: "available",
      lot_id: null,
      lot_no: null,
      quantity_status: established ? "available" : "opening_not_established",
      quantity: established ? "12.000" : null,
      balance_version: 1,
      ledger_cursor: 7,
    }],
  };
}

describe("formal inventory summary contract", () => {
  it("accepts an explicit unopened scope without manufacturing zero inventory", () => {
    const payload = unopenedSummary();
    const result = validateInventorySummary(payload);
    expect(result.opening_balance_status).toBe("not_established");
    expect(result.available_qty).toBeNull();
  });

  it("accepts a reviewed empty opening scope without inventing a ledger movement", () => {
    const payload = unopenedSummary();
    payload.opening_balance_status = "established";
    payload.quantity_status = "material_filter_required";

    const result = validateInventorySummary(payload);

    expect(result.opening_balance_status).toBe("established");
    expect(result.ledger_cursor).toBe(0);
    expect(result.projected_at).toBeNull();
    expect(result.available_qty).toBeNull();
  });

  it("rejects quantity leakage before opening is established", () => {
    const payload = unopenedSummary();
    payload.available_qty = "0.000";
    expect(() => validateInventorySummary(payload)).toThrowError(ApiError);
    expect(() => validateInventorySummary(payload)).toThrow(/不得返回库存数量/);
  });

  it("rejects any unscoped available summary and cross-SKU total", () => {
    const payload = unopenedSummary();
    payload.opening_balance_status = "established";
    payload.projected_at = "2026-08-30T12:00:00Z";
    payload.ledger_cursor = 1;
    payload.quantity_status = "available";
    for (const field of [
      "physical_in_stock_qty",
      "available_qty",
      "reserved_qty",
      "committed_qty",
      "frozen_qty",
      "physical_in_transit_qty",
    ]) payload[field] = "1.000";
    expect(() => validateInventorySummary(payload)).toThrow(/未知 quantity_status/);

    payload.quantity_status = "material_filter_required";
    expect(() => validateInventorySummary(payload)).toThrow(/不得汇总不同 SKU/);
  });

  it("fails closed on missing cursor, bad scope or future schema", () => {
    const missing = unopenedSummary();
    delete missing.ledger_cursor;
    expect(() => validateInventorySummary(missing)).toThrow(/缺少字段 ledger_cursor/);

    const badScope = unopenedSummary();
    badScope.scopes = [{ scope_type: "person", scope_id: "not-a-uuid" }];
    expect(() => validateInventorySummary(badScope)).toThrow(/范围与范围类型不一致/);

    const future = unopenedSummary();
    future.schema_version = "2.0";
    expect(() => validateInventorySummary(future)).toThrow(/版本不受支持/);
  });
});

describe("formal inventory account page contract", () => {
  it("accepts an established exact-decimal account page", () => {
    const result = validateInventoryAccountPage(accountPage());
    expect(result.opening_balance_status).toBe("established");
    expect(result.items[0].quantity).toBe("12.000");
  });

  it("accepts a quantity-blind page while any authorized scope is unopened", () => {
    const result = validateInventoryAccountPage(accountPage(false));
    expect(result.items[0].quantity_status).toBe("opening_not_established");
    expect(result.items[0].quantity).toBeNull();
  });

  it("rejects partial quantity leakage on a globally unopened page", () => {
    const payload = accountPage(false);
    const row = (payload.items as Array<Record<string, unknown>>)[0];
    row.quantity_status = "available";
    row.quantity = "12.000";
    expect(() => validateInventoryAccountPage(payload)).toThrow(/数量必须隐藏/);
  });

  it("rejects cursor, decimal and lot-policy contradictions", () => {
    const ahead = accountPage();
    (ahead.items as Array<Record<string, unknown>>)[0].ledger_cursor = 8;
    expect(() => validateInventoryAccountPage(ahead)).toThrow(/账户游标超过/);

    const decimal = accountPage();
    (decimal.items as Array<Record<string, unknown>>)[0].quantity = "12.0";
    expect(() => validateInventoryAccountPage(decimal)).toThrow(/数量状态/);

    const lot = accountPage();
    (lot.items as Array<Record<string, unknown>>)[0].tracking_mode = "lot";
    expect(() => validateInventoryAccountPage(lot)).toThrow(/批次字段/);
  });
});
