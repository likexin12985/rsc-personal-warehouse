// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  checkOpeningCountFormat, openingCountFormatErrors, saveOpeningCountWorkbook,
} from "../openingCountImportFormat";
import OpeningCountFormatPanel from "./OpeningCountFormatPanel";

vi.mock("../openingCountImportFormat", () => ({
  checkOpeningCountFormat: vi.fn(),
  openingCountFormatErrors: vi.fn(),
  openingCountTemplate: vi.fn(),
  saveOpeningCountWorkbook: vi.fn(),
}));

const file = new File(["one"], "count.xlsx", {
  type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
});

describe("opening-count XLSX format precheck UI", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("shows format-only success without offering an import or stock command", async () => {
    vi.mocked(checkOpeningCountFormat).mockResolvedValue({
      source_sha256: "a".repeat(64), row_count: 2, format_valid: true,
      payload_sha256: "b".repeat(64), errors: [],
    });
    render(<OpeningCountFormatPanel />);
    expect(screen.getByText(/未绑定盘点任务或范围/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/选择 XLSX 文件/), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "检查格式" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("当前未导入"));
    expect(checkOpeningCountFormat).toHaveBeenCalledWith(file);
    expect(screen.queryByRole("button", { name: /确认导入|提交计数/ })).toBeNull();
  });

  it("keeps errors scoped to the selected file and downloads the same file's report", async () => {
    vi.mocked(checkOpeningCountFormat).mockResolvedValue({
      source_sha256: "a".repeat(64), row_count: 1, format_valid: false,
      payload_sha256: null, errors: [{ row: 2, field: "counted_qty", code: "quantity_invalid", message: "实盘数量无效" }],
    });
    const report = new Blob(["report"]);
    vi.mocked(openingCountFormatErrors).mockResolvedValue(report);
    render(<OpeningCountFormatPanel />);
    fireEvent.change(screen.getByLabelText(/选择 XLSX 文件/), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "检查格式" }));
    await waitFor(() => expect(screen.getByText("实盘数量无效")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "下载错误报告" }));
    await waitFor(() => expect(openingCountFormatErrors).toHaveBeenCalledWith(file));
    expect(saveOpeningCountWorkbook).toHaveBeenCalledWith(report, "rsc-opening-count-errors.xlsx");
    fireEvent.change(screen.getByLabelText(/选择 XLSX 文件/), { target: { files: [new File(["two"], "next.xlsx")] } });
    expect(screen.queryByText("实盘数量无效")).toBeNull();
    expect(screen.queryByRole("button", { name: "下载错误报告" })).toBeNull();
  });
});
