import { describe, expect, it } from "vitest";
import { createSupplyRecoveryStore, type SupplySentinel } from "./materialRequestSupplyRecovery";

function sentinel(): SupplySentinel {
  return { v: 1, kind: "material_request_supply", x_request_id: "supply-trace-12345678",
    person_id: "10000000-0000-4000-8000-000000000001", authorization_version: 1,
    request_id: "20000000-0000-4000-8000-000000000001", action: "create_supply_task",
    request_version: 5, task_id: null, task_version: null };
}
function storage() {
  const rows = new Map<string, string>();
  return { rows, getItem: (key: string) => rows.get(key) ?? null,
    setItem: (key: string, value: string) => { rows.set(key, value); },
    removeItem: (key: string) => { rows.delete(key); } };
}
describe("supply durable request coordinates", () => {
  it("persists and rereads only bounded nonsecret anchors", () => {
    const memory = storage();
    const store = createSupplyRecoveryStore(memory);
    expect(store.read().kind).toBe("missing");
    store.persist(sentinel());
    expect(store.read()).toEqual({ kind: "valid", value: sentinel() });
    store.persist(sentinel());
    const raw = [...memory.rows.values()][0];
    expect(raw).not.toMatch(/reference_no|reason|comment|token|Idempotency|body/);
    expect(() => store.persist({ ...sentinel(), x_request_id: "another-trace-123456" })).toThrow(/禁止覆盖/);
    expect(() => store.clear("another-trace-123456")).toThrow(/不匹配/);
    store.clear(sentinel().x_request_id);
    expect(store.read().kind).toBe("missing");
  });
  it("rejects extra sensitive fields, mismatched task anchors and corrupt data", () => {
    const memory = storage();
    const store = createSupplyRecoveryStore(memory);
    expect(() => store.persist({ ...sentinel(), reason: "private" } as SupplySentinel)).toThrow();
    expect(() => store.persist({ ...sentinel(), action: "cancel_supply_task" })).toThrow();
    memory.setItem("cloud-oam-material-request-supply-sentinel-v1", "{bad");
    expect(store.read().kind).toBe("corrupt");
    expect(() => store.persist(sentinel())).toThrow();
    expect(() => store.clear(sentinel().x_request_id)).toThrow();
  });
  it("fails closed when persistence or readback is unavailable", () => {
    const memory = storage();
    const blocked = createSupplyRecoveryStore({ ...memory, setItem: () => { throw new Error("storage unavailable"); } });
    expect(() => blocked.persist(sentinel())).toThrow();
    const discarded = createSupplyRecoveryStore({ ...memory, setItem: () => {} });
    expect(() => discarded.persist(sentinel())).toThrow(/核验失败/);
    const unreadable = createSupplyRecoveryStore({ ...memory, getItem: () => { throw new Error("blocked"); } });
    expect(unreadable.read().kind).toBe("unavailable");
    expect(() => unreadable.persist(sentinel())).toThrow();
  });
});
