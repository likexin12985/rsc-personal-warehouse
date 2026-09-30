import {test} from 'vitest';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { directory, detail, outboundOptions, shipmentOptions } from './formalLossSenderReads';
const UUID='11111111-2222-4333-8444-555555555555';
for (const tracking of ['quantity','serial']) for(const phase of ['pending_departure','departed_not_shipped']) {
  const name=`${tracking}-${phase}`;
  const fixture=()=>JSON.parse(readFileSync(new URL(`./test-fixtures/loss-sender/h5-read-contracts/${name}.json`,import.meta.url),'utf8'));
  test(`${name}: actual HTTP contracts keep distinct physical stages`,()=>{
    const f=fixture();assert.equal(f.synthetic,true);
    const d=detail(f.detail,f.identity,f.detail.origin.operation_id);
    const page=directory(f.directory,f.identity);
    const out=outboundOptions(f.outbound_options,f.identity,d),ship=shipmentOptions(f.shipment_options,f.identity,d);
    assert.deepEqual(d,f.detail);assert.deepEqual(page,f.directory);
    assert.deepEqual(out,f.outbound_options);assert.deepEqual(ship,f.shipment_options);
    assert.equal(out.lines[0].selectable_quantity,phase==='pending_departure'?'1.000':'0.000');
    assert.equal(ship.lines.length,phase==='pending_departure'?0:1);
    if(ship.lines.length)assert.equal(ship.lines[0].shipped_quantity,'0.000');
    assert.equal(d.line.selected_serials.length,tracking==='serial'?1:0);
  });
  test(`${name}: wrong identity, mixed origins, raw QR and inconsistent quantities are rejected`,()=>{
    const f=fixture(),d=detail(f.detail,f.identity,f.detail.origin.operation_id);
    assert.throws(()=>detail(f.detail,{...f.identity,person_id:UUID},d.origin.operation_id));
    assert.throws(()=>detail(f.detail,f.identity,UUID));
    for(const mutate of [
      x=>x.work_order_id=UUID,
      x=>x.origin.loss_line_id=UUID,
      x=>x.line.source_recovery_line_id=UUID,
      x=>x.line.selected_serials.push({serial_id:UUID,serial_no:'FORGED',qr_code:'PRIVATE'}),
      x=>x.line.return_quantity='0.000',
      x=>x.loss_submitted_at='2099-01-01T00:00:00Z',
    ]){const copy=structuredClone(f.detail);mutate(copy);assert.throws(()=>detail(copy,f.identity,d.origin.operation_id));}
    for(const mutate of [
      x=>x.lines[0].selectable_quantity='9.000',
      x=>x.lines[0].departed_quantity='9.000',
      x=>x.lines[0].remaining_quantity='-1.000',
      x=>x.lines[0].operation_line_id=UUID,
      x=>x.lines[0].source_recovery_line_id=UUID,
      x=>x.lines[0].serials.push({serial_id:UUID,serial_no:'FORGED'}),
      x=>x.destination.source_location_id=UUID,
      x=>x.origin.request_hash='0'.repeat(64),
      x=>x.lines.push(structuredClone(x.lines[0])),
    ]){const copy=structuredClone(f.outbound_options);mutate(copy);assert.throws(()=>outboundOptions(copy,f.identity,d));}
    if(f.shipment_options.lines.length)for(const mutate of [
      x=>x.lines[0].shipped_quantity='9.000',
      x=>x.lines[0].unassigned_quantity='9.000',
      x=>x.lines[0].selectable_quantity='9.000',
      x=>x.lines[0].outbound_at='2099-01-01T00:00:00Z',
      x=>x.lines.push(structuredClone(x.lines[0])),
    ]){const copy=structuredClone(f.shipment_options);mutate(copy);assert.throws(()=>shipmentOptions(copy,f.identity,d));}
  });
  test(`${name}: directory pagination binds snapshot and refuses duplicates`,()=>{
    const f=fixture(),p=f.directory;
    assert.throws(()=>directory(p,f.identity,0));
    assert.throws(()=>directory(p,f.identity,51));
    assert.throws(()=>directory(p,f.identity,20,null,'0'.repeat(64)));
    assert.throws(()=>directory(p,f.identity,20,UUID));
    assert.throws(()=>directory({...p,items:[p.items[0],p.items[0]]},f.identity));
    assert.throws(()=>directory({...p,next_after_id:UUID},f.identity));
    assert.throws(()=>directory({...p,items:[{...p.items[0],work_order_id:UUID}]},f.identity));
  });
}

test('quantity budgets use exact decimal units and current scale, including values above JS safe integers',()=>{
  const f=JSON.parse(readFileSync(new URL('./test-fixtures/loss-sender/h5-read-contracts/quantity-pending_departure.json',import.meta.url),'utf8'));
  const d=detail(f.detail,f.identity,f.detail.origin.operation_id);
  for(const [scale,fraction,held,selected] of [[3,true,'0.199','0.199'],[2,true,'0.199','0.190'],[3,false,'0.999','0.000']]){
    const wire=structuredClone(f.outbound_options);
    Object.assign(wire.lines[0],{quantity_scale:scale,allow_fraction:fraction,held_quantity:held,selectable_quantity:selected});
    assert.equal(outboundOptions(wire,f.identity,d).lines[0].selectable_quantity,selected);
    wire.lines[0].selectable_quantity='1.000';assert.throws(()=>outboundOptions(wire,f.identity,d));
  }
  const giant='999999999999999.999',wire=structuredClone(f.outbound_options),original=structuredClone(f.detail);
  original.line.return_quantity=giant;
  Object.assign(wire.lines[0],{quantity_scale:3,allow_fraction:true,return_quantity:giant,remaining_quantity:giant,held_quantity:giant,selectable_quantity:giant});
  const huge=detail(original,f.identity,original.origin.operation_id);
  assert.equal(outboundOptions(wire,f.identity,huge).lines[0].selectable_quantity,giant);
  wire.lines[0].departed_quantity='0.001';assert.throws(()=>outboundOptions(wire,f.identity,huge));
});

test('same-size replacement of an original SN is rejected in outbound and shipment options',()=>{
  for(const [phase,field,parse] of [['pending_departure','outbound_options',outboundOptions],['departed_not_shipped','shipment_options',shipmentOptions]]){
    const f=JSON.parse(readFileSync(new URL(`./test-fixtures/loss-sender/h5-read-contracts/serial-${phase}.json`,import.meta.url),'utf8'));
    const d=detail(f.detail,f.identity,f.detail.origin.operation_id),wire=structuredClone(f[field]);
    wire.lines[0].serials[0].serial_id=UUID;
    assert.throws(()=>parse(wire,f.identity,d));
    wire.lines[0].serials[0].serial_id=f[field].lines[0].serials[0].serial_id;
    wire.lines[0].serials[0].serial_no='OTHER-SN';assert.throws(()=>parse(wire,f.identity,d));
  }
});
