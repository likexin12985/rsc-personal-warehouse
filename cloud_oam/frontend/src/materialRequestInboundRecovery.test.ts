import { access, identity, approvedDetail, REQUEST_ID } from "./materialRequestReservationTestFixtures";
import { describe, expect, it, vi } from "vitest";
import { createInboundStore, validateInboundSentinel, recoverInbound } from "./materialRequestInboundRecovery";
const id=(n:number)=>`22222222-2222-4222-8222-${String(n).padStart(12,"0")}`;
const base={v:1 as const,kind:"inbound-create" as const,trace:"web-inbound-trace-123456",key:null,person_id:id(1),authorization_version:2,request_id:id(3),expected_version:4,receipt_id:id(5),target_location_id:id(6),target_person_id:id(7)};
const storage=()=>{let value:string|null=null;return {getItem:()=>value,setItem:(_:string,v:string)=>{value=v},removeItem:()=>{value=null}}};
describe("inbound recovery",()=>{it("keeps create and post facts separate",()=>{const store=createInboundStore(storage());store.persist(base);expect(store.read()).toMatchObject({kind:"valid"});expect(()=>store.persist({...base,kind:"inbound-post",key:"web-inbound-key-123456",inbound_order_id:id(8)})).toThrow();store.clear(base.trace);});it("requires an order id for post recovery",()=>{expect(()=>validateInboundSentinel({...base,kind:"inbound-post",key:"web-inbound-key-123456"})).toThrow();});});

function recoveryFixture() {
  const original = { ...base, kind: "inbound-post" as const, key: "inbound-original-key-1234", inbound_order_id: id(8),
    person_id: identity().person_id, authorization_version: identity().authorization_version, request_id: REQUEST_ID, expected_version: 3 };
  const store = createInboundStore(storage()); store.persist(original);
  const row = { schema_version: "1.0", inbound_order_id: id(8), inbound_no: "INB-1", receipt_id: base.receipt_id,
    target_location_id: base.target_location_id, target_person_id: base.target_person_id, status: "posted", posting_transaction_id: id(9) };
  const adapter: any = { loadIdentityNoReplay: vi.fn(async () => identity()), loadAccessNoReplay: vi.fn(async () => access()),
    detailNoReplay: vi.fn(async () => approvedDetail()), listInboundOrders: vi.fn(async () => [row]) };
  return { original, store, row, adapter };
}
it("requires the exact original order even when another order uses the same receipt", async () => {
  const p = recoveryFixture(); p.adapter.listInboundOrders.mockResolvedValue([{ ...p.row, inbound_order_id: id(99) }]);
  await expect(recoverInbound(p.adapter, p.store, p.original)).rejects.toThrow(/唯一原入账/); expect(p.store.read().kind).toBe("valid");
});
it("retains the command when permission is revoked during readback", async () => {
  const p = recoveryFixture(); p.adapter.loadAccessNoReplay.mockResolvedValueOnce(access()).mockResolvedValueOnce({ ...access(), can_read: false, can_create: false, can_withdraw: false, can_cancel: false, can_read_allocation_options: false });
  await expect(recoverInbound(p.adapter, p.store, p.original)).rejects.toThrow(/核验期间/); expect(p.store.read().kind).toBe("valid");
});
it("retains the command if its page is no longer active", async () => {
  const p = recoveryFixture(); await expect(recoverInbound(p.adapter, p.store, p.original, () => false)).rejects.toThrow(/核验期间/);
  expect(p.store.read().kind).toBe("valid");
});
it("rejects posted without inventory transaction and duplicate matching rows", async () => {
  const p = recoveryFixture(); p.adapter.listInboundOrders.mockResolvedValueOnce([{ ...p.row, posting_transaction_id: null }]);
  await expect(recoverInbound(p.adapter, p.store, p.original)).rejects.toThrow(/库存事务/);
  p.adapter.listInboundOrders.mockResolvedValueOnce([p.row, p.row]);
  await expect(recoverInbound(p.adapter, p.store, p.original)).rejects.toThrow(/唯一原入账/); expect(p.store.read().kind).toBe("valid");
});
it("checks identity versions and separates create and post coordinates", () => {
  expect(() => validateInboundSentinel({ ...base, authorization_version: 0 })).toThrow();
  expect(() => validateInboundSentinel({ ...base, kind: "inbound-post", inbound_order_id: id(8), key: null })).toThrow();
  expect(() => validateInboundSentinel({ ...base, inbound_order_id: id(8) })).toThrow();
});
