// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill } from "./ui";


describe("StatusPill", () => {
  it.each([
    ["pending_approval", "待审批"],
    ["dispatched", "运输中"],
    ["received", "已入库"],
    ["occupied", "占用中"],
    ["consumed", "已消耗"],
    ["recovered", "已回收"],
  ])("renders %s as %s", (status, label) => {
    render(<StatusPill status={status} />);
    expect(screen.getByText(label).className).toContain(`status-${status}`);
  });

  it("keeps unknown statuses visible for diagnosis", () => {
    render(<StatusPill status="unexpected_state" />);
    expect(screen.getByText("unexpected_state").className).toContain(
      "status-unexpected_state",
    );
  });
});
