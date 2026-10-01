import { identity } from './formalLossReview';
import { pending, requestHash, sources, type Command, type Flow, type Preview, type Sources } from './lossCorrectionContracts';
import type { Sources as OriginalSources } from './lossExecutionContracts';
type Fixture = { source: Sources; preview: Preview | null; original: Command; request_hash: string;
  missing: unknown; found: { result: Record<string, unknown> }; sealed_command: Command; sealed: unknown; after: Sources; origin: OriginalSources };
export const fixtures = Object.entries(import.meta.glob('./test-fixtures/loss-correction/*.json', { eager: true, import: 'default' }))
  .flatMap(([name, data]) => Object.entries(data as Record<Flow, Fixture>).map(([flow, data]) => ({
    name: `${name.split('/').at(-1)}:${flow}`, flow: flow as Flow, data,
  })));
export async function saved(f: typeof fixtures[number], sealed = false) {
  const s = sources(f.data.source, identity(f.data.source), f.data.original.root_disposition_id);
  const command = sealed ? f.data.sealed_command : f.data.original;
  return pending({ v: 1, ...identity(s), flow: f.flow, source: s, preview: f.data.preview, command, request_hash: await requestHash(command, f.flow) });
}
