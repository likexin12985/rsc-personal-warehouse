import { expect, it } from 'vitest';
import fixtures from './test-fixtures/return-condition/command-hashes.json';
import { command, originalInputHash, prepare, verifyPending } from './returnConditionCommands';
it.each(fixtures.samples)('$kind matches the actual Python original-input digest without reordering the saved command', async sample => {
  const value = command(sample.original), original = structuredClone(value);
  expect(await originalInputHash(value)).toBe(sample.original_input_hash);
  expect(value).toEqual(original);
  const p = await prepare(sample.person_id, sample.authorization_version, sample.inbound_line_id, value);
  expect(p.original_input_hash).toBe(sample.original_input_hash);
  expect(p.original.evidence_file_ids).toEqual(sample.original.evidence_file_ids);
  await expect(verifyPending({ ...p, original: { ...p.original, reason: '篡改后的意见' } })).rejects.toThrow();
  expect(await originalInputHash({ ...value, idempotency_key: 'another-original-key' })).not.toBe(sample.original_input_hash);
});
it.each(['submit', 'supplement', 'verify_region'])('%s needs this action’s evidence', kind => {
  const sample = fixtures.samples.find(s => s.kind === kind)!;
  expect(() => command({ ...sample.original, evidence_file_ids: [] })).toThrow();
});
it('rejects input truncation, unsupported fields, and inaccurate numeric quantities', () => {
  const value = fixtures.samples.find(s => s.kind === 'submit')!.original;
  for (const quantity of [2, 0, '0', '-1', '1e2', '0.0001', '1.0000']) expect(() => command({ ...value, quantity })).toThrow();
  expect(() => command({ ...value, evidence_file_ids: [value.evidence_file_ids[0], value.evidence_file_ids[0]] })).toThrow();
  expect(() => command({ ...value, approval: true })).toThrow();
});
