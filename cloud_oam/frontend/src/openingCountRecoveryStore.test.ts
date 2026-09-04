import { describe, expect, it } from "vitest";
import {
  createOpeningCountRecoveryStore, validateOpeningCountSentinel, withNoPendingOpeningCount,
  type OpeningCountLockManager, type OpeningCountSentinel, type OpeningCountTaskLease,
} from "./openingCountRecoveryStore";

const id = (prefix: string) => `${prefix}0000000-0000-4000-8000-000000000001`;
const sentinel: OpeningCountSentinel = {
  v: 1, kind: "opening_scope_count", task_id: id("1"), round_id: id("2"), round_no: 1, scope_id: id("3"),
  actor_person_id: id("4"), actor_authorization_version: 7, trace_request_id: "opening-count-trace-001",
};
function fixture() {
  const values = new Map<string, string>();
  const held = new Set<string>();
  const calls: string[] = [];
  const storage = {
    getItem(key: string) { return values.get(key) ?? null; },
    setItem(key: string, value: string) { values.set(key, value); },
    removeItem(key: string) { values.delete(key); },
  };
  const locks: OpeningCountLockManager = {
    async request(name, options, callback) {
      calls.push(name);
      expect(options).toEqual({ mode: "exclusive", ifAvailable: true });
      if (held.has(name)) return callback(null);
      held.add(name);
      try { return await callback({ name }); } finally { held.delete(name); }
    },
  };
  return { values, storage, locks, calls, held };
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("durable opening count task coordinates", () => {
  it("stores only canonical non-sensitive anchors and survives a new store instance", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    await store.withTaskLease(sentinel.task_id, async (lease) => {
      expect(lease.read()).toEqual({ kind: "missing" });
      lease.persist(sentinel);
      lease.persist({ ...sentinel });
      expect(lease.read()).toEqual({ kind: "valid", value: sentinel });
    });
    const restarted = createOpeningCountRecoveryStore(f);
    const read = restarted.read(sentinel.task_id);
    expect(read).toEqual({ kind: "valid", value: sentinel });
    if (read.kind === "valid") expect(Object.isFrozen(read.value)).toBe(true);
    expect([...f.values.values()]).toEqual([JSON.stringify(sentinel)]);
    expect([...f.values.values()].join()).not.toMatch(/idempotency|payload|body|hash|token|mobile|name/i);
  });

  it.each(["idempotency_key", "body", "request_hash", "mobile", "user_id"])(
    "rejects the extra %s field instead of persisting it", (field) => {
      expect(() => validateOpeningCountSentinel({ ...sentinel, [field]: "must-not-persist" })).toThrow("无效");
    },
  );

  it("does not grant POST authority to the same persisted coordinates after lease or process restart", async () => {
    const f = fixture();
    const original = createOpeningCountRecoveryStore(f);
    await original.withTaskLease(sentinel.task_id, async (lease) => { lease.persist(sentinel); });
    let posts = 0;
    for (const store of [original, createOpeningCountRecoveryStore(f)]) {
      await expect(store.withTaskLease(sentinel.task_id, async (lease) => {
        lease.persist({ ...sentinel }); posts += 1;
      })).rejects.toThrow("仍待核验");
    }
    expect(posts).toBe(0);
    expect(original.read(sentinel.task_id)).toEqual({ kind: "valid", value: sentinel });
  });

  it("rejects legacy, malformed, cross-task and unsupported records without deleting them", async () => {
    for (const raw of ["{broken", JSON.stringify({ trace_request_id: sentinel.trace_request_id }),
      JSON.stringify({ ...sentinel, v: 2 }), JSON.stringify({ ...sentinel, task_id: id("9") })]) {
      const f = fixture();
      const store = createOpeningCountRecoveryStore(f);
      await store.withTaskLease(sentinel.task_id, async (lease) => { lease.persist(sentinel); });
      const key = [...f.values.keys()][0];
      f.values.set(key, raw);
      expect(store.read(sentinel.task_id)).toEqual({ kind: "corrupt" });
      await store.withTaskLease(sentinel.task_id, async (lease) => {
        expect(() => lease.persist(sentinel)).toThrow("禁止覆盖");
        expect(() => lease.clearExact(sentinel)).toThrow("禁止清理");
      });
      expect(f.values.get(key)).toBe(raw);
    }
  });

  it("requires exact task, round, scope, identity, authorization and trace when clearing", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    await store.withTaskLease(sentinel.task_id, async (lease) => {
      lease.persist(sentinel);
      for (const changed of [
        { ...sentinel, task_id: id("9") }, { ...sentinel, round_id: id("9") }, { ...sentinel, round_no: 2 },
        { ...sentinel, scope_id: id("9") }, { ...sentinel, actor_person_id: id("9") },
        { ...sentinel, actor_authorization_version: 8 }, { ...sentinel, trace_request_id: "another-trace-001" },
      ]) {
        expect(() => lease.clearExact(changed)).toThrow();
        expect(() => lease.persist(changed)).toThrow();
      }
      expect(lease.read()).toEqual({ kind: "valid", value: sentinel });
      lease.clearExact(sentinel);
      expect(lease.read()).toEqual({ kind: "missing" });
    });
  });

  it("holds a cross-instance task lease for the whole operation without queuing a later write", async () => {
    const f = fixture();
    const firstStore = createOpeningCountRecoveryStore(f);
    const secondStore = createOpeningCountRecoveryStore(f);
    const entered = deferred();
    const finish = deferred();
    let secondCalls = 0;
    const first = firstStore.withTaskLease(sentinel.task_id, async (lease) => {
      lease.persist(sentinel);
      entered.resolve();
      await finish.promise;
      lease.clearExact(sentinel);
    });
    await entered.promise;
    await expect(secondStore.withTaskLease(sentinel.task_id, async () => { secondCalls += 1; })).rejects.toThrow("其他页面");
    await expect(firstStore.withTaskLease(sentinel.task_id, async () => { secondCalls += 1; })).rejects.toThrow("请勿重复");
    finish.resolve();
    await first;
    expect(secondCalls).toBe(0);
    expect(f.held.size).toBe(0);
    await secondStore.withTaskLease(sentinel.task_id, async () => { secondCalls += 1; });
    expect(secondCalls).toBe(1);
  });

  it("does not globally serialize independent task coordinates", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    const entered = deferred();
    const finish = deferred();
    const first = store.withTaskLease(sentinel.task_id, async () => {
      entered.resolve(); await finish.promise;
    });
    await entered.promise;
    await store.withTaskLease(id("8"), async (lease) => {
      lease.persist({ ...sentinel, task_id: id("8") });
    });
    expect(f.held.size).toBe(1);
    finish.resolve();
    await first;
    expect(f.calls[0]).not.toBe(f.calls[1]);
  });

  it("blocks all other task commands on pending or corrupt count coordinates", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    let writes = 0;
    await store.withTaskLease(sentinel.task_id, async (lease) => { lease.persist(sentinel); });
    await expect(withNoPendingOpeningCount(sentinel.task_id, async () => { writes += 1; }, store)).rejects.toThrow("禁止其他写入");
    const key = [...f.values.keys()][0];
    f.values.set(key, "{}");
    await expect(withNoPendingOpeningCount(sentinel.task_id, async () => { writes += 1; }, store)).rejects.toThrow("禁止其他写入");
    expect(writes).toBe(0);
    await withNoPendingOpeningCount(id("8"), async () => { writes += 1; }, store);
    expect(writes).toBe(1);
  });

  it("keeps the task barrier active until another command fully settles", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    const entered = deferred();
    const finish = deferred();
    const other = withNoPendingOpeningCount(sentinel.task_id, async () => {
      entered.resolve(); await finish.promise;
    }, store);
    await entered.promise;
    await expect(createOpeningCountRecoveryStore(f).withTaskLease(sentinel.task_id, async () => undefined)).rejects.toThrow("其他页面");
    finish.resolve();
    await other;
    expect(f.held.size).toBe(0);
  });

  it("invalidates escaped leases on either success or error", async () => {
    for (const throws of [false, true]) {
      const f = fixture();
      const store = createOpeningCountRecoveryStore(f);
      let escaped!: OpeningCountTaskLease;
      const work = store.withTaskLease(sentinel.task_id, async (lease) => {
        escaped = lease;
        if (throws) throw new Error("intentional failure");
      });
      if (throws) await expect(work).rejects.toThrow("intentional"); else await work;
      expect(() => escaped.persist(sentinel)).toThrow("协调已结束");
      expect(() => escaped.read()).toThrow("协调已结束");
      expect(() => escaped.clearExact(sentinel)).toThrow("协调已结束");
      expect(f.held.size).toBe(0);
    }
  });

  it("never runs the work without persistent storage or cross-tab locks", async () => {
    const f = fixture();
    for (const options of [{ storage: null, locks: f.locks }, { storage: f.storage, locks: null }]) {
      let calls = 0;
      await expect(createOpeningCountRecoveryStore(options).withTaskLease(sentinel.task_id, async () => { calls += 1; })).rejects.toThrow("不可用");
      expect(calls).toBe(0);
    }
  });

  it("stops before transport when persistence or its reread fails", async () => {
    for (const mode of ["throw", "drop", "corrupt", "read-throws"] as const) {
      const f = fixture();
      let written = false;
      let posts = 0;
      const storage = {
        ...f.storage,
        getItem(key: string) {
          if (written && mode === "read-throws") throw new Error("unavailable");
          return f.storage.getItem(key);
        },
        setItem(key: string, value: string) {
          written = true;
          if (mode === "throw") throw new Error("quota exceeded");
          if (mode !== "drop") f.storage.setItem(key, mode === "corrupt" ? "{}" : value);
        },
      };
      const store = createOpeningCountRecoveryStore({ storage, locks: f.locks });
      await expect(store.withTaskLease(sentinel.task_id, async (lease) => {
        lease.persist(sentinel); posts += 1;
      })).rejects.toThrow("写后核验失败");
      await expect(store.withTaskLease(sentinel.task_id, async () => { posts += 1; })).rejects.toThrow("不可用");
      expect(posts).toBe(0);
    }
  });

  it("latches removal uncertainty and does not clear a replacement sentinel", async () => {
    for (const mode of ["throw", "drop", "replacement", "read-throws"] as const) {
      const f = fixture();
      let removed = false;
      const storage = {
        ...f.storage,
        getItem(key: string) {
          if (removed && mode === "read-throws") throw new Error("unavailable");
          return f.storage.getItem(key);
        },
        removeItem(key: string) {
          removed = true;
          if (mode === "throw") throw new Error("storage failed");
          if (mode === "drop") return;
          if (mode === "replacement") f.storage.setItem(key, JSON.stringify({ ...sentinel, trace_request_id: "replacement-trace-001" }));
          else f.storage.removeItem(key);
        },
      };
      const store = createOpeningCountRecoveryStore({ storage, locks: f.locks });
      await expect(store.withTaskLease(sentinel.task_id, async (lease) => {
        lease.persist(sentinel); lease.clearExact(sentinel);
      })).rejects.toThrow("清理未确认");
      expect(store.read(sentinel.task_id)).toEqual({ kind: "unavailable" });
      if (mode === "replacement") expect([...f.values.values()][0]).toContain("replacement-trace-001");
    }
  });

  it("rejects invalid coordinates before any storage or lock access", async () => {
    const f = fixture();
    const store = createOpeningCountRecoveryStore(f);
    for (const bad of ["../legacy", "", "00000000-0000-0000-0000-000000000000"]) {
      expect(() => store.read(bad)).toThrow("无效");
      await expect(store.withTaskLease(bad, async () => undefined)).rejects.toThrow("无效");
    }
    for (const changed of [{ ...sentinel, actor_authorization_version: 0 },
      { ...sentinel, actor_authorization_version: "7" }, { ...sentinel, trace_request_id: "short" },
      { ...sentinel, trace_request_id: "unsafe\nrequest" }]) {
      expect(() => validateOpeningCountSentinel(changed)).toThrow("无效");
    }
    expect(f.calls).toEqual([]);
    expect(f.values.size).toBe(0);
  });
});
