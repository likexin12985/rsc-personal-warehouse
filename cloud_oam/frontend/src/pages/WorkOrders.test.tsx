import { describe, expect, it } from "vitest";

import { formatOamDate, materialOperationLabel, normalizeWorkOrderDetail, workItemText } from "./WorkOrders";


describe("workItemText", () => {
  it("labels every formal material operation", () => {
    expect(materialOperationLabel("consume")).toBe("消耗");
    expect(materialOperationLabel("reverse")).toBe("冲销");
    expect(materialOperationLabel("future_operation")).toBe("future_operation");
  });
  it("keeps OAM yes/no semantics explicit", () => {
    expect(workItemText({ columnType: "1", result: "Y" })).toBe("是");
    expect(workItemText({ columnType: "1", result: "N" })).toBe("否");
  });

  it("renders multi-select arrays without losing choices", () => {
    expect(workItemText({ columnType: "4", result: ["主板死机", "红灯闪烁"] })).toBe(
      "主板死机、红灯闪烁",
    );
  });

  it("distinguishes empty results from false values", () => {
    expect(workItemText({ columnType: "2", result: "" })).toBe("未填写");
    expect(workItemText({ columnType: "7", result: 0 })).toBe("0");
  });
});

describe("formatOamDate", () => {
  it("keeps invalid OAM date text visible for diagnosis", () => {
    expect(formatOamDate("unknown-time")).toBe("unknown-time");
  });
});

describe("normalizeWorkOrderDetail", () => {
  it("keeps older snapshots usable when newly added arrays are absent", () => {
    const normalized = normalizeWorkOrderDetail({
      summary: { code: "WT-OLD-001" },
      counts: {},
      targets: [],
      workItems: [],
      quotations: [],
      timeline: [],
    } as never);
    expect(normalized.fees).toEqual([]);
    expect(normalized.relatedOrders).toEqual([]);
    expect(normalized.materials).toEqual([]);
  });
});
