// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiNoReplay } from "../api";
import OpeningStartPreparationPanel from "./OpeningStartPreparationPanel";

vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), apiNoReplay: vi.fn() }));
const id = (number: number) => `10000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const actor = { person_id: id(1), authorization_version: 7 };
const labels = ["任务区域", "资产所有组织", "实物库存位置", "初盘执行人员"];
function wire(path: string, identity = actor): any {
  const [base, search] = path.split("?");
  const stage = base.split("/").at(-1);
  const query = new URLSearchParams(search);
  const secondRegion = query.get("region_org_id") === id(12);
  const result: any = { schema_version: "1.0", actor_person_id: identity.person_id,
    authorization_version: identity.authorization_version, start_ready: false,
    control_evidence_status: "control_evidence_not_evaluated", items: [],
    [stage === "assignees" ? "next_after_person_id" : "next_after_id"]: null };
  for (const key of ["region_org_id", "owner_org_id", "location_id"]) if (query.has(key)) result[key] = query.get(key);
  if (stage === "regions") result.items = [
    { region_org_id: id(2), code: "REGION-1", name: "任务区域一", province_code: "330000" },
    { region_org_id: id(12), code: "REGION-2", name: "任务区域二", province_code: "320000" },
  ];
  if (stage === "asset-owners") result.items = [{ owner_org_id: id(secondRegion ? 13 : 3), code: "OWNER",
    name: secondRegion ? "资产所有区域二" : "资产所有区域一" }];
  if (stage === "locations") result.items = [{ location_id: id(4), code: "PERSONAL", name: "工程师个人仓",
    location_type: "personal", physical_owner_org_id: id(2), physical_owner_name: "物理归属区域",
    custodian_person_id: id(7), custodian_name: "保管工程师甲" }];
  if (stage === "assignees") result.items = [{ person_id: id(7), assignee_user_id: "actual-user-7", name: "保管工程师甲" }];
  return result;
}
function deferred() {
  let resolve!: (value: any) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<any>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function select(label: string, value: string) {
  const field = await screen.findByRole("combobox", { name: label }) as HTMLSelectElement;
  await waitFor(() => expect(field.disabled).toBe(false));
  fireEvent.change(field, { target: { value } });
}
async function expand() {
  fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
  await screen.findByRole("option", { name: "任务区域一 · REGION-1" });
}
async function selectAll() {
  await expand();
  await select(labels[0], id(2));
  await select(labels[1], id(3));
  await select(labels[2], id(4));
  await select(labels[3], id(7));
}
beforeEach(() => {
  vi.mocked(apiNoReplay).mockReset();
  vi.mocked(apiNoReplay).mockImplementation(async (path) => wire(path));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("read-only opening preparation panel", () => {
  it("starts collapsed, reads four layers only after selection, and cannot start a task", async () => {
    const storeWrite = vi.spyOn(Storage.prototype, "setItem");
    const storeRemove = vi.spyOn(Storage.prototype, "removeItem");
    const { container } = render(<OpeningStartPreparationPanel actor={actor} />);
    expect(apiNoReplay).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "展开准备目录" }).getAttribute("aria-expanded")).toBe("false");
    await selectAll();
    expect(apiNoReplay).toHaveBeenCalledTimes(4);
    expect(screen.getByText(/仅核验范围与人员，尚未评估省级控制库存，不能启动/)).toBeTruthy();
    expect(screen.getByText("库位物理归属")).toBeTruthy();
    expect(screen.getByText("物理归属区域")).toBeTruthy();
    expect(screen.getByText("保管责任人")).toBeTruthy();
    expect(screen.getByText("本次核验人员")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /启动|创建|提交|过账|复制/ })).toBeNull();
    expect(container.querySelector("input, textarea, form")).toBeNull();
    for (const [path, init] of vi.mocked(apiNoReplay).mock.calls) {
      expect(path).toMatch(/^\/v1\/stocktakes\/opening\/start-options\//);
      expect(init?.method).toBe("GET");
      expect(init?.body).toBeUndefined();
    }
    expect(storeWrite).not.toHaveBeenCalled();
    expect(storeRemove).not.toHaveBeenCalled();
  });

  it.each(labels.slice(0, 3))("refreshing %s immediately clears its choice and all descendants", async (label) => {
    render(<OpeningStartPreparationPanel actor={actor} />);
    await selectAll();
    const index = labels.indexOf(label);
    fireEvent.click(screen.getByRole("button", { name: `刷新${label}` }));
    for (const child of labels.slice(index + 1)) expect(screen.queryByRole("combobox", { name: child })).toBeNull();
    await waitFor(() => expect((screen.getByRole("combobox", { name: label }) as HTMLSelectElement).disabled).toBe(false));
    expect((screen.getByRole("combobox", { name: label }) as HTMLSelectElement).value).toBe("");
    expect(screen.queryByText("本次核验人员")).toBeNull();
  });

  it("changing an upstream selection discards an in-flight old page and its failure", async () => {
    const old = deferred();
    let oldPath = "";
    vi.mocked(apiNoReplay).mockImplementation(async (path) => {
      if (path.includes("asset-owners") && path.includes(id(2))) { oldPath = path; return old.promise; }
      return wire(path);
    });
    render(<OpeningStartPreparationPanel actor={actor} />);
    await expand();
    await select(labels[0], id(2));
    await waitFor(() => expect(oldPath).not.toBe(""));
    await select(labels[0], id(12));
    await screen.findByRole("option", { name: "资产所有区域二 · OWNER" });
    await select(labels[1], id(13));
    await act(async () => { old.reject(new Error("PRIVATE OLD FAILURE")); });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("option", { name: "资产所有区域一 · OWNER" })).toBeNull();
    expect((screen.getByRole("combobox", { name: labels[1] }) as HTMLSelectElement).value).toBe(id(13));
  });

  it("ignores a successful late upstream response after changing regions", async () => {
    const old = deferred();
    let oldPath = "";
    vi.mocked(apiNoReplay).mockImplementation(async (path) => {
      if (path.includes("asset-owners") && path.includes(id(2))) { oldPath = path; return old.promise; }
      return wire(path);
    });
    render(<OpeningStartPreparationPanel actor={actor} />);
    await expand();
    await select(labels[0], id(2));
    await select(labels[0], id(12));
    await screen.findByRole("option", { name: "资产所有区域二 · OWNER" });
    await act(async () => { old.resolve(wire(oldPath)); });
    expect(screen.queryByRole("option", { name: "资产所有区域一 · OWNER" })).toBeNull();
  });

  it("an upstream read failure clears descendants and never echoes raw server details", async () => {
    render(<OpeningStartPreparationPanel actor={actor} />);
    await selectAll();
    vi.mocked(apiNoReplay).mockRejectedValueOnce(new Error("PRIVATE SECRET FROM SERVER"));
    fireEvent.click(screen.getByRole("button", { name: "刷新资产所有组织" }));
    expect(screen.queryByRole("combobox", { name: labels[2] })).toBeNull();
    expect(screen.queryByRole("combobox", { name: labels[3] })).toBeNull();
    expect((await screen.findByRole("alert")).textContent).toContain("旧选项和后续选择已清除");
    expect(screen.queryByText(/PRIVATE/)).toBeNull();
    expect(screen.queryByRole("option", { name: "资产所有区域一 · OWNER" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "刷新资产所有组织" }));
    await screen.findByRole("option", { name: "资产所有区域一 · OWNER" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("combobox", { name: labels[2] })).toBeNull();
  });

  it.each(["person", "version"])("switching actor %s closes the panel and isolates late identity data", async (change) => {
    const old = deferred();
    const nextActor = change === "person" ? { ...actor, person_id: id(99) } : { ...actor, authorization_version: 8 };
    let oldPath = "";
    vi.mocked(apiNoReplay).mockImplementationOnce((path) => { oldPath = path; return old.promise; });
    const page = render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    page.rerender(<OpeningStartPreparationPanel actor={nextActor} />);
    expect(screen.queryByRole("combobox")).toBeNull();
    vi.mocked(apiNoReplay).mockImplementation(async (path) => wire(path, nextActor));
    await expand();
    const outdated = wire(oldPath);
    outdated.items[0].name = "旧账号私有区域";
    await act(async () => { old.resolve(outdated); });
    expect(screen.queryByRole("option", { name: /旧账号/ })).toBeNull();
    expect(apiNoReplay).toHaveBeenCalledTimes(2);
  });

  it("rejects a response whose actor changed, even if its selected coordinates are unchanged", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (path) => wire(path, { ...actor, person_id: id(99) }));
    render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("option", { name: "任务区域一 · REGION-1" })).toBeNull();
  });

  it("actual transport sends one GET on 401 without refresh POST, replay or storage writes", async () => {
    const actual = await vi.importActual<typeof import("../api")>("../api");
    vi.mocked(apiNoReplay).mockImplementation(actual.apiNoReplay);
    vi.stubGlobal("BroadcastChannel", undefined);
    const transport = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { message: "PRIVATE AUTH DETAIL" } }), {
      status: 401, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", transport);
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const removed = vi.spyOn(Storage.prototype, "removeItem");
    render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(transport).toHaveBeenCalledTimes(1);
    expect(transport.mock.calls[0][0]).toBe("/api/v1/stocktakes/opening/start-options/regions?limit=50");
    expect(transport.mock.calls[0][1].method).toBe("GET");
    expect(storage).not.toHaveBeenCalled();
    expect(removed).not.toHaveBeenCalled();
    expect(screen.queryByText(/PRIVATE AUTH/)).toBeNull();
  });

  it("collapsing or unmounting invalidates pending reads without storage or transport side effects", async () => {
    const pending = deferred();
    let request = "";
    vi.mocked(apiNoReplay).mockImplementationOnce((path) => { request = path; return pending.promise; });
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const removed = vi.spyOn(Storage.prototype, "removeItem");
    const page = render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    fireEvent.click(screen.getByRole("button", { name: "收起准备目录" }));
    page.unmount();
    await act(async () => { pending.resolve(wire(request)); });
    expect(page.container.innerHTML).toBe("");
    expect(apiNoReplay).toHaveBeenCalledTimes(1);
    expect(storage).not.toHaveBeenCalled();
    expect(removed).not.toHaveBeenCalled();
  });

  it("does not show preparation for an absent or malformed formal identity", () => {
    const page = render(<OpeningStartPreparationPanel actor={{ ...actor, person_id: "bad" }} />);
    expect(page.container.innerHTML).toBe("");
    page.rerender(<OpeningStartPreparationPanel actor={{ ...actor, authorization_version: 0 }} />);
    expect(page.container.innerHTML).toBe("");
    expect(apiNoReplay).not.toHaveBeenCalled();
  });

  it("empty directories offer explicit administrator configuration, never automatic people", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (path) => ({ ...wire(path), items: [] }));
    render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    expect(await screen.findByText(/暂无可选任务区域.*管理员.*系统不会自动指派人员/)).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: labels[1] })).toBeNull();
  });

  it("prevents double-click refresh and clears the old page until the new one is verified", async () => {
    render(<OpeningStartPreparationPanel actor={actor} />);
    await expand();
    const pending = deferred();
    let path = "";
    vi.mocked(apiNoReplay).mockImplementationOnce((value) => { path = value; return pending.promise; });
    const button = screen.getByRole("button", { name: "刷新任务区域" });
    act(() => { fireEvent.click(button); fireEvent.click(button); });
    expect(apiNoReplay).toHaveBeenCalledTimes(2);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("option", { name: "任务区域一 · REGION-1" })).toBeNull();
    await act(async () => { pending.resolve(wire(path)); });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(["repeated-person", "repeated-user", "leaked-cursor"])("paging %s clears the entire directory and selection", async (failure) => {
    let pageNumber = 0;
    vi.mocked(apiNoReplay).mockImplementation(async (path) => {
      const value = wire(path);
      if (path.includes("/locations?")) { value.items[0].location_type = "region"; }
      if (path.includes("/assignees?")) {
        pageNumber += 1;
        value.items = pageNumber === 1 ? Array.from({ length: 50 }, (_, index) => ({
          person_id: id(100 + index), assignee_user_id: `user-${index}`, name: `可选人员${index}`,
        })) : [{ person_id: id(failure === "repeated-person" ? 149 : 150),
          assignee_user_id: failure === "repeated-user" ? "user-0" : "new-user", name: "续页人员" }];
        value.next_after_person_id = pageNumber === 1 ? id(149) : failure === "leaked-cursor" ? id(999) : null;
      }
      return value;
    });
    render(<OpeningStartPreparationPanel actor={actor} />);
    await expand();
    await select(labels[0], id(2)); await select(labels[1], id(3)); await select(labels[2], id(4));
    await select(labels[3], id(100));
    fireEvent.click(screen.getByRole("button", { name: "加载更多初盘执行人员" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("option", { name: "可选人员0" })).toBeNull();
    expect(screen.queryByText("本次核验人员")).toBeNull();
    expect(screen.queryByRole("button", { name: "加载更多初盘执行人员" })).toBeNull();
  });

  it("successful continuation merges pages once and clears the previous selection", async () => {
    let pageNumber = 0;
    const pending = deferred();
    vi.mocked(apiNoReplay).mockImplementation(async (path) => {
      const value = wire(path);
      if (path.includes("/regions?")) {
        pageNumber += 1;
        if (pageNumber === 2) return pending.promise;
        value.items = Array.from({ length: 50 }, (_, index) => ({ region_org_id: id(100 + index),
          code: `REGION-${index}`, name: `范围${index}`, province_code: null }));
        value.next_after_id = id(149);
      }
      return value;
    });
    render(<OpeningStartPreparationPanel actor={actor} />);
    fireEvent.click(screen.getByRole("button", { name: "展开准备目录" }));
    await select(labels[0], id(100));
    await screen.findByRole("combobox", { name: labels[1] });
    const more = screen.getByRole("button", { name: "加载更多任务区域" });
    act(() => { fireEvent.click(more); fireEvent.click(more); });
    expect(pageNumber).toBe(2);
    expect(screen.queryByRole("combobox", { name: labels[1] })).toBeNull();
    const firstPath = vi.mocked(apiNoReplay).mock.calls[0][0];
    await act(async () => { pending.resolve({ ...wire(firstPath), items: [{ region_org_id: id(150),
      code: "NEXT", name: "续页区域", province_code: null }], next_after_id: null }); });
    expect(await screen.findByRole("option", { name: "续页区域 · NEXT" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "范围0 · REGION-0" })).toBeTruthy();
    expect((screen.getByRole("combobox", { name: labels[0] }) as HTMLSelectElement).value).toBe("");
    expect(screen.queryByRole("button", { name: "加载更多任务区域" })).toBeNull();
  });

  it("rejects a purported personal executor who is not the displayed custodian", async () => {
    vi.mocked(apiNoReplay).mockImplementation(async (path) => {
      const value = wire(path);
      if (path.includes("/assignees?")) value.items[0].person_id = id(17);
      return value;
    });
    render(<OpeningStartPreparationPanel actor={actor} />);
    await expand(); await select(labels[0], id(2)); await select(labels[1], id(3)); await select(labels[2], id(4));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("option", { name: "保管工程师甲" })).toBeNull();
  });
});
