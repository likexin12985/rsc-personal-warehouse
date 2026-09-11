import { describe, expect, it } from "vitest";
import { createInboundStore, validateInboundSentinel } from "./materialRequestInboundRecovery";
const id=(n:number)=>`22222222-2222-4222-8222-${String(n).padStart(12,"0")}`;
const base={v:1 as const,kind:"inbound-create" as const,trace:"web-inbound-trace-123456",key:null,person_id:id(1),authorization_version:2,request_id:id(3),expected_version:4,receipt_id:id(5),target_location_id:id(6),target_person_id:id(7)};
const storage=()=>{let value:string|null=null;return {getItem:()=>value,setItem:(_:string,v:string)=>{value=v},removeItem:()=>{value=null}}};
describe("inbound recovery",()=>{it("keeps create and post facts separate",()=>{const store=createInboundStore(storage());store.persist(base);expect(store.read()).toMatchObject({kind:"valid"});expect(()=>store.persist({...base,kind:"inbound-post",key:"web-inbound-key-123456",inbound_order_id:id(8)})).toThrow();store.clear(base.trace);});it("requires an order id for post recovery",()=>{expect(()=>validateInboundSentinel({...base,kind:"inbound-post",key:"web-inbound-key-123456"})).toThrow();});});
