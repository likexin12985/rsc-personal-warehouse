import quantityRegional from './test-fixtures/loss-review/contract-quantity-regional.json';
import quantityHeadquarters from './test-fixtures/loss-review/contract-quantity-headquarters.json';
import serialRegional from './test-fixtures/loss-review/contract-serial-regional.json';
import serialHeadquarters from './test-fixtures/loss-review/contract-serial-headquarters.json';
import { expect, it } from 'vitest';
import { detail, pending, queue, resolution, verifyCommand } from './formalLossReview';
const fixtures = {
  'quantity-regional': quantityRegional,
  'quantity-headquarters': quantityHeadquarters,
  'serial-regional': serialRegional,
  'serial-headquarters': serialHeadquarters,
};
for(const tracking of ['quantity','serial'] as const)for(const stage of ['regional','headquarters'] as const){
 it(`accepts actual Python ${tracking} ${stage} query, command hash and committed proof`,async()=>{
  const data=fixtures[`${tracking}-${stage}`];
  expect(queue(data.queue,data.identity,stage,'pending')).toEqual(data.queue);
  expect(detail(data.detail,data.identity,stage,data.pending.command.original.operation_id)).toEqual(data.detail.report);
  const original=pending(data.pending);expect(await verifyCommand(original.command)).toEqual(original.command);
  expect(resolution(data.found,original)).toEqual({status:'found'});
 });
}
