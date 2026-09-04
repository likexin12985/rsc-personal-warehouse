import { ApiError, api } from "./api";
import type { OpeningStocktakeTaskDetail } from "./formalOpeningStocktake";

export type OpeningRecountActor = Readonly<{ person_id: string; authorization_version: number }>;
export type OpeningRecountAssigneeContext = Readonly<{
  task_id: string;
  source_round_id: string;
  scope_id: string;
  location_id: string;
  region_org_id: string;
  task_version: number;
  actor_person_id: string;
  actor_authorization_version: number;
}>;
export type OpeningRecountAssignee = Readonly<{
  user_id: string;
  person_id: string;
  display_name: string;
  employee_no: string;
  role_code: "admin" | "provincial_manager" | "technician";
}>;
export type OpeningRecountAssigneePage = OpeningRecountAssigneeContext & Readonly<{
  schema_version: "1.0";
  items: readonly OpeningRecountAssignee[];
  next_after_person_id: string | null;
}>;
export type OpeningRecountAssigneeSelection = Readonly<{
  context: OpeningRecountAssigneeContext;
  assignee: OpeningRecountAssignee;
}>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CONTEXT_FIELDS = [
  "task_id", "source_round_id", "scope_id", "location_id", "region_org_id", "task_version",
  "actor_person_id", "actor_authorization_version",
] as const;

function invalid(): never { throw new ApiError(409, "复盘人员目录与当前任务、范围或身份不一致，请重新核验"); }
function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(value)) invalid();
  return value.toLowerCase();
}
function integer(value: unknown, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) invalid();
  return value;
}
function text(value: unknown, maximum: number): string {
  if (typeof value !== "string" || !value || value.trim() !== value || value.length > maximum || /[\u0000-\u001f\u007f]/.test(value)) invalid();
  return value;
}
function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  const keys = Object.keys(row);
  if (keys.length !== fields.length || fields.some((field) => !Object.prototype.hasOwnProperty.call(row, field))) invalid();
  return row;
}
function checkedContext(value: OpeningRecountAssigneeContext): OpeningRecountAssigneeContext {
  const row = exact(value, CONTEXT_FIELDS);
  return Object.freeze({
    task_id: uuid(row.task_id), source_round_id: uuid(row.source_round_id), scope_id: uuid(row.scope_id),
    location_id: uuid(row.location_id), region_org_id: uuid(row.region_org_id),
    task_version: integer(row.task_version, 0), actor_person_id: uuid(row.actor_person_id),
    actor_authorization_version: integer(row.actor_authorization_version, 1),
  });
}

export function openingRecountAssigneeContext(
  detail: OpeningStocktakeTaskDetail, scopeId: string, actor: OpeningRecountActor,
): OpeningRecountAssigneeContext {
  const checkedScopeId = uuid(scopeId);
  const scope = detail.scopes.find((item) => item.scope_id.toLowerCase() === checkedScopeId);
  if (!scope || detail.status !== "recount_required" || detail.current_round?.status !== "submitted"
    || detail.evidence_status !== "sealed" || !detail.allowed_actions.includes("open_recount")) invalid();
  return checkedContext({
    task_id: detail.task_id, source_round_id: detail.current_round.round_id,
    scope_id: checkedScopeId, location_id: scope.location_id, region_org_id: detail.region_org_id,
    task_version: detail.task_version, actor_person_id: actor.person_id,
    actor_authorization_version: actor.authorization_version,
  });
}

export function openingRecountAssigneeContextKey(context: OpeningRecountAssigneeContext): string {
  return JSON.stringify(checkedContext(context));
}

export function validateOpeningRecountAssigneePage(
  value: unknown, expected: OpeningRecountAssigneeContext, afterPersonId: string | null = null, limit = 100,
): OpeningRecountAssigneePage {
  const context = checkedContext(expected);
  const after = afterPersonId === null ? null : uuid(afterPersonId);
  integer(limit, 1);
  if (limit > 100) invalid();
  const page = exact(value, [...CONTEXT_FIELDS, "schema_version", "items", "next_after_person_id"]);
  if (page.schema_version !== "1.0") invalid();
  for (const field of CONTEXT_FIELDS) {
    const actual = field === "task_version" ? integer(page[field], 0)
      : field === "actor_authorization_version" ? integer(page[field], 1) : uuid(page[field]);
    if (actual !== context[field]) invalid();
  }
  if (!Array.isArray(page.items) || page.items.length > limit) invalid();
  const seenUsers = new Set<string>();
  let previousPersonId = after;
  const items = page.items.map((value): OpeningRecountAssignee => {
    const row = exact(value, ["user_id", "person_id", "display_name", "employee_no", "role_code"]);
    const userId = text(row.user_id, 36);
    const personId = uuid(row.person_id);
    const role = row.role_code;
    if (!/^[!-~]{1,36}$/.test(userId) || seenUsers.has(userId) || (previousPersonId !== null && personId <= previousPersonId)
      || (role !== "admin" && role !== "provincial_manager" && role !== "technician")) invalid();
    seenUsers.add(userId);
    previousPersonId = personId;
    return Object.freeze({ user_id: userId, person_id: personId,
      display_name: text(row.display_name, 120), employee_no: text(row.employee_no, 100), role_code: role });
  });
  const next = page.next_after_person_id === null ? null : uuid(page.next_after_person_id);
  // A cursor may disclose only a person already present in this authorized
  // page.  Never accept a filtered-out global directory position.
  if (next !== null && (items.length !== limit || next !== previousPersonId
    || (after !== null && next <= after))) invalid();
  return Object.freeze({ ...context, schema_version: "1.0", items: Object.freeze(items), next_after_person_id: next });
}

export async function loadOpeningRecountAssignees(
  expected: OpeningRecountAssigneeContext, afterPersonId: string | null = null,
): Promise<OpeningRecountAssigneePage> {
  const context = checkedContext(expected);
  const params = new URLSearchParams({ expected_task_version: String(context.task_version), limit: "100" });
  if (afterPersonId !== null) params.set("after_person_id", uuid(afterPersonId));
  const path = `/v1/stocktakes/opening/${context.task_id}/rounds/${context.source_round_id}/scopes/${context.scope_id}/assignees`;
  const result = await api<unknown>(`${path}?${params}`, { cache: "no-store", headers: { "Cache-Control": "no-store" } });
  return validateOpeningRecountAssigneePage(result, context, afterPersonId);
}
