// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PublicKnowledge from "./PublicKnowledge";
import type { KnowledgeCatalog, KnowledgeItem } from "./knowledge";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const item: KnowledgeItem = { code: "TEST001", name: "测试主板", category: "板卡", model: "AC TEST", note: "仅测试夹具", sourceSheet: "测试页", sourceRow: 2 };
const catalog: KnowledgeCatalog = { schemaVersion: 1, status: "ready", sourceUrl: "https://wbenergy.feishu.cn/sheets/ZVnis9SUzhfiU9t9EK4csLvEnNg?sheet=1eOLBU", verifiedAt: "2026-01-01T00:00:00Z", sourceSha256: "a".repeat(64), items: [item, { ...item, code: "TEST002", name: "测试枪线", category: "枪线", sourceRow: 3, model: "" }] };

it("opens the public homepage without authentication, session reads or network calls", () => {
  const fetch = vi.fn(() => { throw new Error("public page must not fetch private data"); });
  vi.stubGlobal("fetch", fetch);
  const storage = vi.spyOn(Storage.prototype, "getItem");
  render(<PublicKnowledge data={{ ...catalog, status: "pending", verifiedAt: null, sourceSha256: null, items: [] }} />);
  expect(screen.getByText("资料待更新")).toBeTruthy();
  expect(screen.getByRole("link", { name: "星星后台管理" }).getAttribute("href")).toBe("/xx");
  expect(screen.getByText("豫ICP备2026043964号-1")).toBeTruthy();
  expect(screen.getByRole("link", { name: "工业和信息化部备案管理系统" }).getAttribute("href")).toBe("https://beian.miit.gov.cn/");
  expect(screen.queryByText(/登录|验证码|个人仓/)).toBeNull();
  expect(document.querySelector('input[type="password"]')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
  expect(storage).not.toHaveBeenCalled();
  expect(screen.queryByText("TEST001")).toBeNull();
});

it("combines case-insensitive keywords and exact category without inventing fitment", () => {
  render(<PublicKnowledge data={catalog} />);
  expect(screen.getByText("未注明")).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "test001 主板" } });
  expect(screen.getByText("测试主板")).toBeTruthy();
  expect(screen.queryByText("测试枪线")).toBeNull();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "枪线" } });
  expect(screen.getByText("未找到相关备件")).toBeTruthy();
  fireEvent.click(screen.getByText("清除筛选"));
  expect(screen.getByText("测试枪线")).toBeTruthy();
  expect(screen.getByText("来源：测试页 · 第 3 行")).toBeTruthy();
});

it("paginates large catalogs and resets to page one when filters change", () => {
  const data = { ...catalog, items: Array.from({ length: 23 }, (_, i) => ({ ...item, name: `样本 ${i}`, code: `TEST${i}`, sourceRow: i + 2 })) };
  render(<PublicKnowledge data={data} />);
  expect(screen.getAllByRole("article")).toHaveLength(20);
  fireEvent.click(screen.getByText("下一页"));
  expect(screen.getAllByRole("article")).toHaveLength(3);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "TEST1" } });
  expect(screen.getAllByRole("article")).toHaveLength(11);
  expect(screen.queryByText("下一页")).toBeNull();
});

it("renders source descriptions as plain text", () => {
  render(<PublicKnowledge data={{ ...catalog, items: [{ ...item, note: '<img src=x onerror="alert(1)">' }] }} />);
  expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeTruthy();
  expect(document.querySelector("article img")).toBeNull();
});
