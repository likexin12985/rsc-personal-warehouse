import { ApiError, apiNoReplay } from "./api";

export type OpeningPreparationActor = Readonly<{ person_id: string; authorization_version: number }>;
export type OpeningPreparationStage = "regions" | "asset-owners" | "locations" | "assignees";
export type OpeningPreparationContext = Readonly<{
  stage: OpeningPreparationStage; actor_person_id: string; authorization_version: number;
  region_org_id?: string; owner_org_id?: string; location_id?: string;
}>;
export type OpeningPreparationOption = Readonly<
  | { stage: "regions"; id: string; name: string; code: string; provinceCode: string | null }
  | { stage: "asset-owners"; id: string; name: string; code: string }
  | { stage: "locations"; id: string; name: string; code: string; locationType: "region" | "personal";
    physicalOwnerId: string; physicalOwnerName: string; custodianPersonId: string | null; custodianName: string | null }
  | { stage: "assignees"; id: string; name: string; userId: string }
>;
export type OpeningPreparationPage = Readonly<{
  context: OpeningPreparationContext; items: readonly OpeningPreparationOption[]; nextAfterId: string | null;
}>;

export const OPENING_PREPARATION_LIMIT = 50;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ANCHORS: Record<OpeningPreparationStage, readonly string[]> = {
  regions: [], "asset-owners": ["region_org_id"], locations: ["region_org_id", "owner_org_id"],
  assignees: ["region_org_id", "owner_org_id", "location_id"],
};
function invalid(): never { throw new ApiError(409, "期初盘点准备目录与当前身份或范围不一致，请重新核验"); }
function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value) || value === "00000000-0000-0000-0000-000000000000") invalid();
  return value.toLowerCase();
}
function integer(value: unknown, minimum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) invalid();
  return value;
}
function label(value: unknown, maximum: number): string {
  if (typeof value !== "string" || !value || value.trim() !== value || value.length > maximum
    || /[\u0000-\u001f\u007f]/.test(value)) invalid();
  return value;
}
function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== fields.length || fields.some((key) => !Object.hasOwn(row, key))) invalid();
  return row;
}
function anchors(stage: unknown): readonly string[] {
  if (typeof stage !== "string" || !Object.hasOwn(ANCHORS, stage)) invalid();
  return ANCHORS[stage as OpeningPreparationStage];
}
function checkedContext(value: OpeningPreparationContext): OpeningPreparationContext {
  const fields = anchors(value?.stage);
  const row = exact(value, ["stage", "actor_person_id", "authorization_version", ...fields]);
  return Object.freeze({ stage: value.stage, actor_person_id: uuid(row.actor_person_id),
    authorization_version: integer(row.authorization_version, 1),
    ...Object.fromEntries(fields.map((key) => [key, uuid(row[key])])),
  });
}

export function openingPreparationContext(
  actor: OpeningPreparationActor, stage: OpeningPreparationStage, coordinates: Record<string, string> = {},
): OpeningPreparationContext {
  const identity = exact(actor, ["person_id", "authorization_version"]);
  exact(coordinates, anchors(stage));
  return checkedContext({ stage, actor_person_id: uuid(identity.person_id),
    authorization_version: integer(identity.authorization_version, 1), ...coordinates });
}
export function openingPreparationContextKey(context: OpeningPreparationContext): string {
  return JSON.stringify(checkedContext(context));
}

function option(stage: OpeningPreparationStage, value: unknown): OpeningPreparationOption {
  if (stage === "regions") {
    const row = exact(value, ["region_org_id", "code", "name", "province_code"]);
    return Object.freeze({ stage, id: uuid(row.region_org_id), code: label(row.code, 80), name: label(row.name, 200),
      provinceCode: row.province_code === null ? null : label(row.province_code, 12) });
  }
  if (stage === "asset-owners") {
    const row = exact(value, ["owner_org_id", "code", "name"]);
    return Object.freeze({ stage, id: uuid(row.owner_org_id), code: label(row.code, 80), name: label(row.name, 200) });
  }
  if (stage === "locations") {
    const row = exact(value, ["location_id", "code", "name", "location_type", "physical_owner_org_id",
      "physical_owner_name", "custodian_person_id", "custodian_name"]);
    if (row.location_type !== "region" && row.location_type !== "personal") invalid();
    const custodianPersonId = row.custodian_person_id === null ? null : uuid(row.custodian_person_id);
    const custodianName = row.custodian_name === null ? null : label(row.custodian_name, 120);
    if ((custodianPersonId === null) !== (custodianName === null)
      || (row.location_type === "personal" && custodianPersonId === null)) invalid();
    return Object.freeze({ stage, id: uuid(row.location_id), code: label(row.code, 100), name: label(row.name, 200),
      locationType: row.location_type, physicalOwnerId: uuid(row.physical_owner_org_id),
      physicalOwnerName: label(row.physical_owner_name, 200), custodianPersonId, custodianName });
  }
  const row = exact(value, ["assignee_user_id", "person_id", "name"]);
  const userId = label(row.assignee_user_id, 36);
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$/.test(userId)) invalid();
  return Object.freeze({ stage, id: uuid(row.person_id), name: label(row.name, 120), userId });
}

export function validateOpeningStartOptionPage(
  value: unknown, expected: OpeningPreparationContext, afterId: string | null = null, limit = OPENING_PREPARATION_LIMIT,
): OpeningPreparationPage {
  const context = checkedContext(expected);
  const after = afterId === null ? null : uuid(afterId);
  if (integer(limit, 1) > 100) invalid();
  const fields = anchors(context.stage);
  const cursorField = context.stage === "assignees" ? "next_after_person_id" : "next_after_id";
  const page = exact(value, ["schema_version", "actor_person_id", "authorization_version", "start_ready",
    "control_evidence_status", "items", cursorField, ...fields]);
  if (page.schema_version !== "1.0" || page.start_ready !== false
    || page.control_evidence_status !== "control_evidence_not_evaluated"
    || uuid(page.actor_person_id) !== context.actor_person_id
    || integer(page.authorization_version, 1) !== context.authorization_version) invalid();
  for (const key of fields) if (uuid(page[key]) !== context[key as keyof OpeningPreparationContext]) invalid();
  if (!Array.isArray(page.items) || page.items.length > limit) invalid();
  const seenUsers = new Set<string>();
  let previous = after;
  const items = page.items.map((row) => {
    const item = option(context.stage, row);
    if (previous !== null && item.id <= previous) invalid();
    previous = item.id;
    if (item.stage === "assignees") {
      if (seenUsers.has(item.userId)) invalid();
      seenUsers.add(item.userId);
    }
    return item;
  });
  const nextAfterId = page[cursorField] === null ? null : uuid(page[cursorField]);
  if (nextAfterId !== null && (items.length !== limit || nextAfterId !== previous
    || (after !== null && nextAfterId <= after))) invalid();
  return Object.freeze({ context, items: Object.freeze(items), nextAfterId });
}

export async function loadOpeningStartOptions(
  expected: OpeningPreparationContext, afterId: string | null = null, limit = OPENING_PREPARATION_LIMIT,
): Promise<OpeningPreparationPage> {
  const context = checkedContext(expected);
  const after = afterId === null ? null : uuid(afterId);
  if (integer(limit, 1) > 100) invalid();
  const params = new URLSearchParams({ limit: String(limit) });
  for (const key of anchors(context.stage)) params.set(key, context[key as keyof OpeningPreparationContext] as string);
  if (after !== null) params.set(context.stage === "assignees" ? "after_person_id" : "after_id", after);
  // The existing transport explicitly disables refresh/replay, including an
  // authentication POST after a 401. No command or durable storage is created.
  const result = await apiNoReplay<unknown>(`/v1/stocktakes/opening/start-options/${context.stage}?${params}`, {
    method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
  });
  return validateOpeningStartOptionPage(result, context, after, limit);
}

export function mergeOpeningStartOptions(
  previous: readonly OpeningPreparationOption[], page: OpeningPreparationPage,
  context: OpeningPreparationContext, after: string | null,
): readonly OpeningPreparationOption[] {
  if (openingPreparationContextKey(page.context) !== openingPreparationContextKey(context)) invalid();
  if ((after === null && previous.length !== 0) || (after !== null && previous.at(-1)?.id !== after)) invalid();
  const result = [...previous, ...page.items];
  const users = new Set<string>();
  let previousId: string | null = null;
  for (const item of result) {
    if (item.stage !== context.stage || (previousId !== null && item.id <= previousId)) invalid();
    previousId = item.id;
    if (item.stage === "assignees") {
      if (users.has(item.userId)) invalid();
      users.add(item.userId);
    }
  }
  if (page.nextAfterId !== null && (!page.items.length || page.items.at(-1)?.id !== page.nextAfterId
    || (after !== null && page.nextAfterId <= after))) invalid();
  return Object.freeze(result);
}
