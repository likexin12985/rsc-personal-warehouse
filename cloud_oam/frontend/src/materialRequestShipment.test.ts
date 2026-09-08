import { describe, expect, it } from "vitest";
import { validateShipmentInput, validateShipmentResult } from "./materialRequestShipment";
const id=(n:number)=>`11111111-1111-1111-1111-${n.toString().padStart(12,"0")}`;
describe("shipment contract",()=>{
 it("accepts strict package input",()=>{const x=validateShipmentInput({expected_request_version:3,target_location_id:id(1),target_person_id:null,carrier:"人工承运",tracking_no:"SF-1",shipped_at:"2026-09-09T10:00:00+08:00",lines:[{outbound_posting_id:id(2),shipped_qty:"1.000",serial_ids:[]}]});expect(x.lines[0].shipped_qty).toBe("1.000")});
 it("rejects duplicate posting and unknown result fields",()=>{expect(()=>validateShipmentInput({expected_request_version:1,target_location_id:id(1),target_person_id:null,carrier:"x",tracking_no:"x",shipped_at:"2026-09-09T10:00:00Z",lines:[{outbound_posting_id:id(2),shipped_qty:"1.000",serial_ids:[]},{outbound_posting_id:id(2),shipped_qty:"1.000",serial_ids:[]}]})).toThrow();expect(()=>validateShipmentResult({schema_version:"1.0",shipment_id:id(1),shipment_no:"SHP-1",request_id:id(2),status:"shipped",carrier:"x",tracking_no:"x",shipped_at:"2026-09-09T10:00:00Z",lines:[],idempotency_replayed:false,extra:true})).toThrow()});
});
