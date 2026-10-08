import { type InboundDetail, exact, id, hash, time, text, units, quantity, demand, canonical } from "./myFulfillmentContract";
import { validateCloseInput } from "./materialRequestClosure";
export type RejectionInput = { expected_request_version: number; reason: string; receipt_id: string; receipt_line_id: string; receipt_request_hash: string; quantity: string; serial_ids: string[] };
export type ProgressAction = "cancel_registration" | "depart" | "handover";
export type ProgressInput = { expected_request_version: number; reason: string; action: ProgressAction; registration_request_hash: string;
  previous_event_id: string | null; previous_request_hash: string | null; physical_at: string | null; carrier: string | null; tracking_no: string | null };
export type RejectionResult = Omit<RejectionInput, "expected_request_version" | "reason"> & { schema_version: "1.0"; return_id: string; return_no: string;
  request_id: string; request_version: number; status: "registered"; registered_at: string; request_hash: string; replayed: boolean };
export type ProgressResult = Omit<ProgressInput, "expected_request_version"> & { event_id: string; return_id: string; request_id: string;
  request_version: number; recorded_at: string; request_hash: string; replayed: boolean };
export type ProgressState = { return_id: string; request_id: string; registration_request_hash: string; status: "registered" | "cancelled" | "departed" | "handed_over"; events: ProgressResult[] };
export type RejectionHistory = { registration: RejectionResult; progress: ProgressState; permitted_actions: ProgressAction[] };
export type RejectionLine = { receipt_line_id: string; available_qty: string; available_serials: { serial_id: string; serial_no: string }[]; register_permitted: boolean; registrations: RejectionHistory[] };
export type RejectionSource = { receipt_id: string; status: "verified" | "blocked"; message: string; detail: InboundDetail | null; lines: RejectionLine[] };
export type RejectionPage = { schema_version: "1.0"; request_id: string; request_version: number; items: RejectionSource[]; next_after_id: string | null };
export type RejectionCommand = { kind: "register"; input: RejectionInput } | { kind: "progress"; return_id: string; input: ProgressInput };
export type RejectionStatus = { lookup_status: "confirmed" | "not_observed"; command: RejectionResult | ProgressResult | null };
const inputKeys = "expected_request_version reason receipt_id receipt_line_id receipt_request_hash quantity serial_ids";
const progressKeys = "expected_request_version reason action registration_request_hash previous_event_id previous_request_hash physical_at carrier tracking_no";
function version(value: number) { demand(Number.isSafeInteger(value) && value >= 0, "需求版本无效"); }
function uniqueIds(values: string[]) { demand(new Set(values).size === values.length, "记录重复"); values.forEach(id); }
function array(value: unknown, max: number): asserts value is unknown[] { demand(Array.isArray(value) && value.length <= max, "退回记录数量超限或不完整"); }
export function rejectionInput(value: unknown): RejectionInput {
  const r = exact<RejectionInput>(value, inputKeys); validateCloseInput({ expected_request_version: r.expected_request_version, reason: r.reason });
  id(r.receipt_id); id(r.receipt_line_id); hash(r.receipt_request_hash); demand(units(r.quantity) > 0n); array(r.serial_ids, 1000); uniqueIds(r.serial_ids);
  return { ...r, quantity: quantity(r.quantity), serial_ids: [...r.serial_ids].sort() };
}
// Match the server's timezone normalization and six-digit nonzero microseconds.
export function progressTime(value: string): string {
  time(value); const utc = new Date(value).toISOString();
  const micros = (value.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
  return utc.replace(/\.\d{3}Z$/, micros === "000000" ? "Z" : `.${micros}Z`);
}
export function progressInput(value: unknown): ProgressInput {
  const r = exact<ProgressInput>(value, progressKeys); validateCloseInput({ expected_request_version: r.expected_request_version, reason: r.reason });
  demand(["cancel_registration", "depart", "handover"].includes(r.action)); hash(r.registration_request_hash);
  demand((r.previous_event_id === null) === (r.previous_request_hash === null));
  if (r.previous_event_id !== null) { id(r.previous_event_id); hash(r.previous_request_hash); }
  if (r.action === "handover") {
    demand(r.previous_event_id !== null && r.carrier !== null && r.tracking_no !== null);
    for (const s of [r.carrier, r.tracking_no]) demand(text(s) === s.trim() && s.length <= 100);
  } else demand(r.previous_event_id === null && r.carrier === null && r.tracking_no === null);
  demand((r.action === "cancel_registration") === (r.physical_at === null));
  return { ...r, physical_at: r.physical_at === null ? null : progressTime(r.physical_at) };
}
export function rejectionResult(value: unknown, requestId: string): RejectionResult {
  const r = exact<RejectionResult>(value, "schema_version return_id return_no request_id request_version receipt_id receipt_line_id receipt_request_hash quantity serial_ids status registered_at request_hash replayed");
  demand(r.schema_version === "1.0" && r.status === "registered" && id(r.request_id) === requestId && typeof r.replayed === "boolean");
  id(r.return_id); text(r.return_no); time(r.registered_at); hash(r.request_hash); version(r.request_version);
  id(r.receipt_id); id(r.receipt_line_id); hash(r.receipt_request_hash); demand(units(r.quantity) > 0n && quantity(r.quantity) === r.quantity);
  array(r.serial_ids, 1000); uniqueIds(r.serial_ids); demand(canonical([...r.serial_ids].sort()) === canonical(r.serial_ids)); return r;
}
export function progressResult(value: unknown, requestId: string, returnId: string): ProgressResult {
  const r = exact<ProgressResult>(value, "event_id return_id request_id request_version action registration_request_hash previous_event_id previous_request_hash physical_at recorded_at carrier tracking_no reason request_hash replayed");
  demand(id(r.request_id) === requestId && id(r.return_id) === returnId && typeof r.replayed === "boolean");
  id(r.event_id); version(r.request_version); time(r.recorded_at); hash(r.request_hash);
  const { event_id: _e, return_id: _r, request_id: _q, request_version: v, recorded_at: _t, request_hash: _h, replayed: _p, ...input } = r;
  progressInput({ ...input, expected_request_version: v });
  if (r.physical_at) demand(Date.parse(r.physical_at) <= Date.parse(r.recorded_at)); return r;
}
export function progressState(value: unknown, requestId: string, original: RejectionResult): ProgressState {
  const r = exact<ProgressState>(value, "return_id request_id registration_request_hash status events");
  demand(r.return_id === original.return_id && r.request_id === requestId && r.registration_request_hash === original.request_hash);
  array(r.events, 2); uniqueIds(r.events.map(e => e.event_id));
  r.events.forEach((e, i) => {
    progressResult(e, requestId, original.return_id); demand(e.registration_request_hash === original.request_hash && e.request_version >= original.request_version);
    if (i === 0) demand(["cancel_registration", "depart"].includes(e.action));
    else demand(r.events[0].action === "depart" && e.action === "handover" && e.previous_event_id === r.events[0].event_id && e.previous_request_hash === r.events[0].request_hash);
    if (e.physical_at) demand(Date.parse(e.physical_at) >= Date.parse(i ? r.events[i - 1].physical_at! : original.registered_at));
  });
  const last = r.events.at(-1)?.action;
  demand(r.status === (last === undefined ? "registered" : { cancel_registration: "cancelled", depart: "departed", handover: "handed_over" }[last])); return r;
}
export function rejectionPage(value: unknown, requestId: string, after: string | null = null): RejectionPage {
  const p = exact<RejectionPage>(value, "schema_version request_id request_version items next_after_id");
  demand(p.schema_version === "1.0" && id(p.request_id) === requestId); version(p.request_version); array(p.items, 20); uniqueIds(p.items.map(s => s.receipt_id));
  let last = after;
  for (const s of p.items) {
    exact(s, "receipt_id status message detail lines"); text(s.message); demand(last === null || s.receipt_id > last); last = s.receipt_id;
    array(s.lines, 100); uniqueIds(s.lines.map(l => l.receipt_line_id));
    if (s.status === "blocked") { demand(s.detail === null && s.lines.length === 0); continue; }
    demand(s.status === "verified" && s.detail !== null); const d = s.detail;
    exact(d, "receipt_no receipt_request_hash shipment_id shipment_no target_location_name received_at lines inbound_no inventory_transaction_id posted_at");
    text(d.receipt_no); hash(d.receipt_request_hash); id(d.shipment_id); text(d.shipment_no); text(d.target_location_name); time(d.received_at); array(d.lines, 100);
    uniqueIds(d.lines.map(l => l.receipt_line_id));
    for (const l of d.lines) {
      exact(l, "receipt_line_id sku_code material_name base_unit accepted_qty rejected_qty condition tracking_mode accepted_serials rejected_serials");
      text(l.sku_code); text(l.material_name); text(l.base_unit); units(l.accepted_qty); units(l.rejected_qty);
      demand(["none", "lot", "serial", "lot_and_serial"].includes(l.tracking_mode));
      for (const [serials, qty] of [[l.accepted_serials, l.accepted_qty], [l.rejected_serials, l.rejected_qty]] as const) {
        array(serials, 1000); uniqueIds(serials.map(sn => sn.serial_id)); serials.forEach(sn => { exact(sn, "serial_id serial_no"); text(sn.serial_no); });
        demand(["serial", "lot_and_serial"].includes(l.tracking_mode) ? BigInt(serials.length) * 1000n === units(qty) : serials.length === 0);
      }
    }
    demand(canonical(s.lines.map(l => l.receipt_line_id).sort()) === canonical(d.lines.filter(l => units(l.rejected_qty) > 0n).map(l => l.receipt_line_id).sort()));
    for (const l of s.lines) {
      exact(l, "receipt_line_id available_qty available_serials register_permitted registrations");
      const original = d.lines.find(row => row.receipt_line_id === l.receipt_line_id)!;
      const available = units(l.available_qty); demand(typeof l.register_permitted === "boolean" && (!l.register_permitted || available > 0n)); array(l.registrations, 1000);
      uniqueIds(l.registrations.map(h => h.registration.return_id));
      let claimed = 0n; const used: string[] = [];
      for (const h of l.registrations) {
        exact(h, "registration progress permitted_actions"); const r = rejectionResult(h.registration, requestId);
        demand(r.receipt_id === s.receipt_id && r.receipt_line_id === l.receipt_line_id && r.receipt_request_hash === d.receipt_request_hash && r.request_version <= p.request_version);
        const state = progressState(h.progress, requestId, r); demand(state.events.every(e => e.request_version <= p.request_version));
        const allowed = state.status === "registered" ? ["cancel_registration", "depart"] : state.status === "departed" ? ["handover"] : [];
        array(h.permitted_actions, 2); demand(new Set(h.permitted_actions).size === h.permitted_actions.length && h.permitted_actions.every(a => allowed.includes(a)));
        if (state.status !== "cancelled") { claimed += units(r.quantity); used.push(...r.serial_ids); }
      }
      demand(claimed + available === units(original.rejected_qty)); uniqueIds(used); demand(used.every(sid => original.rejected_serials.some(sn => sn.serial_id === sid)));
      demand(canonical(l.available_serials) === canonical(original.rejected_serials.filter(sn => !used.includes(sn.serial_id))));
    }
  }
  if (p.next_after_id !== null) demand(id(p.next_after_id) === last && p.items.length > 0); return p;
}
export function rejectionCommand(value: RejectionCommand): RejectionCommand {
  if (value.kind === "register") { exact(value, "kind input"); return { kind: "register", input: rejectionInput(value.input) }; }
  exact(value, "kind return_id input"); demand(value.kind === "progress"); return { kind: "progress", return_id: id(value.return_id), input: progressInput(value.input) };
}
export async function rejectionFingerprint(command: RejectionCommand): Promise<string> {
  const bytes = new TextEncoder().encode(canonical(rejectionCommand(command).input));
  const digest = await crypto.subtle.digest("SHA-256", bytes); return [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, "0")).join("");
}
export function rejectionStatus(value: unknown, requestId: string, returnId: string | null): RejectionStatus {
  const s = exact<RejectionStatus>(value, "lookup_status command"); demand(["confirmed", "not_observed"].includes(s.lookup_status) && (s.lookup_status === "confirmed") === (s.command !== null));
  if (s.command) { if (returnId) progressResult(s.command, requestId, returnId); else rejectionResult(s.command, requestId); } return s;
}
