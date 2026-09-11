import { describe, expect, it } from "vitest";
import { createReceiptStore, receiptRequestHash, validateReceiptSentinel } from "./materialRequestReceiptRecovery";
const id=(n:number)=>`11111111-1111-4111-8111-${String(n).padStart(12,"0")}`;
const input={expected_request_version:2,receiver_person_id:id(1),received_at:"2026-09-11T10:00:00Z",lines:[{shipment_line_id:id(2),accepted_qty:"1.000",rejected_qty:"0.000",condition:"normal",serial_ids:[],exception_evidence_file_id:null}]} as const;
const sentinel={v:1 as const,kind:"receipt" as const,trace:"web-receipt-trace-123456",key:"web-receipt-key-123456",person_id:id(3),authorization_version:7,request_id:id(4),input};
const storage=()=>{let value:string|null=null;return {getItem:()=>value,setItem:(_:string,v:string)=>{value=v},removeItem:()=>{value=null}}};
describe("receipt recovery",()=>{
 it("persists and clears only the exact trace",()=>{const store=createReceiptStore(storage());store.persist(sentinel);expect(store.read()).toMatchObject({kind:"valid"});expect(()=>store.clear("other-trace-123456")).toThrow();store.clear(sentinel.trace);expect(store.read()).toEqual({kind:"missing"});});
 it("rejects a malformed or second pending sentinel",()=>{expect(()=>validateReceiptSentinel({...sentinel,input:{...input,lines:[]}})).toThrow();const store=createReceiptStore(storage());store.persist(sentinel);expect(()=>store.persist({...sentinel,trace:"web-receipt-trace-999999"})).toThrow();});
 it("matches the server canonical request hash contract",async()=>{const hash=await receiptRequestHash(id(4),input);expect(hash).toBe("4e546fd26f8e5e98d2a748640b6a8dfb993b432baa8d47e03c76abba1301bb59");});
});
