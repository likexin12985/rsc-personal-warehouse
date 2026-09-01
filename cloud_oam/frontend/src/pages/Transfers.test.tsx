// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import TransfersPage from "./Transfers";

vi.mock("../api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api")>();
  return { ...original, api: vi.fn() };
});

const user = {
  id: "user-1",
  mobile: "13800000000",
  name: "测试管理员",
  role: "admin",
  province: null,
  require_password_change: false,
};

describe("v0.9 Transfer quarantine", () => {
  beforeEach(() => {
    vi.mocked(api).mockResolvedValue([]);
  });

  it("keeps history readable while omitting every legacy write control", async () => {
    render(<TransfersPage user={user} />);
    expect(await screen.findByText("v0.9 历史原型/只读")).toBeTruthy();
    for (const label of ["新建物料单", "通过申请", "驳回", "确认派发", "确认实物入库", "撤销"]) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });
});
