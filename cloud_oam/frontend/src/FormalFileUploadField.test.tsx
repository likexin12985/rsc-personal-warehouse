// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FormalFilePurpose, FormalUploadFile, PreparedFormalFileUpload } from "./formalFileUpload";
import FormalFileUploadField, { type FormalFileUploadClient } from "./FormalFileUploadField";

const FILE_ID = "90000000-0000-4000-8000-000000000001";
const SHA = "ab".repeat(32);

function selectedFile(): File {
  return new File([new Uint8Array([1, 2, 3])], "盘点照片.jpg", { type: "image/jpeg" });
}

function prepared(file: FormalUploadFile, purpose: FormalFilePurpose): PreparedFormalFileUpload {
  return Object.freeze({
    file,
    purpose,
    original_filename: file.name,
    size_bytes: file.size,
    mime_type: file.type,
    sha256: SHA,
    intent_headers: { "Idempotency-Key": `file-intent-${"a".repeat(36)}`, "X-Request-ID": `web-${"b".repeat(36)}` },
    complete_headers: { "X-Request-ID": `web-${"c".repeat(36)}` },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("formal file upload field", () => {
  it("retains one prepared coordinate after uncertainty and clears it on identity/object change", async () => {
    const prepare = vi.fn(async (file: FormalUploadFile, purpose: FormalFilePurpose) => prepared(file, purpose));
    const execute = vi.fn()
      .mockRejectedValueOnce(new Error("上传结果待确认"))
      .mockImplementationOnce(async (value: PreparedFormalFileUpload) => ({
        file_id: FILE_ID,
        purpose: value.purpose,
        status: "available" as const,
        verified_at: "2026-09-01T08:01:00Z",
        sha256: value.sha256,
        size_bytes: value.size_bytes,
        mime_type: value.mime_type,
      }));
    const client: FormalFileUploadClient = { prepare, execute };
    const onAvailableChange = vi.fn();
    const onBlockingChange = vi.fn();
    const { rerender } = render(<FormalFileUploadField purpose="stocktake_evidence" bindingKey="person-1:v1:task-1:scope-1" label="选择盘点证据" client={client} onAvailableChange={onAvailableChange} onBlockingChange={onBlockingChange} />);

    fireEvent.change(screen.getByLabelText("选择盘点证据"), { target: { files: [selectedFile()] } });
    expect(await screen.findByText("状态：失败或结果待确认")).toBeTruthy();
    expect(screen.getByText("盘点照片.jpg")).toBeTruthy();
    expect(screen.getByText(/3 B · SHA-256/).textContent).toContain(SHA);
    expect(onBlockingChange).toHaveBeenLastCalledWith(true);
    expect(execute).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "按原上传坐标重试" }));
    expect(await screen.findByText("状态：available（已完成严格确认）")).toBeTruthy();
    expect(execute).toHaveBeenCalledTimes(2);
    expect(execute.mock.calls[1][0]).toBe(execute.mock.calls[0][0]);
    await waitFor(() => {
      expect(onAvailableChange).toHaveBeenLastCalledWith([expect.objectContaining({ file_id: FILE_ID, purpose: "stocktake_evidence", status: "available" })]);
      expect(onBlockingChange).toHaveBeenLastCalledWith(false);
    });

    rerender(<FormalFileUploadField purpose="stocktake_evidence" bindingKey="person-2:v2:task-2:scope-2" label="选择盘点证据" client={client} onAvailableChange={onAvailableChange} onBlockingChange={onBlockingChange} />);
    await waitFor(() => expect(screen.queryByText("盘点照片.jpg")).toBeNull());
    expect(onAvailableChange).toHaveBeenLastCalledWith([]);
  });

  it("never exposes a mismatched-purpose completion as available", async () => {
    const client: FormalFileUploadClient = {
      prepare: vi.fn(async (file, purpose) => prepared(file, purpose)),
      execute: vi.fn(async (value) => ({
        file_id: FILE_ID,
        purpose: "request_attachment" as const,
        status: "available" as const,
        verified_at: "2026-09-01T08:01:00Z",
        sha256: value.sha256,
        size_bytes: value.size_bytes,
        mime_type: value.mime_type,
      })),
    };
    const onAvailableChange = vi.fn();
    render(<FormalFileUploadField purpose="external_approval_evidence" bindingKey="request-1:step-3" label="选择外部审批证据" client={client} onAvailableChange={onAvailableChange} />);
    fireEvent.change(screen.getByLabelText("选择外部审批证据"), { target: { files: [selectedFile()] } });
    expect(await screen.findByText("状态：失败或结果待确认")).toBeTruthy();
    expect(screen.queryByText("状态：available（已完成严格确认）")).toBeNull();
    expect(onAvailableChange).toHaveBeenLastCalledWith([]);
  });
});
