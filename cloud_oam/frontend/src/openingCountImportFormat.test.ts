// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, apiBinary } from "./api";
import {
  checkOpeningCountFormat, openingCountTemplate, OPENING_IMPORT_MAX_BYTES,
} from "./openingCountImportFormat";

vi.mock("./api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./api")>();
  return { ...original, api: vi.fn(), apiBinary: vi.fn() };
});

const mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

describe("opening count format-only client", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends an exact XLSX body and rejects a contradictory server result", async () => {
    const file = new File(["source"], "count.xlsx", { type: mime });
    vi.mocked(api).mockResolvedValue({
      schema_version: "1.0", format: "opening_count_import_v1",
      source_sha256: "a".repeat(64), row_count: 1,
      format_valid: true, payload_sha256: null, errors: [],
    });
    await expect(checkOpeningCountFormat(file)).rejects.toThrow("不符合约定");
    const [path, init] = vi.mocked(api).mock.calls[0];
    expect(path).toContain("/imports/opening-count/format-check");
    expect(init?.body).toBe(file);
    expect(new Headers(init?.headers).get("Content-Type")).toBe(mime);
    expect(init?.cache).toBe("no-store");
  });

  it("fails locally for oversized input and refuses a successful HTML response as XLSX", async () => {
    const oversized = new File([new Uint8Array(OPENING_IMPORT_MAX_BYTES + 1)], "count.xlsx");
    await expect(checkOpeningCountFormat(oversized)).rejects.toThrow("8 MiB");
    expect(api).not.toHaveBeenCalled();
    vi.mocked(apiBinary).mockResolvedValue(new Response("<html>login</html>", {
      status: 200, headers: { "Content-Type": "text/html" },
    }));
    await expect(openingCountTemplate()).rejects.toThrow("文件响应格式错误");
  });
});
