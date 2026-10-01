import type { Locks } from './lossReviewRecovery';
export class MemoryStorage {
  values=new Map<string,string>();get length(){return this.values.size;}key(n:number){return [...this.values.keys()][n]??null;}
  getItem(k:string){return this.values.get(k)??null;}setItem(k:string,v:string){this.values.set(k,v);}removeItem(k:string){this.values.delete(k);}
}
export function locks():Locks {
  const held=new Set<string>();return {async request(name,_options,callback){if(held.has(name))return callback(null);held.add(name);try{return await callback({name});}finally{held.delete(name);}}};
}
