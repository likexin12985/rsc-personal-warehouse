import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  executeFormalFileUpload,
  FORMAL_FILE_MAXIMUM_SIZE_BYTES,
  prepareFormalFileUpload,
  type FormalUploadFile,
} from "./formalFileUpload";


const FILE_ID = "90000000-0000-4000-8000-000000000001";
const SHA = "ab".repeat(32);
const NOW = Date.parse("2026-09-01T08:00:00Z");


function file(
  name = "现场照片.jpg",
  type = "image/jpeg",
  bytes = new Uint8Array([1, 2, 3]),
): FormalUploadFile {
  const blob = new Blob([bytes], { type });
  return Object.assign(blob, { name }) as FormalUploadFile;
}

function digest(): Promise<ArrayBuffer> {
  const bytes = new Uint8Array(32);
  for (let index = 0; index < bytes.length; index += 2) {
    bytes[index] = 0xab;
    bytes[index + 1] = 0xab;
  }
  return Promise.resolve(bytes.buffer);
}

function uploadIntent(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1.0",
    file_id: FILE_ID,
    purpose: "request_attachment",
    status: "pending",
    upload: {
      method: "PUT",
      url: "https://private-bucket.oss-cn-test.aliyuncs.com/signed-key?signature=opaque",
      expires_at: "2026-09-01T08:10:00Z",
      headers: {
        "Content-Type": "image/jpeg",
        "x-oss-meta-sha256": SHA,
        "x-oss-meta-file-id": FILE_ID,
        "x-oss-forbid-overwrite": "true",
      },
    },
    idempotency_replayed: false,
    ...overrides,
  };
}

function completion() {
  return {
    schema_version: "1.0",
    file_id: FILE_ID,
    purpose: "request_attachment",
    status: "available",
    verified_at: "2026-09-01T08:01:00Z",
    already_available: false,
  };
}


describe("formal private-file PC upload", () => {
  it("keeps receipt evidence purpose through upload and refuses another purpose at completion", async () => {
    const prepared = await prepareFormalFileUpload(file(), "receipt_exception_evidence", { digest });
    for (const completionPurpose of ["receipt_exception_evidence", "request_attachment"]) {
      const requester = vi.fn(async (path: string) => path.endsWith("/complete")
        ? { ...completion(), purpose: completionPurpose }
        : uploadIntent({ purpose: "receipt_exception_evidence" }));
      const result = executeFormalFileUpload(prepared, {
        requester, objectFetcher: async () => new Response(null, { status: 200 }), now: () => NOW,
      });
      if (completionPurpose === "receipt_exception_evidence") {
        expect((await result).purpose).toBe(completionPurpose);
      } else {
        await expect(result).rejects.toBeInstanceOf(ApiError);
      }
    }
  });

  it("hashes locally, uses exact signed PUT headers, then asks the API to HEAD-verify", async () => {
    const prepared = await prepareFormalFileUpload(
      file(),
      "request_attachment",
      { digest },
    );
    expect(prepared.sha256).toBe(SHA);
    expect(prepared.intent_headers["Idempotency-Key"]).toMatch(
      /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/,
    );

    const requester = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/v1/files/upload-intents") {
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          purpose: "request_attachment",
          original_filename: "现场照片.jpg",
          size_bytes: 3,
          mime_type: "image/jpeg",
          sha256: SHA,
        });
        return uploadIntent();
      }
      expect(path).toBe(`/v1/files/${FILE_ID}/complete`);
      expect(init?.method).toBe("POST");
      return completion();
    });
    const objectFetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("PUT");
      expect(init?.body).toBe(prepared.file);
      expect(init?.credentials).toBe("omit");
      expect(init?.redirect).toBe("error");
      expect(init?.referrerPolicy).toBe("no-referrer");
      expect(new Headers(init?.headers)).toEqual(new Headers({
        "Content-Type": "image/jpeg",
        "x-oss-meta-sha256": SHA,
        "x-oss-meta-file-id": FILE_ID,
        "x-oss-forbid-overwrite": "true",
      }));
      return new Response(null, { status: 200 });
    });

    const result = await executeFormalFileUpload(prepared, {
      requester,
      objectFetcher,
      now: () => NOW,
    });

    expect(result).toEqual({
      file_id: FILE_ID,
      purpose: "request_attachment",
      status: "available",
      verified_at: "2026-09-01T08:01:00Z",
      sha256: SHA,
      size_bytes: 3,
      mime_type: "image/jpeg",
    });
    expect(requester).toHaveBeenCalledTimes(2);
    expect(objectFetcher).toHaveBeenCalledTimes(1);
  });

  it("does not upload again when an idempotent intent is already available", async () => {
    const prepared = await prepareFormalFileUpload(
      file(),
      "request_attachment",
      { digest },
    );
    const requester = vi.fn(async (path: string) => (
      path.endsWith("/complete")
        ? { ...completion(), already_available: true }
        : uploadIntent({ status: "available", upload: null, idempotency_replayed: true })
    ));
    const objectFetcher = vi.fn();

    await executeFormalFileUpload(prepared, {
      requester,
      objectFetcher,
      now: () => NOW,
    });

    expect(objectFetcher).not.toHaveBeenCalled();
    expect(requester).toHaveBeenCalledTimes(2);
  });

  it("uses completion as the readback after an uncertain PUT result", async () => {
    const prepared = await prepareFormalFileUpload(
      file(),
      "request_attachment",
      { digest },
    );
    const requester = vi.fn(async (path: string) => (
      path.endsWith("/complete") ? completion() : uploadIntent()
    ));
    const objectFetcher = vi.fn(async () => {
      throw new TypeError("network result unavailable");
    });

    const result = await executeFormalFileUpload(prepared, {
      requester,
      objectFetcher,
      now: () => NOW,
    });

    expect(result.status).toBe("available");
    expect(requester).toHaveBeenCalledTimes(2);
  });

  it("keeps one coordinate and fails closed when PUT and completion are both uncertain", async () => {
    const prepared = await prepareFormalFileUpload(
      file(),
      "request_attachment",
      { digest },
    );
    const requester = vi.fn(async (path: string, _init?: RequestInit) => {
      if (path.endsWith("/complete")) throw new ApiError(503, "HEAD unavailable");
      return uploadIntent();
    });
    const objectFetcher = vi.fn(async () => {
      throw new TypeError("network result unavailable");
    });

    await expect(executeFormalFileUpload(prepared, {
      requester,
      objectFetcher,
      now: () => NOW,
    })).rejects.toThrow("只能使用原请求坐标重试");

    const intentCall = requester.mock.calls[0];
    expect(new Headers(intentCall[1]?.headers).get("Idempotency-Key")).toBe(
      prepared.intent_headers["Idempotency-Key"],
    );
  });

  it("rejects an insecure URL or any signed-header mismatch before sending bytes", async () => {
    const prepared = await prepareFormalFileUpload(
      file(),
      "request_attachment",
      { digest },
    );
    for (const intent of [
      uploadIntent({
        upload: {
          ...uploadIntent().upload,
          url: "http://private-bucket.example/signed-key",
        },
      }),
      uploadIntent({
        upload: {
          ...uploadIntent().upload,
          headers: {
            ...uploadIntent().upload.headers,
            "x-oss-meta-sha256": "cd".repeat(32),
          },
        },
      }),
    ]) {
      const requester = vi.fn(async () => intent);
      const objectFetcher = vi.fn();
      await expect(executeFormalFileUpload(prepared, {
        requester,
        objectFetcher,
        now: () => NOW,
      })).rejects.toBeInstanceOf(ApiError);
      expect(objectFetcher).not.toHaveBeenCalled();
    }
  });

  it("rejects empty metadata and files above the formal size limit", async () => {
    await expect(prepareFormalFileUpload(
      file("bad\nname.jpg"),
      "request_attachment",
      { digest },
    )).rejects.toBeInstanceOf(ApiError);

    const oversized = {
      ...file(),
      size: FORMAL_FILE_MAXIMUM_SIZE_BYTES + 1,
      arrayBuffer: async () => new ArrayBuffer(0),
    } as unknown as FormalUploadFile;
    await expect(prepareFormalFileUpload(
      oversized,
      "request_attachment",
      { digest },
    )).rejects.toThrow("120 MB");
  });
});
