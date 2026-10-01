import { identity } from './formalLossReview';
import { pending, requestHash, sources, type Command, type Preview, type Sources } from './lossExecutionContracts';
type Fixture={source:Sources;preview:Preview;original:Command;missing:unknown;sealed_command:Command;sealed:unknown;found:{disposition:Record<string,unknown>};after:Sources};
export const fixtures=Object.entries(import.meta.glob('./test-fixtures/loss-execution/*.json',{eager:true,import:'default'})).map(([name,data])=>({name:name.split('/').at(-1)!,data:data as Fixture}));
export const fixture=fixtures.find(f=>f.name==='quantity-convert_used.json')!.data;
export async function saved(f:Fixture=fixture,sealed=false){
  const s=sources(f.source,identity(f.source),f.source.report.operation_id),d=s.decisions[0],c=sealed?f.sealed_command:f.original;
  return pending({v:1,...identity(s),flow:d.disposition==='return_to_region'?'return':'disposition',operation_id:s.report.operation_id,operation_no:s.report.operation_no,line_id:d.line_id,material_name:s.report.lines[0].material_name,disposition:d.disposition,preview:f.preview,command:c,request_hash:await requestHash(c)});
}
