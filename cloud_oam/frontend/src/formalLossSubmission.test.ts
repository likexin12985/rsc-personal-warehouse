import { beforeAll, expect, it, vi } from 'vitest';
import q from './test-fixtures/loss-submission/loss-submission-quantity-found.json';
import qs from './test-fixtures/loss-submission/loss-submission-quantity-sealed.json';
import s from './test-fixtures/loss-submission/loss-submission-serial-found.json';
import ss from './test-fixtures/loss-submission/loss-submission-serial-sealed.json';
import * as contract from './formalLossSubmission';
beforeAll(async()=>{const {webcrypto}=await vi.importActual<{webcrypto:Crypto}>('node:crypto');vi.stubGlobal('crypto',webcrypto);});
const fixtures=[q,qs,s,ss];
it.each(fixtures)('verifies current backend source, intent, preview and outcome %s',async f=>{
 const source=contract.sources(f.sources,f.identity),body=contract.input(f.preview_input,f.identity.person_id),command=contract.command(f.command,f.identity.person_id);
 expect(await contract.requestHash(body)).toBe(f.preview.request_hash);
 const selected=contract.selectionInput(f.selection_input,f.identity.person_id);
 await contract.selection(f.selection,selected,source);
 const prepared=await contract.preview(f.preview,body,source);
 expect(contract.lookupInput(command,prepared.request_hash)).toEqual(f.lookup_input);
 expect(await contract.lookup(f.missing,command,prepared)).toEqual({status:'unknown'});
 expect((await contract.lookup(f.observed,command,prepared)).status).toBe(f.observed.lookup_status==='found'?'submitted':'sealed');
});
it.each(['person_id','location_id','authorization_version'] as const)('blocks source identity change %s',field=>{const v=structuredClone(q.sources);Object.assign(v,{[field]:field==='authorization_version'?99:'11111111-1111-4111-8111-111111111111'});expect(()=>contract.sources(v,q.identity)).toThrow();});
it.each([null,1,1.2,true,'-1','0','0.000','1e1','1.0001','1000000000000000'])('rejects invalid input quantity %s',quantity=>{const v=structuredClone(q.preview_input);Object.assign(v.lines[0],{quantity});expect(()=>contract.input(v,q.identity.person_id)).toThrow();});
it.each(['stock_account_id','sku_code','quantity','custodian_person_id'] as const)('rejects altered preview source %s',async field=>{const source=contract.sources(q.sources,q.identity),body=contract.input(q.preview_input,q.identity.person_id),v=structuredClone(q.preview);Object.assign(v.lines[0].source,{[field]:field==='quantity'?'3.000':'11111111-1111-4111-8111-111111111111'});await expect(contract.preview(v,body,source)).rejects.toThrow();});
it('requires serial count and scanned SKU to match source',()=>{const source=contract.sources(s.sources,s.identity),body=contract.input(s.preview_input,s.identity.person_id);expect(()=>contract.checkSelection({...body,lines:[{...body.lines[0],quantity:'2.000'}]},source)).toThrow();body.lines[0].serial_verifications[0].sku_code='OTHER';expect(()=>contract.checkSelection(body,source)).toThrow();});
it('unknown and corrupt outcomes never authorize a retry or clear original evidence',async()=>{const source=contract.sources(q.sources,q.identity),body=contract.input(q.preview_input,q.identity.person_id),prepared=await contract.preview(q.preview,body,source),original=contract.command(q.command,q.identity.person_id);for(const value of [{lookup_status:'not_found',retry_permitted:true},{lookup_status:'missing',retry_permitted:false},{lookup_status:'found',retry_permitted:false,submission:{...q.result,posting_transaction_id:null}},{lookup_status:'found',retry_permitted:false,submission:{...q.result,request_id:'different-request'}}])await expect(contract.lookup(value,original,prepared)).rejects.toThrow();});
it('rejects evidence substitution and duplicate account selection',async()=>{const source=contract.sources(q.sources,q.identity),body=contract.input(q.preview_input,q.identity.person_id),v=structuredClone(q.preview);v.evidence[0].file_id='11111111-1111-4111-8111-111111111111';await expect(contract.preview(v,body,source)).rejects.toThrow();expect(()=>contract.input({...q.preview_input,lines:[...q.preview_input.lines,...q.preview_input.lines]},q.identity.person_id)).toThrow();});

it('accepts backend SN and QR text limits while rejecting longer proofs', () => {
 const v=structuredClone(s.preview_input);v.lines[0].serial_verifications[0].serial_no='s'.repeat(200);v.lines[0].serial_verifications[0].qr_code='q'.repeat(250);
 expect(contract.input(v,s.identity.person_id).lines[0].serial_verifications[0].serial_no.length).toBe(200);
 v.lines[0].serial_verifications[0].serial_no+='s';expect(()=>contract.input(v,s.identity.person_id)).toThrow();
});

import serialOptionsFixture from './test-fixtures/loss-submission/loss-source-serial-options.json';
it('validates current backend SN options without exposing QR proof', () => {
 const f=serialOptionsFixture, current=contract.sources(f.sources,f.identity), account=current.items[0];
 const page=contract.serialOptions(f.page,current,account.stock_account_id);
 expect(page.items.map(s=>s.serial_id)).toEqual(f.page.items.map(s=>s.serial_id));
 expect(page.items.every(s=>!Object.hasOwn(s,'qr_code'))).toBe(true);
});
it.each(['person_id','stock_account_id','material_id','ledger_cursor','total_serials','projection_status','opening_balance_status'] as const)('rejects stale or foreign SN evidence %s', field => {
 const f=serialOptionsFixture,current=contract.sources(f.sources,f.identity),v=structuredClone(f.page);
 Object.assign(v,{[field]:field==='ledger_cursor'||field==='total_serials'?999:'invalid'});
 expect(()=>contract.serialOptions(v,current,current.items[0].stock_account_id)).toThrow();
});
it('rejects incomplete, inactive, overlapping and mismatched exact SN query results', () => {
 const f=serialOptionsFixture,current=contract.sources(f.sources,f.identity),key=current.items[0].stock_account_id;
 expect(()=>contract.serialOptions({...f.page,items:[]},current,key)).toThrow();
 expect(()=>contract.serialOptions({...f.page,items:[{...f.page.items[0],lifecycle_status:'lost'}]},current,key)).toThrow();
 expect(()=>contract.serialOptions(f.page,current,key,f.page.items[0].serial_id)).toThrow();
 expect(()=>contract.serialOptions(f.page,current,key,null,'WRONG-SN')).toThrow();
});
