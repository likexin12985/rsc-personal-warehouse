/** Correction-approved scrap uses verified history; a saved request is never replanned for recovery. */
import { canonical } from './formalLossReview';
import { createAdapter as createCorrectionAdapter, type Detail } from './lossCorrectionAdapter';
import { command, digest, id, object, prepare, verifyPending, type CorrectionSource, type Pending, type ScrapCommand } from './formalScrapCommands';
import { fail, type Kind } from './formalScrapFacts';
import type { Context, Transport } from './formalScrapRecovery';

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;
export type Preview = { planning_status: 'preview_only'; stock_effect: 'none'; operation_id: string;
  line_id: string; decision_id: string; predecessor_reversal_id: string; source_account_id: string;
  target_account_id: null; source_condition: string; quantity: string; serial_ids: string[];
  plan_hash: string; checked_at: string };
export type Prepared = { pending: Pending; preview: Preview; detail: Detail };
export type Adapter = Transport & { describe(root: string): Promise<Detail>;
  prepare(root: string, decision: string, reason: string, evidence: readonly string[]): Promise<Prepared> };
const base = '/v1/stock-operations/loss-reports/scraps/corrections';
const headers = { 'Cache-Control': 'no-store', Pragma: 'no-cache', 'Content-Type': 'application/json' };
function corrected(p: Pending): ScrapCommand & { source: CorrectionSource } {
  if (p.kind !== 'correction' || !('execution_reason' in p.original) || p.original.source.kind !== 'correction') fail();
  return p.original as ScrapCommand & { source: CorrectionSource };
}
function reference(detail: Detail, identifier: string): CorrectionSource {
  const s = detail.source, choice = s.approval_choices.find(c => c.correction_decision_id === id(identifier));
  const fact = s.history.find(h => h.kind === 'approval' && h.fact_id === identifier);
  if (s.chain_state !== 'awaiting_execution' || !s.approval_reference || !choice || choice.disposition !== 'scrap'
      || choice.execution_mode !== 'dedicated_flow_required' || !fact || fact.disposition !== 'scrap') fail();
  // These are only references returned in the same verified server snapshot.
  // The dedicated preview and COMMIT independently re-prove their relationship.
  return { kind: 'correction', ...s.approval_reference, correction_decision_id: identifier,
    expected_correction_decision_hash: fact.request_hash } as CorrectionSource;
}
function preview(value: unknown, detail: Detail, ref: CorrectionSource): Preview {
  const r = object(value, ['planning_status', 'stock_effect', 'operation_id', 'line_id', 'decision_id',
    'predecessor_reversal_id', 'source_account_id', 'target_account_id', 'source_condition', 'quantity',
    'serial_ids', 'plan_hash', 'checked_at']);
  const s = detail.source;
  id(r.source_account_id); digest(r.plan_hash);
  if (r.planning_status !== 'preview_only' || r.stock_effect !== 'none' || r.operation_id !== s.operation_id
      || r.line_id !== s.line_id || r.decision_id !== ref.correction_decision_id
      || r.predecessor_reversal_id !== ref.reversal_id || r.target_account_id !== null
      || r.source_condition !== detail.line.condition_code || r.quantity !== s.quantity
      || !Array.isArray(r.serial_ids) || r.serial_ids.length > 10000) fail();
  const serials = r.serial_ids.map(id);
  if (new Set(serials).size !== serials.length || canonical([...serials].sort()) !== canonical([...s.serial_ids].sort())) fail();
  if (typeof r.checked_at !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(r.checked_at)) fail();
  const at = Date.parse(r.checked_at);
  if (!Number.isFinite(at) || at > Date.now() + 30000 || at < Date.now() - 300000) fail();
  return { ...r, serial_ids: serials } as Preview;
}
export function createAdapter(personId: string, requestNoReplay: Requester): Adapter {
  const person = id(personId), corrections = createCorrectionAdapter(person, requestNoReplay);
  async function context(stage: Kind): Promise<Context> {
    if (stage !== 'correction') fail();
    const c = await corrections.context();
    return { ...c, kind: stage, can_write: c.can_write.executions };
  }
  async function stable(before: Context) { if (canonical(before) !== canonical(await context('correction'))) fail(); }
  async function plan(c: ScrapCommand, detail: Detail, ref: CorrectionSource) {
    const { request_id: _request, idempotency_key: _key, expected_plan_hash: _plan, ...body } = c;
    return preview(await requestNoReplay(base + '/preview', { method: 'POST', cache: 'no-store', headers,
      body: JSON.stringify(body) }), detail, ref);
  }
  async function post(value: Pending, suffix: string) {
    const p = await verifyPending(value), c = corrected(p);
    if (p.person_id !== person) fail();
    return requestNoReplay(base + suffix, { method: 'POST', cache: 'no-store',
      headers: { ...headers, 'X-Request-ID': c.request_id, 'Idempotency-Key': c.idempotency_key },
      body: JSON.stringify(suffix ? { operator_person_id: p.person_id, original: c } : c) });
  }
  return {
    context, describe: corrections.describe,
    async prepare(root, decision, reason, evidence) {
      const before = await context('correction'); if (!before.can_read || !before.can_write) fail();
      const detail = await corrections.describe(id(root));
      if (detail.source.authorization_version !== before.authorization_version) fail();
      const ref = reference(detail, decision);
      const c = command('correction', { source: ref, execution_reason: reason, evidence_file_ids: [...evidence],
        expected_plan_hash: '0'.repeat(64), request_id: crypto.randomUUID(), idempotency_key: crypto.randomUUID() }) as ScrapCommand;
      const result = await plan(c, detail, ref); await stable(before);
      return { pending: await prepare(person, before.authorization_version, 'correction', { ...c, expected_plan_hash: result.plan_hash }),
        preview: result, detail };
    },
    async verifySource(value, current) {
      const p = await verifyPending(value), c = corrected(p);
      if (p.person_id !== person || current.person_id !== person || current.kind !== 'correction') fail();
      const detail = await corrections.describe(c.source.root_disposition_id), ref = reference(detail, c.source.correction_decision_id);
      if (detail.source.authorization_version !== current.authorization_version || canonical(ref) !== canonical(c.source)) fail();
      const fresh = await plan(c, detail, ref); if (fresh.plan_hash !== c.expected_plan_hash) fail();
    },
    submit: p => post(p, ''), lookup: p => post(p, '/request-lookup'), seal: p => post(p, '/request-seal'),
  };
}
