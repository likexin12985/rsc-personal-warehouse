import { expect, it } from 'vitest';
import fixture from './test-fixtures/stock-scrap/command-hashes.json';
import { command, pending, prepare, requestHash, target, verifyPending } from './formalScrapCommands';
import type { Kind } from './formalScrapFacts';

const samples=fixture.samples;
it.each(samples)('matches actual Python command hashing: $kind / $variant',async s=>{
  const k=s.kind as Kind,c=command(k,s.original);
  expect(c).toEqual(s.original);
  expect(await requestHash(k,c)).toBe(s.request_hash);
  const p=await prepare(s.person_id,s.authorization_version,k,c);
  expect(await verifyPending(JSON.parse(JSON.stringify(p)))).toEqual(p);
  expect(target(p)).toMatch(/^[a-f0-9-]{36}$/);
});
it.each(samples)('preserves the full saved command including private key: $kind / $variant',async s=>{
  const k=s.kind as Kind,c=command(k,s.original),p=await prepare(s.person_id,s.authorization_version,k,c);
  expect(p.original.idempotency_key).toBe(s.original.idempotency_key);
  expect(p.original).toEqual(s.original);
  expect(await requestHash(k,{...c,idempotency_key:'another-private-key'})).toBe(s.request_hash);
  await expect(verifyPending({...p,original:{...c,request_id:'another-request-id'}})).rejects.toThrow();
});
it.each(samples)('rejects stage substitution and user-supplied stock coordinates: $kind / $variant',async s=>{
  const k=s.kind as Kind;
  for(const other of ['original','correction','apply','regional','headquarters','execute'] as Kind[]){
    if(other!==k)expect(()=>command(other,s.original)).toThrow();
  }
  for(const extra of ['quantity','source_account_id','target_account_id','serial_ids','actor_user_id','permission'])
    expect(()=>command(k,{...s.original,[extra]:'forged'})).toThrow();
});
it('rejects corrupt saved identity, version, shape and digest',async()=>{
  const s=samples[0],p=await prepare(s.person_id,7,'original',command('original',s.original));
  for(const patch of [{v:2},{person_id:'00000000-0000-0000-0000-000000000000'},
    {authorization_version:0},{authorization_version:1.5},{request_hash:'invalid'},
    {retry_allowed:true},{original:{...p.original,evidence_file_ids:[]}}]){
    expect(()=>pending({...p,...patch})).toThrow();
  }
  await expect(verifyPending({...p,request_hash:'0'.repeat(64)})).rejects.toThrow();
});
it('does not change the saved evidence order while hashing canonical sorted evidence',async()=>{
  const s=samples[1],c=command('original',s.original);
  if(!('execution_reason' in c))throw new Error('wrong fixture');
  const reversed={...c,evidence_file_ids:[...c.evidence_file_ids].reverse()};
  expect(command('original',reversed)).toEqual(reversed);
  expect(await requestHash('original',reversed)).toBe(s.request_hash);
  expect(()=>command('original',{...c,evidence_file_ids:[c.evidence_file_ids[0],c.evidence_file_ids[0]]})).toThrow();
});
it.each(['', ' leading', 'trailing ', '\u0085trimmed', 'control\u0001', '\ud800', '🌟'.repeat(501)])(
  'refuses non-canonical or invalid reasons without silently changing an original command',value=>{
    expect(()=>command('original',{...samples[0].original,execution_reason:value})).toThrow();
  });
