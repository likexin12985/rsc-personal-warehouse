/** Recipient contracts mirror the existing formal my-receiving APIs. */
export const conditions = { normal: "正常", shortage: "短少", damaged: "破损", wrong_material: "错料", wrong_serial: "错 SN", rejected: "拒收" } as const;
export type Condition = keyof typeof conditions;
export type ReceivingLine = { shipment_line_id: string; request_line_id: string; sku_code: string; material_name: string; base_unit: string; shipped_qty: string; accepted_qty: string; rejected_qty: string; unconfirmed_qty: string; has_exception: boolean };
export type ReceivingPackage = { shipment_id: string; shipment_no: string; shipment_status: "pending_handover" | "shipped" | "in_transit" | "exception"; carrier: string; tracking_no: string; shipped_at: string; target_location_id: string; target_location_name: string; lines: ReceivingLine[] };
export type ReceivingPage = { schema_version: "1.0"; request_id: string; request_no: string; request_version: number; person_id: string; packages: ReceivingPackage[]; next_after_id: string | null };
export type CandidateLine = ReceivingLine & { lot_no: string | null; tracking_mode: "none" | "lot" | "serial" | "lot_and_serial"; quantity_scale: number; allow_fraction: boolean; remaining_serials: { serial_id: string; serial_no: string; qr_code: string }[] };
export type ReceiptCandidate = Omit<ReceivingPage, "packages" | "next_after_id"> & { shipment_id: string; shipment_no: string; shipped_at: string; target_location_name: string; checked_at: string; can_receive: boolean; blocked_reason: "permission_required" | "request_not_approved" | "pending_handover" | "complete" | null; lines: CandidateLine[] };
export type ReceiptInputLine = { shipment_line_id: string; accepted_qty: string; rejected_qty: string; condition: Condition; accepted_serial_ids: string[]; rejected_serial_ids: string[]; exception_evidence_file_id: string | null };
export type ReceiptInput = { expected_request_version: number; shipment_id: string; received_at: string; lines: ReceiptInputLine[] };
export type ReceiptResult = { schema_version: "1.0"; request_id: string; person_id: string; receipt_id: string; receipt_no: string; shipment_id: string; received_at: string; status: "accepted" | "exception"; request_hash: string; lines: (ReceiptInputLine & { receipt_line_id: string })[]; idempotency_replayed: boolean };
export type InboundDetail = { receipt_no: string; receipt_request_hash: string; shipment_id: string; shipment_no: string; target_location_name: string; received_at: string; lines: { receipt_line_id: string; sku_code: string; material_name: string; base_unit: string; accepted_qty: string; rejected_qty: string; condition: Condition; tracking_mode: CandidateLine["tracking_mode"]; rejected_serials: { serial_id: string; serial_no: string }[]; accepted_serials: { serial_id: string; serial_no: string }[] }[]; inbound_no: string | null; inventory_transaction_id: string | null; posted_at: string | null };
export type InboundItem = { receipt_id: string; status: "pending" | "posted" | "no_accepted" | "blocked"; message: string; detail: InboundDetail | null };
export type InboundPage = Omit<ReceivingPage, "packages"> & { can_post: boolean; items: InboundItem[] };
export type InboundInput = { expected_request_version: number; receipt_id: string; receipt_request_hash: string };
export type InboundResult = { schema_version: "1.0"; request_id: string; request_version: number; current_request_version: number; person_id: string; receipt_id: string; receipt_request_hash: string; shipment_id: string; inbound_order_id: string; inbound_no: string; inventory_transaction_id: string; posted_at: string; request_hash: string; idempotency_replayed: boolean };
export type PersonalCommand = { kind: "receipt"; input: ReceiptInput } | { kind: "inbound"; input: InboundInput };
export function demand(condition: unknown, message = "本人收货或入账数据未通过核验，请保留原请求"): asserts condition { if (!condition) throw new Error(message); }
export function exact<T>(value: unknown, keys: string): T {
  demand(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join("|") === keys.split(" ").sort().join("|"));
  return value as T;
}
export function id(value: unknown): string { demand(typeof value === "string" && /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)); return value; }
export function text(value: unknown): string { demand(typeof value === "string" && value.trim() && value.length <= 1000 && !/[\u0000-\u001f\u007f]/.test(value)); return value; }
export function version(value: unknown): number { demand(typeof value === "number" && Number.isSafeInteger(value) && value >= 1); return value; }
export function hash(value: unknown): string { demand(typeof value === "string" && /^[a-f0-9]{64}$/.test(value)); return value; }
export function time(value: unknown): string { demand(typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value))); return value; }
export function quantity(value: unknown): string { demand(typeof value === "string" && /^(0|[1-9][0-9]{0,14})(?:\.[0-9]{1,3})?$/.test(value), "数量必须为非负十进制，最多三位小数"); const [whole, fraction = ""] = value.split("."); return `${whole}.${fraction.padEnd(3, "0")}`; }
export function units(value: string): bigint { return BigInt(quantity(value).replace(".", "")); }
function wireQuantity(value: string) { demand(quantity(value) === value); return units(value); }
function list(value: unknown, max: number, minimum = 0): asserts value is unknown[] { demand(Array.isArray(value) && value.length >= minimum && value.length <= max); }
function unique(values: string[]) { demand(new Set(values).size === values.length); }
function anchors(row: ReceivingPage | InboundPage | ReceiptCandidate, requestId: string, personId: string) { demand(row.schema_version === "1.0" && id(row.request_id) === id(requestId) && id(row.person_id) === id(personId)); version(row.request_version); text(row.request_no); }
const lineFields = "shipment_line_id request_line_id sku_code material_name base_unit shipped_qty accepted_qty rejected_qty unconfirmed_qty has_exception";
function checkLine(line: ReceivingLine) { id(line.shipment_line_id); id(line.request_line_id); text(line.sku_code); text(line.material_name); text(line.base_unit); const shipped = wireQuantity(line.shipped_qty), accepted = wireQuantity(line.accepted_qty), rejected = wireQuantity(line.rejected_qty), remaining = wireQuantity(line.unconfirmed_qty); demand(shipped > 0n && accepted + rejected + remaining === shipped && typeof line.has_exception === "boolean" && (!rejected || line.has_exception)); }
function cursor(ids: string[], next: string | null, after: string | null) { let previous = after ? id(after) : ""; for (const value of ids) { demand(id(value) > previous); previous = value; } if (next !== null) demand(ids.length && id(next) === previous); }
export function receivingPage(value: unknown, requestId: string, personId: string, after: string | null = null): ReceivingPage {
  const row = exact<ReceivingPage>(value, "schema_version request_id request_no request_version person_id packages next_after_id"); anchors(row, requestId, personId); list(row.packages, 20);
  const lines: string[] = [];
  for (const item of row.packages) { exact(item, "shipment_id shipment_no shipment_status carrier tracking_no shipped_at target_location_id target_location_name lines"); id(item.shipment_id); id(item.target_location_id); for (const v of [item.shipment_no, item.carrier, item.tracking_no, item.target_location_name]) text(v); time(item.shipped_at); demand(["pending_handover", "shipped", "in_transit", "exception"].includes(item.shipment_status)); list(item.lines, 100, 1); for (const line of item.lines) { exact(line, lineFields); checkLine(line); lines.push(line.shipment_line_id); } }
  unique(lines); cursor(row.packages.map(x => x.shipment_id), row.next_after_id, after); return row;
}
export function receiptCandidate(value: unknown, requestId: string, shipmentId: string, personId: string): ReceiptCandidate {
  const row = exact<ReceiptCandidate>(value, "schema_version request_id request_no request_version person_id shipment_id shipment_no shipped_at target_location_name checked_at can_receive blocked_reason lines"); anchors(row, requestId, personId); demand(id(row.shipment_id) === id(shipmentId)); text(row.shipment_no); text(row.target_location_name); time(row.shipped_at); time(row.checked_at);
  demand(typeof row.can_receive === "boolean" && (row.blocked_reason === null || ["permission_required", "request_not_approved", "pending_handover", "complete"].includes(row.blocked_reason)) && row.can_receive === (row.blocked_reason === null)); list(row.lines, 100, 1);
  const serials: string[] = [], qrs: string[] = [];
  for (const line of row.lines) {
    exact(line, lineFields + " lot_no tracking_mode quantity_scale allow_fraction remaining_serials"); checkLine(line);
    demand(["none", "lot", "serial", "lot_and_serial"].includes(line.tracking_mode) && Number.isInteger(line.quantity_scale) && line.quantity_scale >= 0 && line.quantity_scale <= 3 && typeof line.allow_fraction === "boolean");
    if (line.lot_no !== null) text(line.lot_no); if (["lot", "lot_and_serial"].includes(line.tracking_mode)) demand(line.lot_no !== null);
    for (const q of [line.shipped_qty, line.accepted_qty, line.rejected_qty, line.unconfirmed_qty]) demand(units(q) % (10n ** BigInt(3 - line.quantity_scale)) === 0n && (line.allow_fraction || units(q) % 1000n === 0n));
    list(line.remaining_serials, 1000); demand(tracked(line) ? BigInt(line.remaining_serials.length) * 1000n === units(line.unconfirmed_qty) : !line.remaining_serials.length);
    for (const serial of line.remaining_serials) { exact(serial, "serial_id serial_no qr_code"); serials.push(id(serial.serial_id)); qrs.push(text(serial.qr_code)); text(serial.serial_no); }
  }
  unique(row.lines.map(x => x.shipment_line_id)); unique(serials); unique(qrs); const complete = row.lines.every(x => units(x.unconfirmed_qty) === 0n); demand(!(row.can_receive && complete) && !(row.blocked_reason === "complete" && !complete)); return row;
}
export function tracked(line: Pick<CandidateLine, "tracking_mode">) { return ["serial", "lot_and_serial"].includes(line.tracking_mode); }
export function inboundPage(value: unknown, requestId: string, personId: string, after: string | null = null): InboundPage {
  const row = exact<InboundPage>(value, "schema_version request_id request_no request_version person_id can_post items next_after_id"); anchors(row, requestId, personId); demand(typeof row.can_post === "boolean"); list(row.items, 20);
  for (const item of row.items) {
    exact(item, "receipt_id status message detail"); id(item.receipt_id); text(item.message); demand(["pending", "posted", "no_accepted", "blocked"].includes(item.status));
    if (item.status === "blocked") { demand(item.detail === null); continue; }
    const d = exact<InboundDetail>(item.detail, "receipt_no receipt_request_hash shipment_id shipment_no target_location_name received_at lines inbound_no inventory_transaction_id posted_at");
    text(d.receipt_no); hash(d.receipt_request_hash); id(d.shipment_id); text(d.shipment_no); text(d.target_location_name); time(d.received_at); list(d.lines, 100, 1);
    let accepted = 0n; const serials: string[] = [];
    for (const line of d.lines) {
      exact(line, "receipt_line_id sku_code material_name base_unit accepted_qty rejected_qty condition tracking_mode accepted_serials rejected_serials");
      id(line.receipt_line_id); text(line.sku_code); text(line.material_name); text(line.base_unit);
      const a = wireQuantity(line.accepted_qty), r = wireQuantity(line.rejected_qty); demand(a + r > 0n); accepted += a;
      demand(Object.hasOwn(conditions, line.condition) && ["none", "lot", "serial", "lot_and_serial"].includes(line.tracking_mode));
      if (line.condition === "normal") demand(r === 0n);
      if (["damaged", "wrong_material", "wrong_serial", "rejected"].includes(line.condition)) demand(a === 0n);
      for (const [group, qty] of [[line.accepted_serials, a], [line.rejected_serials, r]] as const) {
        list(group, 1000); demand(tracked(line) ? BigInt(group.length) * 1000n === qty : !group.length);
        for (const serial of group) { exact(serial, "serial_id serial_no"); serials.push(id(serial.serial_id)); text(serial.serial_no); }
      }
    }
    unique(d.lines.map(x => x.receipt_line_id)); unique(serials); demand((item.status === "no_accepted") === (accepted === 0n));
    if (item.status === "posted") { text(d.inbound_no); id(d.inventory_transaction_id); time(d.posted_at); } else demand(d.inbound_no === null && d.inventory_transaction_id === null && d.posted_at === null);
  }
  cursor(row.items.map(x => x.receipt_id), row.next_after_id, after); return row;
}
const inputLineFields = "shipment_line_id accepted_qty rejected_qty condition accepted_serial_ids rejected_serial_ids exception_evidence_file_id";
export function receiptInput(value: unknown): ReceiptInput {
  const row = exact<ReceiptInput>(value, "expected_request_version shipment_id received_at lines"); version(row.expected_request_version); id(row.shipment_id); time(row.received_at); list(row.lines, 100, 1); const serials: string[] = [];
  const lines = row.lines.map(line => {
    exact(line, inputLineFields); id(line.shipment_line_id); demand(Object.hasOwn(conditions, line.condition)); const accepted = quantity(line.accepted_qty), rejected = quantity(line.rejected_qty); demand(units(accepted) + units(rejected) > 0n);
    if (line.condition === "normal") demand(units(rejected) === 0n && line.exception_evidence_file_id === null); else id(line.exception_evidence_file_id);
    if (["damaged", "wrong_material", "wrong_serial", "rejected"].includes(line.condition)) demand(units(accepted) === 0n);
    list(line.accepted_serial_ids, 1000); list(line.rejected_serial_ids, 1000); serials.push(...line.accepted_serial_ids.map(id), ...line.rejected_serial_ids.map(id));
    return { ...line, accepted_qty: accepted, rejected_qty: rejected, accepted_serial_ids: [...line.accepted_serial_ids].sort(), rejected_serial_ids: [...line.rejected_serial_ids].sort() };
  }).sort((a, b) => a.shipment_line_id.localeCompare(b.shipment_line_id)); unique(lines.map(x => x.shipment_line_id)); unique(serials); return { ...row, lines };
}
export function inboundInput(value: unknown): InboundInput { const row = exact<InboundInput>(value, "expected_request_version receipt_id receipt_request_hash"); version(row.expected_request_version); id(row.receipt_id); hash(row.receipt_request_hash); return row; }
export function canonical(value: unknown): string { if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`; if (value && typeof value === "object") { const r = value as Record<string, unknown>; return `{${Object.keys(r).sort().map(k => `${JSON.stringify(k)}:${canonical(r[k])}`).join(",")}}`; } return JSON.stringify(value); }
export async function commandHash(requestId: string, personId: string, command: PersonalCommand): Promise<string> {
  const value = command.kind === "receipt" ? { ...receiptInput(command.input), request_id: id(requestId), person_id: id(personId) } : { request_id: id(requestId), person_id: id(personId), command: inboundInput(command.input) };
  const result = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(value))); return [...new Uint8Array(result)].map(v => v.toString(16).padStart(2, "0")).join("");
}
export type ReceiptDraft = { accepted: string; rejected: string; condition: Condition; serials: Record<string, "accepted" | "rejected">; evidenceFileId: string | null };
export function buildReceipt(candidate: ReceiptCandidate, drafts: Record<string, ReceiptDraft>, receivedAt: string): ReceiptInput {
  demand(candidate.can_receive, "当前包裹不允许新增验收"); demand(Date.parse(time(receivedAt)) >= Date.parse(candidate.shipped_at) && Date.parse(receivedAt) <= Date.now(), "验收时间必须在交运之后、当前时间之前");
  const lines: ReceiptInputLine[] = [];
  for (const line of candidate.lines) {
    const d = drafts[line.shipment_line_id]; if (!d) continue; const selected = Object.keys(d.serials); demand(selected.every(s => line.remaining_serials.some(x => x.serial_id === s)), "所选SN不在最新待验收包裹中");
    const acceptedIds = selected.filter(s => d.serials[s] === "accepted"), rejectedIds = selected.filter(s => d.serials[s] === "rejected"); demand(acceptedIds.length + rejectedIds.length === selected.length);
    const accepted = tracked(line) ? quantity(String(acceptedIds.length)) : quantity(d.accepted || "0"), rejected = tracked(line) ? quantity(String(rejectedIds.length)) : quantity(d.rejected || "0"); const total = units(accepted) + units(rejected); if (!total) continue;
    demand(total <= units(line.unconfirmed_qty), "验收数量超过包裹剩余数量"); demand(tracked(line) || !selected.length); for (const q of [accepted, rejected]) demand(units(q) % (10n ** BigInt(3 - line.quantity_scale)) === 0n && (line.allow_fraction || units(q) % 1000n === 0n), "数量不符合物料精度");
    lines.push({ shipment_line_id: line.shipment_line_id, accepted_qty: accepted, rejected_qty: rejected, condition: d.condition, accepted_serial_ids: acceptedIds, rejected_serial_ids: rejectedIds, exception_evidence_file_id: d.evidenceFileId });
  }
  return receiptInput({ expected_request_version: candidate.request_version, shipment_id: candidate.shipment_id, received_at: receivedAt, lines });
}
export async function commandResult(value: unknown, requestId: string, personId: string, command: PersonalCommand, fingerprint: string): Promise<ReceiptResult | InboundResult> {
  demand(await commandHash(requestId, personId, command) === hash(fingerprint));
  if (command.kind === "receipt") {
    const r = exact<ReceiptResult>(value, "schema_version request_id person_id receipt_id receipt_no shipment_id received_at status request_hash lines idempotency_replayed"); demand(r.schema_version === "1.0" && id(r.request_id) === requestId && id(r.person_id) === personId && id(r.shipment_id) === command.input.shipment_id && r.request_hash === fingerprint && typeof r.idempotency_replayed === "boolean"); id(r.receipt_id); text(r.receipt_no); time(r.received_at); demand(Date.parse(r.received_at) === Date.parse(command.input.received_at) && (r.received_at.match(/\.(\d+)/)?.[1] || "").padEnd(6, "0") === (command.input.received_at.match(/\.(\d+)/)?.[1] || "").padEnd(6, "0")); list(r.lines, 100, 1);
    const body = receiptInput({ ...command.input, lines: r.lines.map(line => { exact(line, inputLineFields + " receipt_line_id"); id(line.receipt_line_id); const { receipt_line_id: _, ...rest } = line; return rest; }) }); unique(r.lines.map(x => x.receipt_line_id)); demand(canonical(body) === canonical(receiptInput(command.input)) && r.status === (body.lines.every(x => x.condition === "normal") ? "accepted" : "exception")); return r;
  }
  const r = exact<InboundResult>(value, "schema_version request_id request_version current_request_version person_id receipt_id receipt_request_hash shipment_id inbound_order_id inbound_no inventory_transaction_id posted_at request_hash idempotency_replayed");
  demand(r.schema_version === "1.0" && id(r.request_id) === requestId && id(r.person_id) === personId && id(r.receipt_id) === command.input.receipt_id && r.receipt_request_hash === command.input.receipt_request_hash && r.request_hash === fingerprint && r.request_version === command.input.expected_request_version + 1 && version(r.current_request_version) >= r.request_version && typeof r.idempotency_replayed === "boolean"); id(r.shipment_id); id(r.inbound_order_id); text(r.inbound_no); id(r.inventory_transaction_id); time(r.posted_at); return r;
}
