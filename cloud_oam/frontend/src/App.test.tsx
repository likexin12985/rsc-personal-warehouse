// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { api, ApiError } from "./api";
import App, { Login } from "./App";
import type { AccessContext, AuthenticatedUser } from "./types";

vi.mock("./api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./api")>();
  return { ...original, api: vi.fn() };
});

afterEach(cleanup);

describe("formal PC login", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({ sms_enabled: true, sms_interval_seconds: 60 });
  });

  it("offers SMS only and exposes no password or password-change entry", async () => {
    render(<Login onLogin={vi.fn()} />);
    expect(await screen.findByText("验证码登录")).toBeTruthy();
    expect(screen.queryByText("密码登录")).toBeNull();
    expect(screen.queryByText("修改密码")).toBeNull();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });
});

const authenticatedUser: AuthenticatedUser = {
  person_id: "10000000-0000-4000-8000-000000000001",
  name: "李珂鑫",
  mobile: "13800000000",
  role_codes: ["admin"],
  organization_name: "蔚来总部",
  account_status: "active",
  employment_status: "active",
  access_mode: "active",
  authorization_version: 1,
};

const accessContext: AccessContext = {
  person_id: "10000000-0000-4000-8000-000000000001",
  account_status: "active",
  employment_status: "active",
  authorization_version: 1,
  access_mode: "active",
  role_codes: ["admin"],
  assignments: [],
  permissions: [{ resource: "inventory", action: "read", field_code: "" }],
};

const inventorySummary = {
  schema_version: "1.0",
  projection_status: "ready",
  opening_balance_status: "not_established",
  projected_at: null,
  ledger_cursor: 0,
  scopes: [{ scope_type: "national", scope_id: "*" }],
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

function renderAuthenticatedApp(logout: () => Promise<unknown>): void {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/auth/me") return authenticatedUser;
    if (path === "/access/context") return accessContext;
    if (path === "/v1/inventory/summary") return inventorySummary;
    if (path === "/auth/logout") return logout();
    if (path === "/auth/login-options") return { sms_enabled: true, sms_interval_seconds: 60 };
    throw new Error(`unexpected path: ${path}`);
  });
  render(<MemoryRouter><App /></MemoryRouter>);
}

describe("formal PC logout confirmation", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("does not pretend logout completed on a network failure and succeeds after an explicit retry", async () => {
    const logout = vi.fn()
      .mockRejectedValueOnce(new TypeError("network offline"))
      .mockResolvedValueOnce(undefined);
    renderAuthenticatedApp(logout);

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect((await screen.findByRole("alert")).textContent).toContain("当前会话仍保持登录");
    expect(screen.getByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    expect(screen.queryByText("登录工作台")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByText("登录工作台")).toBeTruthy();
    expect(logout).toHaveBeenCalledTimes(2);
  });

  it("keeps the session UI for a proxy error without the credential-clear proof header", async () => {
    renderAuthenticatedApp(() => Promise.reject(new ApiError(503, "proxy unavailable", {})));

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("当前会话仍保持登录"));
    expect(screen.getByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
  });

  it("clears the UI on a non-2xx response only when the server proves both auth cookies were cleared", async () => {
    renderAuthenticatedApp(() => Promise.reject(new ApiError(401, "session already absent", {
      credentialsCleared: true,
    })));

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    expect(await screen.findByText("登录工作台")).toBeTruthy();
  });

  it("clears another tab's UI from the durable logged-out storage event when broadcast is lost", async () => {
    renderAuthenticatedApp(() => Promise.resolve(undefined));

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    window.dispatchEvent(new StorageEvent("storage", {
      key: "cloud-oam-auth-refresh-sentinel-v1",
      newValue: JSON.stringify({
        v: 1,
        state: "logged_out",
        created_at: Date.now(),
        opaque_attempt_id: `authcoord-${"a".repeat(36)}`,
      }),
    }));

    expect(await screen.findByText("登录工作台")).toBeTruthy();
    expect(screen.queryByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeNull();
  });

  it("clears account A before reloading account B after an authenticated storage event", async () => {
    const accountB: AuthenticatedUser = {
      ...authenticatedUser,
      person_id: "10000000-0000-4000-8000-000000000002",
      name: "张鑫",
      mobile: "13900000000",
      authorization_version: 2,
    };
    const accountBAccess: AccessContext = {
      ...accessContext,
      person_id: "10000000-0000-4000-8000-000000000002",
      authorization_version: 2,
    };
    let meCalls = 0;
    let resolveAccountB: ((value: AuthenticatedUser) => void) | undefined;
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") {
        meCalls += 1;
        if (meCalls === 1) return authenticatedUser;
        return new Promise<AuthenticatedUser>((resolve) => { resolveAccountB = resolve; });
      }
      if (path === "/access/context") return meCalls === 1 ? accessContext : accountBAccess;
      if (path === "/v1/inventory/summary") return inventorySummary;
      if (path === "/auth/login-options") return { sms_enabled: true, sms_interval_seconds: 60 };
      throw new Error(`unexpected path: ${path}`);
    });
    render(<MemoryRouter><App /></MemoryRouter>);

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "cloud-oam-auth-refresh-sentinel-v1",
        newValue: JSON.stringify({
          v: 1,
          state: "authenticated",
          created_at: Date.now(),
          opaque_attempt_id: `authcoord-${"b".repeat(36)}`,
        }),
      }));
    });

    expect(screen.queryByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeNull();
    expect(screen.getByText("正在连接云端系统")).toBeTruthy();
    await act(async () => {
      resolveAccountB?.(accountB);
      await Promise.resolve();
    });

    expect(await screen.findByText("张鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    expect(screen.queryByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeNull();
    expect(meCalls).toBe(2);
  });
});

describe("formal opening stocktake navigation", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it.each([
    ["admin", true, true],
    ["provincial_manager", true, true],
    ["technician", true, false],
    ["admin", false, false],
    ["provincial_manager", false, false],
  ] as const)("preparation visibility for role %s and manage=%s requires both gates", async (role, canManage, visible) => {
    const user: AuthenticatedUser = { ...authenticatedUser, role_codes: [role] };
    const context: AccessContext = { ...accessContext, role_codes: [role], permissions: [
      { resource: "stocktake", action: "read", field_code: "" },
      ...(canManage ? [{ resource: "stocktake", action: "manage", field_code: "" }] : []),
    ] };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return user;
      if (path === "/access/context") return context;
      if (path === "/v1/stocktakes/opening?limit=20") return {
        schema_version: "1.0", items: [], next_after_id: null,
      };
      throw new Error("unexpected test request");
    });
    render(<MemoryRouter initialEntries={["/opening-stocktakes"]}><App /></MemoryRouter>);
    await screen.findByRole("heading", { name: "盘点中心" });
    expect(screen.queryByLabelText("期初盘点准备（只读）") !== null).toBe(visible);
    expect(vi.mocked(api).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("opens the formal stocktake center only with stocktake/read", async () => {
    const stocktakeAccess: AccessContext = {
      ...accessContext,
      permissions: [
        ...accessContext.permissions,
        { resource: "stocktake", action: "read", field_code: "" },
      ],
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return stocktakeAccess;
      if (path === "/v1/stocktakes/opening?limit=20") return {
        schema_version: "1.0",
        items: [],
        next_after_id: null,
      };
      if (path === "/auth/login-options") return { sms_enabled: true, sms_interval_seconds: 60 };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-stocktakes"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "盘点中心" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "盘点中心" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "日常盘点" })).toHaveLength(2);
    expect(await screen.findByText("暂无可见正式盘点任务")).toBeTruthy();
  });

  it("redirects a direct stocktake URL when stocktake/read is absent", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return accessContext;
      if (path === "/v1/inventory/summary") return inventorySummary;
      if (path === "/auth/login-options") return { sms_enabled: true, sms_interval_seconds: 60 };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-stocktakes"]}><App /></MemoryRouter>);

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: "盘点中心" })).toHaveLength(0);
    expect(screen.queryAllByRole("link", { name: "日常盘点" })).toHaveLength(0);
    expect(vi.mocked(api).mock.calls.some(([path]) => String(path).startsWith("/v1/stocktakes/opening"))).toBe(false);
  });
});

describe("formal opening reconciliation navigation", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("opens and lists the workbench only with reconciliation/read", async () => {
    const reconciliationAccess: AccessContext = {
      ...accessContext,
      permissions: [
        ...accessContext.permissions,
        { resource: "reconciliation", action: "read", field_code: "" },
      ],
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return reconciliationAccess;
      if (path === "/v1/reconciliations/opening?limit=20") return {
        schema_version: "1.0",
        items: [],
        next_after_id: null,
      };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-reconciliations"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "控制账对账" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "控制账对账" })).toHaveLength(2);
    expect(await screen.findByText("暂无可见正式期初对账")).toBeTruthy();
    expect(screen.queryByLabelText("期初任务标识")).toBeNull();
  });

  it("shows creation only with reconciliation/create_opening and stocktake/read", async () => {
    const reconciliationAccess: AccessContext = {
      ...accessContext,
      permissions: [
        ...accessContext.permissions,
        { resource: "stocktake", action: "read", field_code: "" },
        { resource: "reconciliation", action: "read", field_code: "" },
        { resource: "reconciliation", action: "create_opening", field_code: "" },
      ],
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return reconciliationAccess;
      if (path === "/v1/reconciliations/opening?limit=20") return {
        schema_version: "1.0",
        items: [],
        next_after_id: null,
      };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-reconciliations"]}><App /></MemoryRouter>);

    expect(await screen.findByLabelText("期初任务标识")).toBeTruthy();
    expect(screen.getByRole("button", { name: "创建独立对账" })).toBeTruthy();
  });

  it("hides creation when task fresh-read permission is absent", async () => {
    const reconciliationAccess: AccessContext = {
      ...accessContext,
      permissions: [
        ...accessContext.permissions,
        { resource: "reconciliation", action: "read", field_code: "" },
        { resource: "reconciliation", action: "create_opening", field_code: "" },
      ],
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return reconciliationAccess;
      if (path === "/v1/reconciliations/opening?limit=20") return {
        schema_version: "1.0",
        items: [],
        next_after_id: null,
      };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-reconciliations"]}><App /></MemoryRouter>);

    expect(await screen.findByText("暂无可见正式期初对账")).toBeTruthy();
    expect(screen.queryByLabelText("期初任务标识")).toBeNull();
  });

  it("redirects a direct URL and sends no reconciliation request without read permission", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return accessContext;
      if (path === "/v1/inventory/summary") return inventorySummary;
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/opening-reconciliations"]}><App /></MemoryRouter>);

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: "控制账对账" })).toHaveLength(0);
    expect(vi.mocked(api).mock.calls.some(([path]) => String(path).startsWith(
      "/v1/reconciliations/opening",
    ))).toBe(false);
  });
});

describe("formal material-request navigation", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
  });

  it("mounts the reviewed demand page only with material_request/read", async () => {
    const materialRequestAccess: AccessContext = {
      ...accessContext,
      permissions: [
        ...accessContext.permissions,
        { resource: "material_request", action: "read", field_code: "" },
        { resource: "material_request", action: "create", field_code: "" },
      ],
    };
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return materialRequestAccess;
      if (path === "/v1/material-requests?limit=50") return {
        schema_version: "1.0",
        items: [],
        next_after_id: null,
      };
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/material-requests"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "需求提报" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "需求提报" })).toHaveLength(2);
    expect(await screen.findByText("暂无可见需求")).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建需求" })).toBeTruthy();
    expect(screen.getByText(/明文草稿仅驻留当前页面内存/)).toBeTruthy();
  });

  it("redirects a direct demand URL and sends no demand request without read permission", async () => {
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/auth/me") return authenticatedUser;
      if (path === "/access/context") return accessContext;
      if (path === "/v1/inventory/summary") return inventorySummary;
      throw new Error(`unexpected path: ${path}`);
    });

    render(<MemoryRouter initialEntries={["/material-requests"]}><App /></MemoryRouter>);

    expect(await screen.findByText("李珂鑫，欢迎进入 RSC 个人仓")).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: "需求提报" })).toHaveLength(0);
    expect(vi.mocked(api).mock.calls.some(([path]) => (
      String(path).startsWith("/v1/material-requests")
    ))).toBe(false);
  });
});
