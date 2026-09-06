import { describe, expect, it } from "vitest";

import { createAllocationRecoveryStore, recoverAllocationCommand, type AllocationSentinel } from "./materialRequestAllocationRecovery";

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

  it("keeps the sentinel when a confirmed command has different source projection anchors", async () => {
    const store = createAllocationRecoveryStore(storage());
    store.persist(sentinel);
    const adapter = {
      loadIdentity: async () => ({ schema_version: "1.0", person_id: sentinel.person_id, authorization_version: 7 }),
      loadAccess: async () => ({
        schema_version: "1.0", person_id: sentinel.person_id, authorization_version: 7,
        can_read: true, can_create: false, can_withdraw: false, can_cancel: false,
        can_read_material_catalog: true, can_read_allocation_options: true, can_approve_region: false,
        can_approve_headquarters: false, can_register_external: false, can_verify_external: false,
      }),
      allocationCommandStatus: async () => ({ schema_version: "1.0", lookup_status: "confirmed", command: {
        request_id: sentinel.request_id, allocation_id: "50000000-0000-4000-8000-000000000001",
        allocation_no: "AL-TEST", request_version: 4, revision_id: "60000000-0000-4000-8000-000000000001", revision_no: 1,
        request_line_id: sentinel.request_line_id, source_stock_account_id: sentinel.source_stock_account_id,
        source_balance_version: 99, source_ledger_cursor: 9, allocated_qty: "1.000", allocation_status: "allocated",
        request_status: "approved", idempotency_replayed: true, state_axes: {
          request_status: "approved", allocation_status: "allocated", reservation_status: "not_reserved",
          outbound_status: "not_started", shipment_status: "not_started", logistics_signature_status: "not_signed",
          oam_receipt_status: "not_occurred", personal_inbound_status: "not_started", notification_status: "not_started", reconciliation_status: "not_started",
        },
      } }),
    } as any;
    await expect(recoverAllocationCommand(adapter, store, sentinel)).rejects.toThrow(/锚点不一致/);
    expect(store.read()).toMatchObject({ kind: "valid", value: sentinel });
  });
});
