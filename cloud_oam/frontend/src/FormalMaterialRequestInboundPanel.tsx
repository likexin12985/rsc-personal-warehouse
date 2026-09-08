import { useState } from "react";
import { mutationHeaders } from "./api";
import type { FormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { type InboundOrderResult, validateInboundOrderResult, validateInboundPostingResult } from "./materialRequestShipment";
import { Button, Field, showError } from "./ui";

type Props={adapter:FormalMaterialRequestAdapter;detail:MaterialRequestDetail|null};
export default function FormalMaterialRequestInboundPanel({adapter,detail}:Props){
 const [receipt,setReceipt]=useState(""),[location,setLocation]=useState(""),[person,setPerson]=useState(""),[order,setOrder]=useState<InboundOrderResult|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(""),[message,setMessage]=useState("");
 if(!detail)return null; const requestId=detail.request_id; const requestVersion=detail.request_version;
 async function create(){if(!adapter.createInboundOrder)return;setBusy(true);setError("");try{const h=mutationHeaders("material-request-inbound");const x=await adapter.createInboundOrder(requestId,{expected_request_version:requestVersion,receipt_id:receipt,target_location_id:location,target_person_id:person},{"X-Request-ID":new Headers(h.headers).get("X-Request-ID")!});setOrder(validateInboundOrderResult(x));setMessage(`已创建待入账单 ${x.inbound_no}`)}catch(e){setError(showError(e))}finally{setBusy(false)}}
 async function post(){if(!order||!adapter.postInboundOrder)return;setBusy(true);setError("");try{const h=new Headers(mutationHeaders("material-request-inbound-post").headers);const x=validateInboundPostingResult(await adapter.postInboundOrder(requestId,order.inbound_order_id,{"X-Request-ID":h.get("X-Request-ID")!,"Idempotency-Key":h.get("Idempotency-Key")!}));setMessage(`已完成个人仓入账，库存事务 ${x.inventory_transaction_id}`)}catch(e){setError(showError(e))}finally{setBusy(false)}}
 return <section className="opening-detail-section" aria-label="个人仓入账"><header><div><h3>个人仓入账</h3><p>收货验收与库存入账分开确认；先创建待入账单，再提交正式库存过账。</p></div></header>{error&&<div className="alert alert-error">{error}</div>}{message&&<div className="alert alert-info" role="status">{message}</div>}<div className="form-grid"><Field label="收货单 ID"><input aria-label="收货单 ID" value={receipt} onChange={e=>setReceipt(e.target.value)} /></Field><Field label="目标位置 ID"><input aria-label="目标位置 ID" value={location} onChange={e=>setLocation(e.target.value)} /></Field><Field label="目标人员 ID"><input aria-label="目标人员 ID" value={person} onChange={e=>setPerson(e.target.value)} /></Field></div><div><Button disabled={busy||!receipt||!location||!person} onClick={()=>void create()}>创建待入账单</Button>{order&&<Button disabled={busy} onClick={()=>void post()}>确认个人仓入账</Button>}</div></section>;
}
