import { api, ApiError, jsonBody } from "./api";
import {
  FORMAL_STOCKTAKE_SCHEMA_VERSION,
  confirmFormalStocktakeWrite,
  stocktakeIntentRetryState,
  validateFormalStocktakeDetail,
  validateFormalStocktakePage,
  validateFormalStocktakeWriteResult,
  type FormalStocktakeDetail,
  type FormalStocktakeIntent,
  type FormalStocktakePage,
} from "./formalStocktakes";
import type { FormalStocktakeCountOperation } from "./formalStocktakeCountRecoveryStore";
import type { FormalStocktakeReviewStage } from "./formalStocktakeReviewRecoveryStore";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const SAFE_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const OPERATIONAL_ROLES = new Set(["admin", "provincial_manager", "technician"]);
const SUPPORTED_ROLES = new Set([...OPERATIONAL_ROLES, "star_headquarters_approver"]);

export type FormalStocktakeExpectedIdentity = Readonly<{
  person_id: string;
  authorization_version: number;
}>;

export type FormalStocktakeAccess = Readonly<{
  schema_version: typeof FORMAL_STOCKTAKE_SCHEMA_VERSION;
  person_id: string;
  authorization_version: number;
  can_read: boolean;
  can_count: boolean;
  can_manage: boolean;
  can_review_region: boolean;
  can_review_headquarters: boolean;
  can_post: boolean;
  can_reconcile: boolean;
  can_close: boolean;
}>;

export type StocktakeRegionOption = Readonly<{
  region_org_id: string;
  code: string;
  name: string;
  province_code: string | null;
}>;
export type StocktakeLocationOption = Readonly<{
  location_id: string;
  code: string;
  name: string;
  location_type: "region" | "personal";
  owner_org_id: string;
  owner_org_name: string;
  custodian_person_id: string | null;
  custodian_name: string | null;
}>;
export type StocktakeAssigneeOption = Readonly<{
  assignee_user_id: string;
  person_id: string;
  name: string;
  employee_no: string;
  role_codes: readonly ("admin" | "provincial_manager" | "technician")[];
}>;

type Requester = (path: string, init?: RequestInit) => Promise<unknown>;

export type FormalStocktakeIdentity = Readonly<{
  person_id: string;
  authorization_version: number;
}>;

export type FormalStocktakeBeforeWriteContext = Readonly<{
  intent: FormalStocktakeIntent;
  access: FormalStocktakeAccess;
  before: FormalStocktakeDetail | null;
}>;

export type FormalStocktakeExecuteOptions = Readonly<{
  /** Called after every normal preflight, immediately before the single POST. */
  beforeWrite?: (context: FormalStocktakeBeforeWriteContext) => Promise<void>;
  /** Durable commands must use the no-replay requester for every read. */
  noReplayReads?: boolean;
}>;

export interface FormalStocktakeAdapter {
  loadAccess(): Promise<FormalStocktakeAccess>;
  /** Recovery-only access read, wired to the no-replay requester. */
  loadAccessNoReplay?(): Promise<FormalStocktakeAccess>;
  /** Raw identity response; recovery modules perform the stricter shape check. */
  loadIdentity?(): Promise<unknown>;
  /** Recovery-only identity read, wired to the no-replay requester. */
  loadIdentityNoReplay?(): Promise<unknown>;
  /** Read-only historical lookup. It must never carry a body or idempotency key. */
  postingCommandStatus?(
    taskId: string,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ): Promise<unknown>;
  /** Read-only historical lookup for one non-opening initial/recount scope count. */
  countCommandStatus?(
    taskId: string,
    roundId: string,
    scopeId: string,
    operation: FormalStocktakeCountOperation,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ): Promise<unknown>;
  /** Read-only historical lookup for one non-opening review command. */
  reviewCommandStatus?(
    taskId: string,
    roundId: string,
    reviewStage: FormalStocktakeReviewStage,
    actorPersonId: string,
    actorAuthorizationVersion: number,
    traceRequestId: string,
  ): Promise<unknown>;
  /** Recovery-only detail read, wired to the no-replay requester. */
  detailNoReplay?(taskId: string): Promise<FormalStocktakeDetail>;
  list(afterId?: string | null): Promise<FormalStocktakePage>;
  detail(taskId: string): Promise<FormalStocktakeDetail>;
  listRegions(afterId?: string | null): Promise<Readonly<{ items: readonly StocktakeRegionOption[]; next_after_id: string | null }>>;
  listLocations(regionOrgId: string, afterId?: string | null): Promise<Readonly<{ items: readonly StocktakeLocationOption[]; next_after_id: string | null }>>;
  listAssignees(regionOrgId: string, locationId: string, afterPersonId?: string | null): Promise<Readonly<{ items: readonly StocktakeAssigneeOption[]; next_after_person_id: string | null }>>;
  execute(intent: FormalStocktakeIntent, options?: FormalStocktakeExecuteOptions): Promise<Readonly<{ result: Readonly<Record<string, unknown>>; detail: FormalStocktakeDetail }>>;
}

function fail(message: string, status = 409): never {
  throw new ApiError(status, message);
}

function objectValue(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(`${name}不是有效对象`);
  return value as Record<string, unknown>;
}

function exact(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  const object = objectValue(value, name);
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    return fail(`${name}必须精确包含正式字段`);
  }
  return object;
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) return fail(`${name}无效`);
  return value.toLowerCase();
}

function nullableUuid(value: unknown, name: string): string | null {
  return value === null ? null : uuid(value, name);
}

function text(value: unknown, name: string, allowEmpty = false): string {
  if (typeof value !== "string" || value !== value.trim() || (!allowEmpty && !value)) return fail(`${name}无效`);
  return value;
}

function positiveVersion(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) return fail(`${name}无效`);
  return value as number;
}

function timestamp(value: unknown, name: string): string {
  if (typeof value !== "string" || !AWARE_TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) return fail(`${name}无效`);
  return value;
}

function projectAccess(value: unknown, expected: FormalStocktakeExpectedIdentity): FormalStocktakeAccess {
  const row = exact(value, ["person_id", "account_status", "employment_status", "authorization_version", "access_mode", "role_codes", "assignments", "permissions"], "正式访问上下文");
  const personId = uuid(row.person_id, "person_id");
  const authorizationVersion = positiveVersion(row.authorization_version, "authorization_version");
  if (personId !== uuid(expected.person_id, "expected.person_id") || authorizationVersion !== positiveVersion(expected.authorization_version, "expected.authorization_version")) fail("登录身份或授权版本与正式盘点上下文不一致");
  if (row.account_status !== "active" || row.employment_status !== "active" || row.access_mode !== "active") fail("当前身份不是正式盘点允许的有效在职状态", 403);
  if (!Array.isArray(row.role_codes) || !row.role_codes.length) fail("正式盘点角色缺失", 403);
  const roles = row.role_codes.map((role) => text(role, "role_code"));
  if (new Set(roles).size !== roles.length || roles.some((role) => !SUPPORTED_ROLES.has(role)) || !roles.some((role) => OPERATIONAL_ROLES.has(role))) fail("正式盘点角色无效", 403);
  if (!Array.isArray(row.assignments) || !Array.isArray(row.permissions)) fail("正式盘点授权结构无效");
  let nationalAdminAssignmentCount = 0;
  const assignmentKeys = row.assignments.map((value, index) => {
    const item = exact(value, ["assignment_id", "role_code", "scope_type", "scope_id", "valid_from", "valid_to"], `授权范围 ${index + 1}`);
    const assignmentId = uuid(item.assignment_id, "assignment_id");
    const role = text(item.role_code, "assignment.role_code");
    if (!SUPPORTED_ROLES.has(role)) fail("授权范围包含未知角色");
    const scopeType = text(item.scope_type, "scope_type");
    if (!["national", "organization", "person"].includes(scopeType)) fail("授权范围类型无效");
    const scopeId = text(item.scope_id, "scope_id");
    if (scopeType === "national") {
      if (scopeId !== "*") fail("全国授权范围必须使用 *");
    } else {
      uuid(scopeId, "scope_id");
    }
    timestamp(item.valid_from, "valid_from");
    if (item.valid_to !== null) timestamp(item.valid_to, "valid_to");
    if (role === "admin" && scopeType === "national" && scopeId === "*") nationalAdminAssignmentCount += 1;
    return assignmentId;
  });
  if (new Set(assignmentKeys).size !== assignmentKeys.length) fail("正式访问上下文包含重复授权范围");
  const permissionKeys = row.permissions.map((value, index) => {
    const item = exact(value, ["resource", "action", "field_code"], `正式权限 ${index + 1}`);
    return `${text(item.resource, "permission.resource")}\u0000${text(item.action, "permission.action")}\u0000${text(item.field_code, "permission.field_code", true)}`;
  });
  if (new Set(permissionKeys).size !== permissionKeys.length) fail("正式访问上下文包含重复权限");
  const has = (action: string) => permissionKeys.includes(`stocktake\u0000${action}\u0000`);
  const canRead = has("read");
  const exactNationalAdmin = roles.includes("admin") && nationalAdminAssignmentCount === 1;
  return Object.freeze({ schema_version: FORMAL_STOCKTAKE_SCHEMA_VERSION, person_id: personId, authorization_version: authorizationVersion, can_read: canRead, can_count: canRead && has("count"), can_manage: canRead && has("manage"), can_review_region: canRead && has("review_region"), can_review_headquarters: canRead && has("review_headquarters"), can_post: canRead && exactNationalAdmin && has("post_difference"), can_reconcile: canRead && exactNationalAdmin && has("reconcile"), can_close: canRead && exactNationalAdmin && has("close") });
}

function optionPage(value: unknown, type: "region" | "location" | "assignee", expected: FormalStocktakeExpectedIdentity, regionOrgId?: string, locationId?: string) {
  const pageKeys = type === "region"
    ? ["schema_version", "items", "next_after_id", "authorization_version"]
    : type === "location"
      ? ["schema_version", "region_org_id", "items", "next_after_id", "authorization_version"]
      : ["schema_version", "region_org_id", "location_id", "items", "next_after_person_id", "authorization_version"];
  const row = exact(value, pageKeys, "盘点受控选择项");
  if (row.schema_version !== FORMAL_STOCKTAKE_SCHEMA_VERSION || positiveVersion(row.authorization_version, "authorization_version") !== expected.authorization_version) fail("盘点选择项授权版本不连续");
  if (type !== "region" && uuid(row.region_org_id, "region_org_id") !== uuid(regionOrgId, "expected.region_org_id")) fail("盘点选择项区域锚点不一致");
  if (type === "assignee" && uuid(row.location_id, "location_id") !== uuid(locationId, "expected.location_id")) fail("盘点选择项库位锚点不一致");
  if (!Array.isArray(row.items)) fail("盘点选择项必须是数组");
  const items = row.items.map((value) => {
    if (type === "region") {
      const item = exact(value, ["region_org_id", "code", "name", "province_code"], "区域选择项");
      return Object.freeze({ region_org_id: uuid(item.region_org_id, "region_org_id"), code: text(item.code, "code"), name: text(item.name, "name"), province_code: item.province_code === null ? null : text(item.province_code, "province_code") });
    }
    if (type === "location") {
      const item = exact(value, ["location_id", "code", "name", "location_type", "owner_org_id", "owner_org_name", "custodian_person_id", "custodian_name"], "库位选择项");
      if (item.location_type !== "region" && item.location_type !== "personal") fail("库位类型无效");
      return Object.freeze({ location_id: uuid(item.location_id, "location_id"), code: text(item.code, "code"), name: text(item.name, "name"), location_type: item.location_type, owner_org_id: uuid(item.owner_org_id, "owner_org_id"), owner_org_name: text(item.owner_org_name, "owner_org_name"), custodian_person_id: nullableUuid(item.custodian_person_id, "custodian_person_id"), custodian_name: item.custodian_name === null ? null : text(item.custodian_name, "custodian_name") });
    }
    const item = exact(value, ["assignee_user_id", "person_id", "name", "employee_no", "role_codes"], "盘点人员选择项");
    if (!Array.isArray(item.role_codes) || !item.role_codes.length) fail("盘点人员角色无效");
    const roleCodes = item.role_codes.map((role) => {
      const checked = text(role, "assignee.role_code");
      if (!OPERATIONAL_ROLES.has(checked)) fail("盘点人员角色无效");
      return checked as "admin" | "provincial_manager" | "technician";
    });
    if (new Set(roleCodes).size !== roleCodes.length) fail("盘点人员角色重复");
    const assigneeUserId = text(item.assignee_user_id, "assignee_user_id");
    if (assigneeUserId.length > 160) fail("assignee_user_id 无效");
    return Object.freeze({ assignee_user_id: assigneeUserId, person_id: uuid(item.person_id, "person_id"), name: text(item.name, "name"), employee_no: text(item.employee_no, "employee_no"), role_codes: Object.freeze(roleCodes) });
  });
  const ids = items.map((item) => "region_org_id" in item ? item.region_org_id : "location_id" in item ? item.location_id : item.person_id);
  if (new Set(ids).size !== ids.length) fail("盘点选择项包含重复标识");
  if (type === "assignee") {
    const userIds = (items as readonly StocktakeAssigneeOption[]).map((item) => item.assignee_user_id);
    if (new Set(userIds).size !== userIds.length) fail("盘点人员选择项包含重复用户标识");
  }
  return Object.freeze({ items: Object.freeze(items), next_after_id: type === "assignee" ? undefined : nullableUuid(row.next_after_id, "next_after_id"), next_after_person_id: type === "assignee" ? nullableUuid(row.next_after_person_id, "next_after_person_id") : undefined });
}

function expectedPath(intent: FormalStocktakeIntent): boolean {
  if (intent.action === "create_personal") return intent.path === "/v1/stocktakes/personal";
  if (intent.action === "create_managed") return intent.path === "/v1/stocktakes";
  const task = intent.taskId;
  if (!task) return false;
  if (intent.action === "start") return intent.path === `/v1/stocktakes/${task}/start`;
  if (intent.action === "post") return intent.path === `/v1/stocktakes/${task}/post-differences`;
  if (intent.action === "reconcile") return intent.path === `/v1/stocktakes/${task}/reconcile`;
  if (intent.action === "close") return intent.path === `/v1/stocktakes/${task}/close`;
  if (!intent.roundId) return false;
  const prefix = `/v1/stocktakes/${task}/rounds/${intent.roundId}`;
  if (intent.action === "submit_initial_count") return !!intent.scopeId && intent.path === `${prefix}/scopes/${intent.scopeId}/initial-count`;
  if (intent.action === "submit_recount_count") return !!intent.scopeId && intent.path === `${prefix}/scopes/${intent.scopeId}/recount-count`;
  if (intent.action === "generate_initial_differences") return intent.path === `${prefix}/differences`;
  if (intent.action === "generate_recount_differences") return intent.path === `${prefix}/recount-differences`;
  if (intent.action === "review_region") return intent.path === `${prefix}/reviews/region`;
  if (intent.action === "review_headquarters") return intent.path === `${prefix}/reviews/headquarters`;
  return intent.path === `${prefix}/recount`;
}

function permissionFor(access: FormalStocktakeAccess, action: FormalStocktakeIntent["action"]): boolean {
  if (action === "create_personal" || action === "start" || action === "submit_initial_count" || action === "submit_recount_count") return access.can_count;
  if (action === "review_region") return access.can_review_region;
  if (action === "review_headquarters") return access.can_review_headquarters;
  if (action === "post") return access.can_post;
  if (action === "reconcile") return access.can_reconcile;
  if (action === "close") return access.can_close;
  return access.can_manage;
}

function detailAllows(detail: FormalStocktakeDetail, intent: FormalStocktakeIntent): boolean {
  if (intent.action === "create_personal" || intent.action === "create_managed") return true;
  if (detail.task_id !== intent.taskId) return false;
  return stocktakeIntentRetryState(intent, detail) === "retryable";
}

function uncertain(error: unknown): boolean {
  if (!error || typeof error !== "object") return true;
  const candidate = error as { status?: unknown; write_result_uncertain?: unknown };
  if (candidate.write_result_uncertain === true || !Number.isInteger(candidate.status)) return true;
  const status = candidate.status as number;
  return status === 0 || status === 408 || status === 425 || status >= 500;
}

function markUncertain(error: unknown, retryState: "retryable" | "handoff_required"): Error {
  const result = error instanceof Error ? error : new ApiError(0, "盘点写结果未知");
  Object.assign(result, { write_result_uncertain: true, stocktake_retry_state: retryState });
  return result;
}

async function verifyRecountAssignees(adapter: FormalStocktakeAdapter, detail: FormalStocktakeDetail, intent: FormalStocktakeIntent): Promise<void> {
  const body = exact(intent.body, ["expected_task_version", "assignments", "reason"], "开复盘写意图");
  if (positiveVersion(body.expected_task_version, "expected_task_version") !== intent.expectedTaskVersion || !Array.isArray(body.assignments) || !body.assignments.length) fail("开复盘写意图无效");
  const availableByLocation = new Map<string, ReadonlySet<string>>();
  for (const value of body.assignments) {
    const assignment = exact(value, ["scope_id", "assignee_user_id"], "开复盘范围分配");
    const scopeId = uuid(assignment.scope_id, "scope_id");
    const scope = detail.scopes.find((item) => item.scope_id === scopeId);
    if (!scope) fail("开复盘范围不在当前详情内");
    const assigneeUserId = text(assignment.assignee_user_id, "assignee_user_id");
    if (assigneeUserId.length > 160) fail("assignee_user_id 无效");
    let available = availableByLocation.get(scope.location_id);
    if (!available) {
      const all = new Set<string>();
      let cursor: string | null = null;
      const seen = new Set<string>();
      for (let pageNo = 0; pageNo < 100; pageNo += 1) {
        const page = await adapter.listAssignees(detail.region_org_id, scope.location_id, cursor);
        page.items.forEach((item) => all.add(item.assignee_user_id));
        if (!page.next_after_person_id) { cursor = null; break; }
        if (seen.has(page.next_after_person_id)) fail("盘点人员目录分页游标重复");
        seen.add(page.next_after_person_id); cursor = page.next_after_person_id;
      }
      if (cursor) fail("盘点人员目录分页超出安全上限");
      available = all; availableByLocation.set(scope.location_id, available);
    }
    if (!available.has(assigneeUserId)) fail("复盘人员不在当前范围正式受控目录内", 403);
  }
}

export function createFormalStocktakeAdapter(
  expectedIdentity: FormalStocktakeExpectedIdentity,
  requester: Requester = api,
  mutationRequester: Requester = requester,
  statusRequester: Requester = mutationRequester,
): FormalStocktakeAdapter {
  const expected = Object.freeze({ person_id: uuid(expectedIdentity.person_id, "expected.person_id"), authorization_version: positiveVersion(expectedIdentity.authorization_version, "expected.authorization_version") });
  const noStore = {
    cache: "no-store" as RequestCache,
    headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
  };

  const adapter: FormalStocktakeAdapter = {
    async loadIdentity() {
      return requester("/auth/me", {
        method: "GET",
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    async loadIdentityNoReplay() {
      return statusRequester("/auth/me", {
        method: "GET",
        cache: "no-store",
        headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
      });
    },
    async loadAccess() {
      return projectAccess(await requester("/access/context", noStore), expected);
    },
    async loadAccessNoReplay() {
      return projectAccess(await statusRequester("/access/context", noStore), expected);
    },
    async postingCommandStatus(taskId, actorPersonId, actorAuthorizationVersion, traceRequestId) {
      const query = new URLSearchParams({
        actor_person_id: uuid(actorPersonId, "actor_person_id"),
        actor_authorization_version: String(positiveVersion(actorAuthorizationVersion, "actor_authorization_version")),
        trace_request_id: text(traceRequestId, "trace_request_id"),
      });
      return statusRequester(
        `/v1/stocktakes/${uuid(taskId, "task_id")}/post-differences-command-status?${query.toString()}`,
        {
          method: "GET",
          cache: "no-store",
          headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
        },
      );
    },
    async countCommandStatus(taskId, roundId, scopeId, operation, actorPersonId, actorAuthorizationVersion, traceRequestId) {
      const query = new URLSearchParams({
        operation,
        actor_person_id: uuid(actorPersonId, "actor_person_id"),
        actor_authorization_version: String(positiveVersion(actorAuthorizationVersion, "actor_authorization_version")),
        trace_request_id: text(traceRequestId, "trace_request_id"),
      });
      return statusRequester(
        `/v1/stocktakes/${uuid(taskId, "task_id")}/rounds/${uuid(roundId, "round_id")}/scopes/${uuid(scopeId, "scope_id")}/count-command-status?${query.toString()}`,
        {
          method: "GET",
          cache: "no-store",
          headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
        },
      );
    },
    async reviewCommandStatus(taskId, roundId, reviewStage, actorPersonId, actorAuthorizationVersion, traceRequestId) {
      if (reviewStage !== "region" && reviewStage !== "headquarters") fail("review_stage 无效");
      const query = new URLSearchParams({
        actor_person_id: uuid(actorPersonId, "actor_person_id"),
        actor_authorization_version: String(positiveVersion(actorAuthorizationVersion, "actor_authorization_version")),
        trace_request_id: text(traceRequestId, "trace_request_id"),
      });
      return statusRequester(
        `/v1/stocktakes/${uuid(taskId, "task_id")}/rounds/${uuid(roundId, "round_id")}/reviews/${reviewStage}/command-status?${query.toString()}`,
        {
          method: "GET",
          cache: "no-store",
          headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
        },
      );
    },
    async list(afterId = null) {
      const suffix = afterId ? `&after_id=${uuid(afterId, "after_id")}` : "";
      return validateFormalStocktakePage(await requester(`/v1/stocktakes?limit=50${suffix}`, noStore));
    },
    async detail(taskId) {
      return validateFormalStocktakeDetail(await requester(`/v1/stocktakes/${uuid(taskId, "task_id")}`, noStore));
    },
    async detailNoReplay(taskId) {
      return validateFormalStocktakeDetail(await statusRequester(`/v1/stocktakes/${uuid(taskId, "task_id")}`, noStore));
    },
    async listRegions(afterId = null) {
      const suffix = afterId ? `&after_id=${uuid(afterId, "after_id")}` : "";
      const page = optionPage(await requester(`/v1/stocktake-options/regions?limit=100${suffix}`, noStore), "region", expected);
      return Object.freeze({ items: page.items as readonly StocktakeRegionOption[], next_after_id: page.next_after_id ?? null });
    },
    async listLocations(regionOrgId, afterId = null) {
      const region = uuid(regionOrgId, "region_org_id");
      const suffix = afterId ? `&after_id=${uuid(afterId, "after_id")}` : "";
      const page = optionPage(await requester(`/v1/stocktake-options/locations?region_org_id=${region}&limit=100${suffix}`, noStore), "location", expected, region);
      return Object.freeze({ items: page.items as readonly StocktakeLocationOption[], next_after_id: page.next_after_id ?? null });
    },
    async listAssignees(regionOrgId, locationId, afterPersonId = null) {
      const region = uuid(regionOrgId, "region_org_id");
      const location = uuid(locationId, "location_id");
      const suffix = afterPersonId ? `&after_person_id=${uuid(afterPersonId, "after_person_id")}` : "";
      const page = optionPage(await requester(`/v1/stocktake-options/assignees?region_org_id=${region}&location_id=${location}&limit=100${suffix}`, noStore), "assignee", expected, region, location);
      return Object.freeze({ items: page.items as readonly StocktakeAssigneeOption[], next_after_person_id: page.next_after_person_id ?? null });
    },
    async execute(intent, options = {}) {
      if (!intent || intent.method !== "POST" || !expectedPath(intent) || !SAFE_IDEMPOTENCY_KEY.test(intent.headers?.["Idempotency-Key"] || "") || !SAFE_REQUEST_ID.test(intent.headers?.["X-Request-ID"] || "")) fail("盘点写意图路径或坐标无效");
      const readAccess = options.noReplayReads ? adapter.loadAccessNoReplay : adapter.loadAccess;
      const readDetail = options.noReplayReads ? adapter.detailNoReplay : adapter.detail;
      if (!readAccess || !readDetail) fail("当前盘点客户端缺少持久过账所需的无重放读取能力");
      const access = await readAccess();
      if (!access.can_read || !permissionFor(access, intent.action)) fail("当前正式权限不允许该盘点动作", 403);
      let before: FormalStocktakeDetail | null = null;
      if (intent.taskId) {
        before = await readDetail(intent.taskId);
        if (!detailAllows(before, intent)) fail("详情 allowed_actions 与当前权限未同时授权该动作", 409);
        if (intent.action === "open_recount") await verifyRecountAssignees(adapter, before, intent);
      }
      if (options.beforeWrite) await options.beforeWrite(Object.freeze({ intent, access, before }));
      let rawResult: unknown;
      try {
        rawResult = await mutationRequester(intent.path, { method: intent.method, headers: intent.headers, ...jsonBody(intent.body) });
      } catch (error) {
        if (!uncertain(error)) throw error;
        let retryState: "retryable" | "handoff_required" = intent.action === "create_personal" || intent.action === "create_managed" ? "retryable" : "handoff_required";
        if (intent.taskId) {
          try { retryState = stocktakeIntentRetryState(intent, await readDetail(intent.taskId)); } catch { /* keep handoff */ }
        }
        throw markUncertain(error, retryState);
      }
      let result: Readonly<Record<string, unknown>>;
      try {
        result = validateFormalStocktakeWriteResult(intent, rawResult);
        await readAccess();
        const detail = await readDetail(uuid(result.task_id, "result.task_id"));
        confirmFormalStocktakeWrite(intent, result, detail);
        return Object.freeze({ result, detail });
      } catch (error) {
        throw markUncertain(error, "handoff_required");
      }
    },
  };
  return adapter;
}

export function isFormalStocktakeWriteUncertain(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && (error as { write_result_uncertain?: boolean }).write_result_uncertain);
}

export function formalStocktakeRetryState(error: unknown): "retryable" | "handoff_required" | null {
  if (!error || typeof error !== "object") return null;
  const value = (error as { stocktake_retry_state?: unknown }).stocktake_retry_state;
  return value === "retryable" || value === "handoff_required" ? value : null;
}
