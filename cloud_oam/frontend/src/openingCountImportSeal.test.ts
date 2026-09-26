import { describe, expect, it, vi } from "vitest";
import { createOpeningImportClient, parseOpeningImportSeal, type OpeningImportSealBinding,
  type OpeningImportSeal } from "./openingCountImportClient";

const id = (n: number) => `95000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const expected: OpeningImportSealBinding = { task_id: id(1), round_id: id(2), scope_id: id(3),
  actor_person_id: id(4), authorization_version: 7, source_file_id: id(5), source_sha256: "a".repeat(64), size_bytes: 1024 };
const reviewer = { person_id: id(9), authorization_version: 12 };
const importKey = "opening-import-original-key", uploadKey = "opening-upload-original-key";
const proof: OpeningImportSeal = { ...expected, source_file_id: id(5), schema_version: "rsc.opening_import_seal.v1",
  seal_id: id(6), terminal_audit_id: id(7), reviewer_person_id: reviewer.person_id,
  reviewer_authorization_version: reviewer.authorization_version, permanent_nonexecution: true,
  automatic_retry_allowed: false, source_object_may_exist: true };

describe("permanent original import seal transport", () => {
  it.each([id(5), null])("binds both original keys and all coordinates with source UUID %s", async source_file_id => {
    const binding = { ...expected, source_file_id };
    const read = vi.fn(async () => proof), write = vi.fn(async () => proof), objectFetch = vi.fn();
    const client = createOpeningImportClient({ read, write, objectFetch });
    expect(await client.requestSeal(importKey, uploadKey, binding, reviewer)).toEqual(proof);
    expect(await client.recoverSeal(importKey, uploadKey, binding, reviewer)).toEqual(proof);
    const [writePath, writeInit] = write.mock.calls[0] as unknown as [string, RequestInit];
    const [readPath, readInit] = read.mock.calls[0] as unknown as [string, RequestInit];
    expect(writePath).toBe("/v1/stocktakes/opening/imports/opening-count/command-seals");
    expect(writeInit.method).toBe("POST");
    const body = Object.fromEntries(Object.entries(binding).filter(([, v]) => v !== null));
    expect(JSON.parse(writeInit.body as string)).toEqual(body);
    const url = new URL(readPath, "https://example.test");
    expect(url.pathname).toBe(writePath + "/recovery");
    expect(Object.fromEntries(url.searchParams)).toEqual(Object.fromEntries(Object.entries(body).map(([k, v]) => [k, String(v)])));
    for (const init of [writeInit, readInit]) {
      expect(init.cache).toBe("no-store");
      const h = new Headers(init.headers);
      expect(h.get("Idempotency-Key")).toBe(importKey);
      expect(h.get("X-Original-Upload-Key")).toBe(uploadKey);
    }
    expect(readPath).not.toContain(importKey); expect(readPath).not.toContain(uploadKey);
    expect(readInit.method).toBeUndefined(); expect(readInit.body).toBeUndefined();
    expect(objectFetch).not.toHaveBeenCalled();
  });

  it.each(["404", "403", "409 accepted", "timeout"])("does not replay a POST or synthesize proof after %s", async message => {
    const read = vi.fn(async () => { throw new Error(message); });
    const write = vi.fn(async () => { throw new Error(message); });
    const client = createOpeningImportClient({ read, write });
    await expect(client.requestSeal(importKey, uploadKey, expected, reviewer)).rejects.toThrow(message);
    expect(write).toHaveBeenCalledTimes(1); expect(read).not.toHaveBeenCalled();
    await expect(client.recoverSeal(importKey, uploadKey, expected, reviewer)).rejects.toThrow(message);
    expect(write).toHaveBeenCalledTimes(1); expect(read).toHaveBeenCalledTimes(1);
  });

  it("snapshots the original binding and current reviewer before awaiting the response", async () => {
    const binding = { ...expected }, identity = { ...reviewer };
    const read = vi.fn(async () => {
      binding.size_bytes = 2048; identity.authorization_version = 13;
      return { ...proof, size_bytes: 2048, reviewer_authorization_version: 13 };
    });
    await expect(createOpeningImportClient({ read }).recoverSeal(importKey, uploadKey, binding, identity)).rejects.toThrow();
  });

  it.each([
    ["task_id", id(10)], ["round_id", id(10)], ["scope_id", id(10)], ["actor_person_id", id(10)],
    ["authorization_version", 8], ["source_file_id", id(10)], ["source_sha256", "b".repeat(64)], ["size_bytes", 1025],
    ["reviewer_person_id", id(10)], ["reviewer_authorization_version", 13], ["seal_id", null], ["terminal_audit_id", null],
    ["source_file_id", "00000000-0000-0000-0000-000000000000"], ["schema_version", "rsc.opening_import_seal.v2"],
    ["permanent_nonexecution", false], ["automatic_retry_allowed", true], ["source_object_may_exist", false],
    ["original_filename", "private.xlsx"], ["storage_key", "private/object"], ["upload_key", uploadKey],
    ["url", "https://private.example.test"],
  ])("rejects mismatched or unsupported proof field %s=%s", (field, value) => {
    expect(() => parseOpeningImportSeal({ ...proof, [field]: value }, expected, reviewer)).toThrow();
  });

  it.each([
    { authorization_version: true }, { authorization_version: 0 }, { size_bytes: 8388609 }, { size_bytes: 0 },
    { source_file_id: undefined }, { task_id: "" }, { source_sha256: "A".repeat(64) }, { extra: "untrusted" },
  ])("rejects malformed original coordinates before transport: %j", async patch => {
    const read = vi.fn(), write = vi.fn();
    const bad = { ...expected, ...patch } as unknown as OpeningImportSealBinding;
    const client = createOpeningImportClient({ read, write });
    await expect(client.requestSeal(importKey, uploadKey, bad, reviewer)).rejects.toThrow();
    await expect(client.recoverSeal(importKey, uploadKey, bad, reviewer)).rejects.toThrow();
    expect(read).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
  });

  it("rejects absent, invalid or equal original keys before transport", async () => {
    const read = vi.fn(), write = vi.fn(), client = createOpeningImportClient({ read, write });
    for (const keys of [["", uploadKey], [importKey, "bad\r\nkey"], [importKey, importKey]]) {
      await expect(client.requestSeal(keys[0], keys[1], expected, reviewer)).rejects.toThrow();
      await expect(client.recoverSeal(keys[0], keys[1], expected, reviewer)).rejects.toThrow();
    }
    expect(read).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
  });
});
