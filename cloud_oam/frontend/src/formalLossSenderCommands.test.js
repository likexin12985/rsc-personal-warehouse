import {test} from 'vitest';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {detail,outboundOptions,shipmentOptions} from './formalLossSenderReads';
import {command,input,lookup,preview,requestHash,result} from './formalLossSenderCommands';
const UUID='11111111-2222-4333-8444-555555555555';
for(const tracking of ['quantity','serial'])for(const kind of ['outbound_return','ship_return']){
 const name=`${tracking}-${kind}`;
 const fixture=()=>JSON.parse(readFileSync(new URL(`./test-fixtures/loss-sender/h5-write-contracts/${name}.json`,import.meta.url),'utf8'));
 async function prepare(f){
  const d=detail(f.detail,f.identity,f.detail.origin.operation_id);
  const options=(kind==='outbound_return'?outboundOptions:shipmentOptions)(f.options,f.identity,d);
  const c=command(kind,f.command,f.identity.person_id);
  const {expected_plan_hash,request_id,idempotency_key,...body}=c;
  return {d,options,c,body,p:await preview(kind,f.preview,body,options,d)};
 }
 test(`${name}: canonical request and actual HTTP preview/result/recovery agree`,async()=>{
  const f=fixture(),{d,c,body,p}=await prepare(f);
  assert.deepEqual(p,f.preview);
  assert.equal(await requestHash(kind,d.origin.operation_id,body,f.identity.person_id),f.preview.request_hash);
  assert.deepEqual(await result(kind,f.result,c,p),f.result);
  const found=await lookup(kind,{lookup_status:'found',retry_allowed:false,operation_type:kind,result:f.result},c,p);
  assert.equal(found.status,'found');assert.deepEqual(found.result,f.result);
  assert.deepEqual(await lookup(kind,{lookup_status:'not_observed',retry_allowed:false},c,p),{status:'unknown',retry_allowed:false});
  const closed=await lookup(kind,f.seal,command(kind,f.missingCommand,f.identity.person_id),p);
  assert.equal(closed.status,'sealed');assert.equal(closed.seal.seal_scope,'actor_request_id');
  assert.equal('idempotency_key' in closed.seal,false);assert.equal('plan_hash' in closed.seal,false);
 });
 test(`${name}: malformed command and substitution are rejected before a network write`,async()=>{
  const f=fixture(),{d,options,body}=await prepare(f);
  for(const mutate of [
   x=>x.operator_person_id=UUID,x=>x.work_order_id=UUID,x=>x.expected_plan_hash='0',
   x=>x.request_id='short',x=>x.idempotency_key='with a space',x=>x.lines[0].quantity=1.01,
   x=>x.lines[0].quantity='0.000',x=>x.lines[0].quantity='1.0001',x=>x.lines.push(structuredClone(x.lines[0])),
  ]){const value=structuredClone(f.command);mutate(value);assert.throws(()=>command(kind,value,f.identity.person_id));}
  for(const mutate of [
   x=>x.operator_person_id=UUID,x=>x.request_hash='0'.repeat(64),x=>x.lines[0].selected_quantity='0.500',
   x=>x.lines[0].source_loss_line_id=UUID,x=>x.lines[0].source_recovery_line_id=UUID,
   x=>x.origin.requester_id=UUID,x=>x.destination.target_location_id=UUID,x=>x.plan_hash='invalid',
   x=>x.lines.push(structuredClone(x.lines[0])),
  ]){const value=structuredClone(f.preview);mutate(value);await assert.rejects(()=>preview(kind,value,body,options,d));}
  if(tracking==='serial'){
   const b=structuredClone(body);
   if(kind==='outbound_return')b.lines[0].serial_verifications[0].serial_no='WRONG';
   else b.lines[0].serial_ids[0]=UUID;
   await assert.rejects(()=>preview(kind,f.preview,b,options,d));
  }
 });
 test(`${name}: conflicting found/sealed outcomes never clear an original request`,async()=>{
  const f=fixture(),{c,p}=await prepare(f);
  for(const mutate of [
   x=>x.request_id='different-request',x=>x.plan_hash='0'.repeat(64),x=>x.lines[0].selected_quantity='0.001',
   x=>x.origin.disposition_id=UUID,x=>x.status='accepted',x=>x.recorded_at='2000-01-01T00:00:00Z',
   x=>x.work_order_id=UUID,
  ]){const value=structuredClone(f.result);mutate(value);await assert.rejects(()=>result(kind,value,c,p));}
  await assert.rejects(()=>lookup(kind,{lookup_status:'not_observed',retry_allowed:true},c,p));
  await assert.rejects(()=>lookup(kind,{lookup_status:'found',retry_allowed:false,operation_type:kind==='ship_return'?'outbound_return':'ship_return',result:f.result},c,p));
  const missing=command(kind,f.missingCommand,f.identity.person_id);
  for(const mutate of [
   x=>x.seal.seal_scope='idempotency_key',x=>x.seal.operation_id=UUID,x=>x.seal.request_hash='0'.repeat(64),
   x=>x.seal.operator_person_id=UUID,x=>x.seal.request_id='different-request',x=>x.seal.source_loss_disposition_id=UUID,
   x=>x.seal.idempotency_key=missing.idempotency_key,x=>x.seal.plan_hash=p.plan_hash,
  ]){const value=structuredClone(f.seal);mutate(value);await assert.rejects(()=>lookup(kind,value,missing,p));}
 });
}
