import { expect, it } from 'vitest';
import { identity } from './formalLossReview';
import { fixtures, saved } from './lossCorrectionFixtures';
import { checkTarget, intentFor, pending, posted, requestHash, resolution, sources, verifyPending } from './lossCorrectionContracts';

it('requires both quantity and serial service exports for all three flows', () => {
  expect(fixtures.map(f => f.name).sort()).toEqual(['quantity.json:approvals', 'quantity.json:executions', 'quantity.json:inverses',
    'serial.json:approvals', 'serial.json:executions', 'serial.json:inverses']);
});

it.each(fixtures)('$name matches backend hash, facts and all recovery outcomes', async f => {
  const p = await saved(f);
  expect(await requestHash(p.command, p.flow)).toBe(f.data.request_hash);
  expect(await verifyPending(p)).toEqual(p);
  expect(posted(f.data.found.result, p)).toEqual(f.data.found.result);
  expect(resolution(f.data.found, p)).toEqual({ status: 'found' });
  expect(resolution(f.data.missing, p)).toEqual({ status: 'pending' });
  expect(resolution(f.data.sealed, await saved(f, true))).toEqual({ status: 'sealed' });
  expect(sources(f.data.after, identity(p), p.command.root_disposition_id).root_disposition_id).toBe(p.command.root_disposition_id);
  const changed = () => checkTarget(p, sources(f.data.after, identity(p), p.command.root_disposition_id));
  if (f.flow === 'approvals') expect(changed).not.toThrow();
  else expect(changed).toThrow();
});

it.each(fixtures)('$name never accepts foreign references or extra hidden fields', async f => {
  const p = await saved(f);
  for (const bad of [
    { ...f.data.source, write_authorization_provided: true },
    { ...f.data.source, command_jsonb: {} },
    { ...f.data.source, person_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' },
    { ...f.data.source, root_disposition_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' },
    { ...f.data.source, history: [] },
  ]) expect(() => sources(bad, identity(p), p.command.root_disposition_id)).toThrow();
  expect(() => pending({ ...p, request_hash: 'invalid' })).toThrow();
  await expect(verifyPending({ ...p, command: { ...p.command, reason: 'changed exact request' } })).rejects.toThrow();
  expect(() => resolution({ ...f.data.found, retry_allowed: true }, p)).toThrow();
  expect(() => resolution({ ...f.data.found, request_hash: 'f'.repeat(64) }, p)).toThrow();
  expect(() => resolution({ ...f.data.found, result: { ...f.data.found.result, actor_person_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' } }, p)).toThrow();
});

it.each(fixtures.filter(f => f.flow === 'executions'))('$name requires explicit decision selection', async f => {
  const p = await saved(f);
  expect(() => intentFor(p.source, 'executions', '按批准执行')).toThrow();
  expect(() => intentFor(p.source, 'executions', '按批准执行', 'ffffffff-ffff-4fff-8fff-ffffffffffff')).toThrow();
  expect(intentFor(p.source, 'executions', '按批准执行', p.command.correction_decision_id).correction_decision_id).toBe(p.command.correction_decision_id);
  expect(() => pending({ ...p, preview: { ...p.preview, correction_decision_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff' } })).toThrow();
});
