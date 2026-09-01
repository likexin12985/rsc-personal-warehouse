import { describe, expect, it } from "vitest";

import { formalMaterialCatalogQuery, validateFormalMaterialCatalogPage } from "./formalMaterialCatalog";

const MATERIAL_ID = "50000000-0000-4000-8000-000000000001";

function page() {
  return {
    schema_version: "1.0",
    items: [{
      material_id: MATERIAL_ID,
      sku_code: "SKU-A",
      name: "交流接触器",
      specification: "32A",
      base_unit: "件",
      tracking_mode: "serial",
      quantity_scale: 0,
      allow_fraction: false,
      source_updated_at: "2026-09-01T07:00:00+08:00",
    }],
    next_after_id: "50000000-0000-4000-8000-000000000002",
  };
}

describe("formal material catalog contract", () => {
  it("accepts only the permission-minimal searchable picker projection", () => {
    const result = validateFormalMaterialCatalogPage(page());
    expect(result.items[0]).toMatchObject({
      material_id: MATERIAL_ID,
      sku_code: "SKU-A",
      name: "交流接触器",
      specification: "32A",
      base_unit: "件",
      tracking_mode: "serial",
    });
    expect(formalMaterialCatalogQuery("SKU A")).toBe("SKU A");
  });

  it("fails closed on schema drift, duplicate anchors and ambiguous queries", () => {
    expect(() => validateFormalMaterialCatalogPage({ ...page(), legacy: true })).toThrow();
    const duplicate = page();
    duplicate.items.push({ ...duplicate.items[0] });
    expect(() => validateFormalMaterialCatalogPage(duplicate)).toThrow();
    expect(() => formalMaterialCatalogQuery(" SKU-A")).toThrow();
    expect(() => formalMaterialCatalogQuery("SKU-A\n")).toThrow();
  });
});
