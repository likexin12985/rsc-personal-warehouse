// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, mutationHeaders } from "../api";
import ProvincialManagersPage from "./ProvincialManagers";

vi.mock("../api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../api")>();
  return {
    ...original,
    api: vi.fn(),
    mutationHeaders: vi.fn(() => ({
      headers: {
        "Idempotency-Key": "provincial-manager-grant-test-request",
        "X-Request-ID": "web-test-request",
      },
    })),
  };
});

const region = {
  organization_id: "11111111-1111-4111-8111-111111111111",
  organization_code: "REG-JS",
  organization_name: "江苏区域公司",
  province_code: "320000",
};

const candidate = {
  person_id: "22222222-2222-4222-8222-222222222222",
  person_name: "测试工程师",
  employee_no: "E10001",
  organization_id: "33333333-3333-4333-8333-333333333333",
  organization_code: "ORG-NJ",
  organization_name: "南京服务组织",
  authorization_version: 7,
};

describe("provincial manager administration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api).mockImplementation(async (path, init) => {
      if (path === "/access/provincial-managers/regions") return [region];
      if (path.includes("/candidates?")) return [candidate];
      if (path.includes("/assignments?") && !init?.method) return [];
      if (path === "/access/provincial-managers/assignments" && init?.method === "POST") {
        return {
          assignment_id: "44444444-4444-4444-8444-444444444444",
          person_id: candidate.person_id,
          organization_id: region.organization_id,
          role_code: "provincial_manager",
          scope_type: "organization",
          status: "active",
          valid_from: "2026-08-30T08:00:00Z",
          valid_to: null,
          authorization_version: 8,
          audit_event_id: "55555555-5555-4555-8555-555555555555",
          state_transition_event_id: "66666666-6666-4666-8666-666666666666",
          replayed: false,
        };
      }
      throw new Error(`unexpected API call: ${path}`);
    });
  });

  it("starts empty and grants an exact engineer to the selected region", async () => {
    render(<ProvincialManagersPage />);

    expect(await screen.findByText(/测试工程师/)).toBeTruthy();
    expect(screen.getByText("当前未配置负责人")).toBeTruthy();
    expect(document.body.textContent).not.toContain("13800000000");
    expect(document.body.textContent).not.toContain("internal-user-id");

    fireEvent.change(screen.getByPlaceholderText("请填写任命依据或职责范围"), {
      target: { value: "江苏省背包物资职责确认" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认任命" }));

    await waitFor(() => {
      expect(api).toHaveBeenCalledWith(
        "/access/provincial-managers/assignments",
        expect.objectContaining({
          method: "POST",
          headers: {
            "Idempotency-Key": "provincial-manager-grant-test-request",
            "X-Request-ID": "web-test-request",
          },
          body: JSON.stringify({
            person_id: candidate.person_id,
            organization_id: region.organization_id,
            expected_authorization_version: 7,
            valid_to: null,
            reason: "江苏省背包物资职责确认",
          }),
        }),
      );
    });
    expect(mutationHeaders).toHaveBeenCalledWith("provincial-manager-grant");
    expect(await screen.findByText(/审计证据已写入/)).toBeTruthy();
  });
});
