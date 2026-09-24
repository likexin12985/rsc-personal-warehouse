export type ControlActor = { person_id: string; authorization_version: number };
export type Purpose = "preview" | "execute" | "status";
type Document = Record<string, unknown>;
export type ControlCommand = Document & { action: "source_grant" | "catalog_grant" | "revoke"; reason: string };
export type SignedDocument = { payload: Document; key_id: string; signature: string };
export type ReviewView = { purpose: Purpose; can_execute: boolean; issued_at: number; expires_at: number; result: Document };
const limit = 2 * 1024 * 1024;
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const digest = /^[a-f0-9]{64}$/;
const commandKeys = ["action", "binding_id", "catalog_id", "source_grant_id", "revoked_grant_id", "expected_subject_sha256", "evidence_file_id", "evidence_sha256", "reason", "valid_from", "valid_to", "idempotency_key", "request_id"];
const optional = ["catalog_id", "source_grant_id", "revoked_grant_id", "valid_from", "valid_to"];
function fail(): never { throw new Error("配置文件或响应未通过校验，请保留原请求并重新核查"); }
export function object(value: unknown): Document {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  return value as Document;
}
function exact(value: Document, keys: string[]) {
  if (Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail();
}
function id(value: unknown) {
  if (typeof value !== "string" || !uuid.test(value) || value === "00000000-0000-0000-0000-000000000000") fail();
}
function hash(value: unknown) { if (typeof value !== "string" || !digest.test(value)) fail(); }
function timestamp(value: unknown) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value) || !Number.isFinite(Date.parse(value))) fail();
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return JSON.stringify(value.map(canonical));
  if (value && typeof value === "object") return JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return JSON.stringify(value);
}
function same(left: unknown, right: unknown) { if (canonical(left) !== canonical(right)) fail(); }
function sameCommand(left: ControlCommand, right: ControlCommand) {
  // Normalize only validity fields; reasons and business keys remain literal.
  const normalize = (command: ControlCommand) => Object.fromEntries(Object.entries(command).map(([key, value]) => {
    if ((key === "valid_from" || key === "valid_to") && typeof value === "string") {
      const fraction = value.match(/\.(\d{1,6})/)?.[1] ?? "";
      return [key, [Math.floor(Date.parse(value) / 1000), fraction.padEnd(6, "0")]];
    }
    return [key, value];
  }));
  same(normalize(left), normalize(right));
}
export function commandDocument(value: unknown): ControlCommand {
  const raw = object(value);
  if (Object.keys(raw).some(key => !commandKeys.includes(key))) fail();
  const result = { ...Object.fromEntries(optional.map(key => [key, null])), ...raw };
  exact(result, commandKeys);
  if (!["source_grant", "catalog_grant", "revoke"].includes(String(result.action))) fail();
  for (const key of ["binding_id", "evidence_file_id"]) id(result[key]);
  for (const key of ["catalog_id", "source_grant_id", "revoked_grant_id"]) if (result[key] !== null) id(result[key]);
  hash(result.expected_subject_sha256); hash(result.evidence_sha256);
  if (typeof result.reason !== "string" || !result.reason.trim() || result.reason.length > 1000) fail();
  for (const [key, min, max] of [["idempotency_key", 16, 128], ["request_id", 8, 160]] as const) {
    const text = result[key];
    if (typeof text !== "string" || text.length < min || text.length > max || !/^[A-Za-z0-9._:-]+$/.test(text)) fail();
  }
  for (const key of ["valid_from", "valid_to"]) if (result[key] !== null) timestamp(result[key]);
  if (result.action === "source_grant" && [result.catalog_id, result.source_grant_id, result.revoked_grant_id].some(x => x !== null)) fail();
  if (result.action === "catalog_grant" && (!result.catalog_id || !result.source_grant_id || result.revoked_grant_id !== null)) fail();
  if (result.action === "revoke" && (!result.revoked_grant_id || [result.source_grant_id, result.valid_from, result.valid_to].some(x => x !== null))) fail();
  return result as ControlCommand;
}
export function parseCommand(text: string): ControlCommand {
  if (new TextEncoder().encode(text).length > 65536) fail();
  const value = object(JSON.parse(text));
  if (Object.keys(value).some(key => !["command", "expected_authorization_version", "review_sha256"].includes(key))) fail();
  return commandDocument(value.command);
}
export function signedDocument(value: unknown): SignedDocument {
  const result = object(value);
  exact(result, ["payload", "key_id", "signature"]);
  object(result.payload);
  if (typeof result.key_id !== "string" || !/^[a-f0-9]{16}$/.test(result.key_id) || typeof result.signature !== "string" || !/^[a-f0-9]{128}$/.test(result.signature)) fail();
  if (new TextEncoder().encode(JSON.stringify(value)).length > limit) fail();
  return result as SignedDocument;
}
export function parseResponse(text: string) {
  if (new TextEncoder().encode(text).length > limit) fail();
  return signedDocument(JSON.parse(text));
}
function request(value: unknown, command: ControlCommand, actor: ControlActor, current: boolean) {
  const packet = signedDocument(value), p = packet.payload;
  exact(p, ["schema_version", "handoff_id", "purpose", "deployment_id", "database_id", "actor_user_id", "actor_person_id", "authorization_version", "auth_session_id", "access_issued_at", "access_expires_at", "issued_at", "expires_at", "command", "review_sha256", "audit_event_id"]);
  if (p.schema_version !== "rsc.control_configuration_request.v1" || !["preview", "execute", "status"].includes(String(p.purpose))) fail();
  for (const key of ["handoff_id", "deployment_id", "database_id", "actor_user_id", "actor_person_id", "auth_session_id", "audit_event_id"]) id(p[key]);
  for (const key of ["authorization_version", "access_issued_at", "access_expires_at", "issued_at", "expires_at"]) if (!Number.isSafeInteger(p[key]) || Number(p[key]) <= 0) fail();
  if (p.actor_person_id !== actor.person_id || (current && p.authorization_version !== actor.authorization_version)) fail();
  if (!(Number(p.access_issued_at) <= Number(p.issued_at) && Number(p.issued_at) < Number(p.expires_at) && Number(p.expires_at) <= Number(p.access_expires_at)) || Number(p.expires_at) - Number(p.issued_at) > 300) fail();
  if (p.purpose === "preview") { if (p.review_sha256 !== null) fail(); } else hash(p.review_sha256);
  sameCommand(commandDocument(p.command), command);
  return packet;
}
export function handoffResponse(value: unknown, command: ControlCommand, actor: ControlActor, purpose: Purpose): SignedDocument {
  const result = object(value);
  exact(result, ["handoff", "decision_recorded", "projection_published", "start_ready"]);
  if ([result.decision_recorded, result.projection_published, result.start_ready].some(x => x !== false)) fail();
  const packet = request(result.handoff, command, actor, true);
  if (packet.payload.purpose !== purpose || Number(packet.payload.expires_at) * 1000 <= Date.now()) fail();
  return packet;
}
export function verifiedResponse(value: unknown, response: SignedDocument, command: ControlCommand, actor: ControlActor): ReviewView {
  const view = object(value), payload = response.payload;
  exact(view, ["result", "purpose", "can_execute", "issued_at", "expires_at"]);
  exact(payload, ["schema_version", "deployment_id", "database_id", "issued_at", "expires_at", "request", "result"]);
  if (payload.schema_version !== "rsc.control_configuration_response.v1") fail();
  const original = request(payload.request, command, actor, false).payload;
  if (payload.deployment_id !== original.deployment_id || payload.database_id !== original.database_id || view.purpose !== original.purpose) fail();
  same(view.result, payload.result);
  if (!Number.isSafeInteger(view.issued_at) || !Number.isSafeInteger(view.expires_at) || view.issued_at !== payload.issued_at || view.expires_at !== payload.expires_at || Number(view.expires_at) - Number(view.issued_at) !== 300 || Number(view.issued_at) < Number(original.issued_at) || Number(view.issued_at) >= Number(original.expires_at)) fail();
  if (typeof view.can_execute !== "boolean" || (view.can_execute && (view.purpose !== "preview" || original.authorization_version !== actor.authorization_version || Number(view.expires_at) * 1000 <= Date.now()))) fail();
  const result = object(view.result);
  if (result.projection_published !== false || result.start_ready !== false) fail();
  if (view.purpose === "preview") {
    exact(result, ["review", "review_sha256", "projection_published", "start_ready"]); hash(result.review_sha256);
    const review = object(result.review);
    if (review.schema_version !== "rsc.inventory_control_configuration_review.v1" || review.actor_person_id !== actor.person_id || review.actor_user_id !== original.actor_user_id || review.actor_authorization_version !== original.authorization_version) fail();
    sameCommand(commandDocument(review.command), command);
    const evidence = object(review.evidence_file);
    exact(evidence, ["file_id", "sha256", "size_bytes", "mime_type"]);
    if (evidence.file_id !== command.evidence_file_id || evidence.sha256 !== command.evidence_sha256 || !Number.isSafeInteger(evidence.size_bytes) || Number(evidence.size_bytes) <= 0 || typeof evidence.mime_type !== "string") fail();
    const source = object(review.source), region = object(review.region);
    if (typeof source.code !== "string" || typeof source.mode !== "string" || typeof source.enabled !== "boolean" || typeof region.name !== "string" || typeof region.code !== "string") fail();
  } else {
    if (typeof result.recorded !== "boolean" || (view.purpose === "execute" && !result.recorded)) fail();
    exact(result, result.recorded ? ["decision", "execution_audit_event_id", "recorded", "projection_published", "start_ready"] : ["recorded", "projection_published", "start_ready"]);
    if (result.recorded) {
      const decision = object(result.decision);
      exact(decision, ["decision_id", "action", "binding_id", "catalog_id", "payload_sha256", "audit_event_id", "created_at", "valid_from", "valid_to"]);
      for (const key of ["decision_id", "audit_event_id"]) id(decision[key]);
      id(result.execution_audit_event_id); hash(decision.payload_sha256);
      if (decision.action !== command.action || decision.binding_id !== command.binding_id || decision.catalog_id !== command.catalog_id) fail();
      timestamp(decision.created_at); timestamp(decision.valid_from); if (decision.valid_to !== null) timestamp(decision.valid_to);
    }
  }
  return view as ReviewView;
}
export const actionLabel = (command: ControlCommand) => ({ source_grant: "来源授权", catalog_grant: "版本授权", revoke: "撤销授权" })[command.action];
