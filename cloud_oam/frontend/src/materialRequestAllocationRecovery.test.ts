import { describe, expect, it } from "vitest";

import { createAllocationRecoveryStore, type AllocationSentinel } from "./materialRequestAllocationRecovery";

const sentinel: AllocationSentinel = {
  v: 1, kind: "material_request_allocation", x_request_id: "web-allocation-12345678",
  person_id: "10000000-0000-4000-8000-000000000001", authorization_version: 7,
  request_id: "20000000-0000-4000-8000-000000000001", request_line_id: "30000000-0000-4000-8000-000000000001",
  request_version: 3, source_stock_account_id: "40000000-0000-4000-8000-000000000001",
  allocated_qty: "1.000", source_balance_version: 8, source_ledger_cursor: 9,
};

function storage(initial: string | null = null) {
  let value = initial;
  return {
    getItem: () => value,
    setItem: (_key: string, next: string) => { value = next; },
    removeItem: () => { value = null; },
  };
}

describe("material-request allocation recovery store", () => {
  it("persists, rereads and clears only the same coordinate", () => {
    const store = createAllocationRecoveryStore(storage());
    expect(store.read()).toEqual({ kind: "missing" });
    store.persist(sentinel);
    expect(store.read()).toMatchObject({ kind: "valid", value: sentinel });
    expect(() => store.persist({ ...sentinel, x_request_id: "web-allocation-87654321" })).toThrow();
    expect(() => store.clear("web-allocation-87654321")).toThrow();
    store.clear(sentinel.x_request_id);
    expect(store.read()).toEqual({ kind: "missing" });
  });

  it("keeps corrupt storage blocked", () => {
    const store = createAllocationRecoveryStore(storage("{}"));
    expect(store.read()).toEqual({ kind: "corrupt" });
    expect(() => store.persist(sentinel)).toThrow(/存储不可用/);
  });
});
