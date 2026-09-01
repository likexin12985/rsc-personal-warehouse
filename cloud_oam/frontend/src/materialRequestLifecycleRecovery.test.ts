import { describe, expect, it } from "vitest";

import { createMaterialRequestLifecycleRecoveryStore } from "./materialRequestLifecycleRecovery";

const TRACE_ID = "web-12345678";
const OTHER_TRACE_ID = "web-87654321";

class MemoryStorage {
  readonly values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe("material-request lifecycle recovery sentinel", () => {
  it("persists only one minimal tab-scoped trace sentinel and never expires it", () => {
    const storage = new MemoryStorage();
    let now = 1_725_200_000_000;
    const store = createMaterialRequestLifecycleRecoveryStore(storage, () => now);
    const first = store.persist(TRACE_ID);
    expect(first).toEqual({
      v: 1,
      kind: "material_request_lifecycle",
      x_request_id: TRACE_ID,
      created_at: now,
    });
    const raw = [...storage.values.values()][0];
    expect(JSON.parse(raw)).toEqual(first);
    expect(Object.keys(JSON.parse(raw)).sort()).toEqual(["created_at", "kind", "v", "x_request_id"]);
    for (const forbidden of [
      "Idempotency-Key", "reason", "contact", "address", "token", "hash", "withdraw", "cancel",
    ]) {
      expect(raw).not.toContain(forbidden);
    }

    now += 365 * 24 * 60 * 60 * 1000;
    expect(store.read()).toEqual({ kind: "valid", value: first });
    expect(store.persist(TRACE_ID)).toEqual(first);
    expect(() => store.persist(OTHER_TRACE_ID)).toThrow(/禁止覆盖/);
  });

  it("clears only the exact reread trace and verifies removal", () => {
    const storage = new MemoryStorage();
    const store = createMaterialRequestLifecycleRecoveryStore(storage, () => 1);
    store.persist(TRACE_ID);
    expect(() => store.clear(OTHER_TRACE_ID)).toThrow(/不匹配/);
    expect(store.read().kind).toBe("valid");
    store.clear(TRACE_ID);
    expect(store.read()).toEqual({ kind: "missing" });
    expect(() => store.clear(TRACE_ID)).toThrow(/缺失/);
  });

  it("retains corrupt state and fails closed instead of overwriting or deleting it", () => {
    const storage = new MemoryStorage();
    storage.setItem("cloud-oam-material-request-lifecycle-sentinel-v1", "{bad-json");
    const store = createMaterialRequestLifecycleRecoveryStore(storage, () => 1);
    expect(store.read()).toEqual({ kind: "corrupt" });
    expect(() => store.persist(TRACE_ID)).toThrow(/已损坏/);
    expect(() => store.clear(TRACE_ID)).toThrow(/损坏/);
    expect([...storage.values.values()]).toEqual(["{bad-json"]);
  });

  it("fails closed when session storage cannot be read", () => {
    const unavailable = {
      getItem(): string | null { throw new Error("denied"); },
      setItem(): void { throw new Error("denied"); },
      removeItem(): void { throw new Error("denied"); },
    };
    const store = createMaterialRequestLifecycleRecoveryStore(unavailable, () => 1);
    expect(store.read()).toEqual({ kind: "unavailable" });
    expect(() => store.persist(TRACE_ID)).toThrow(/不可用/);
    expect(() => store.clear(TRACE_ID)).toThrow(/缺失、损坏或不匹配/);
  });
});
