import { ApiError } from "./api";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

export const MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION = "1.0" as const;
export const MATERIAL_REQUEST_WORK_ORDER_OPTION_STATUSES = ["pending", "active"] as const;

export type MaterialRequestWorkOrderOption = Readonly<{
  work_order_id: string;
  work_order_no: string;
  status: typeof MATERIAL_REQUEST_WORK_ORDER_OPTION_STATUSES[number];
  source_system_code: "starcharge_oam";
  source_external_id: string;
  source_version: string;
  source_updated_at: string;
  synced_at: string;
  freshness_status: "fresh";
}>;

export type MaterialRequestWorkOrderOptionIdentity = Readonly<{
  person_id: string;
  authorization_version: number;
}>;

export type MaterialRequestWorkOrderOptionPage = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION;
  person_id: string;
  authorization_version: number;
  items: readonly MaterialRequestWorkOrderOption[];
  next_after_id: string | null;
}>;

export type MaterialRequestWorkOrderOptionDetail = Readonly<{
  schema_version: typeof MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION;
  person_id: string;
  authorization_version: number;
  item: MaterialRequestWorkOrderOption;
}>;

export class MaterialRequestWorkOrderOptionContractError extends ApiError {
  readonly code: string;

  constructor(code: string, message: string) {
    super(409, message);
    this.name = "MaterialRequestWorkOrderOptionContractError";
    this.code = code;
  }
}

function invalid(code: string, message: string): never {
  throw new MaterialRequestWorkOrderOptionContractError(code, message);
}

function record(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalid("material_request_work_order_option_object_invalid", `${name}不是有效对象`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(object: Record<string, unknown>, keys: readonly string[], name: string): void {
  const actual = Object.keys(object).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    invalid("material_request_work_order_option_shape_invalid", `${name}必须精确包含正式字段`);
  }
}

function uuid(value: unknown, name: string): string {
  if (typeof value !== "string" || !UUID.test(value) || value.toLowerCase() === ZERO_UUID) {
    return invalid("material_request_work_order_option_uuid_invalid", `${name}无效`);
  }
  return value.toLowerCase();
}

function version(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    return invalid("material_request_work_order_option_version_invalid", `${name}无效`);
  }
  return value as number;
}

function text(value: unknown, name: string, maximum: number): string {
  if (
    typeof value !== "string"
    || !value
    || value !== value.trim()
    || value.length > maximum
    || /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return invalid("material_request_work_order_option_text_invalid", `${name}无效`);
  }
  return value;
}

function awareTimestamp(value: unknown, name: string): string {
  if (
    typeof value !== "string"
    || !AWARE_TIMESTAMP.test(value)
    || !Number.isFinite(Date.parse(value))
  ) {
    return invalid(
      "material_request_work_order_option_timestamp_invalid",
      `${name} 必须是包含时区的有效时间`,
    );
  }
  return value;
}

function option(value: unknown): MaterialRequestWorkOrderOption {
  const object = record(value, "正式工单选择项");
  exactKeys(
    object,
    [
      "work_order_id", "work_order_no", "status", "source_system_code",
      "source_external_id", "source_version", "source_updated_at", "synced_at",
      "freshness_status",
    ],
    "正式工单选择项",
  );
  if (
    typeof object.status !== "string"
    || !MATERIAL_REQUEST_WORK_ORDER_OPTION_STATUSES.includes(
      object.status as typeof MATERIAL_REQUEST_WORK_ORDER_OPTION_STATUSES[number],
    )
  ) {
    return invalid("material_request_work_order_option_status_invalid", "正式工单状态无效");
  }
  if (object.freshness_status !== "fresh") {
    return invalid(
      "material_request_work_order_option_freshness_invalid",
      "正式工单来源投影不是 fresh，禁止选择",
    );
  }
  if (object.source_system_code !== "starcharge_oam") {
    return invalid(
      "material_request_work_order_option_source_invalid",
      "正式工单来源系统不是 starcharge_oam",
    );
  }
  const sourceUpdatedAt = awareTimestamp(object.source_updated_at, "source_updated_at");
  const syncedAt = awareTimestamp(object.synced_at, "synced_at");
  if (Date.parse(sourceUpdatedAt) > Date.parse(syncedAt)) {
    return invalid(
      "material_request_work_order_option_timestamp_order_invalid",
      "正式工单来源更新时间不能晚于本次同步时间",
    );
  }
  return Object.freeze({
    work_order_id: uuid(object.work_order_id, "work_order_id"),
    work_order_no: text(object.work_order_no, "work_order_no", 100),
    status: object.status as typeof MATERIAL_REQUEST_WORK_ORDER_OPTION_STATUSES[number],
    source_system_code: "starcharge_oam",
    source_external_id: text(object.source_external_id, "source_external_id", 250),
    source_version: text(object.source_version, "source_version", 160),
    source_updated_at: sourceUpdatedAt,
    synced_at: syncedAt,
    freshness_status: "fresh",
  });
}

function assertIdentity(
  personId: string,
  authorizationVersion: number,
  expected: MaterialRequestWorkOrderOptionIdentity,
): void {
  if (
    personId !== uuid(expected.person_id, "expected_person_id")
    || authorizationVersion !== version(
      expected.authorization_version,
      "expected_authorization_version",
    )
  ) {
    invalid(
      "material_request_work_order_option_identity_stale",
      "工单选项身份或授权版本与当前页面不一致",
    );
  }
}

export function validateMaterialRequestWorkOrderOptionQuery(value: string): string {
  if (
    typeof value !== "string"
    || value !== value.trim()
    || value.length > 100
    || /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return invalid("material_request_work_order_option_query_invalid", "工单检索词无效");
  }
  return value;
}

export function validateMaterialRequestWorkOrderOptionPage(
  value: unknown,
  expected: MaterialRequestWorkOrderOptionIdentity,
): MaterialRequestWorkOrderOptionPage {
  const object = record(value, "正式工单选项分页");
  exactKeys(
    object,
    ["schema_version", "person_id", "authorization_version", "items", "next_after_id"],
    "正式工单选项分页",
  );
  if (object.schema_version !== MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION) {
    return invalid("material_request_work_order_option_version_unsupported", "工单选项版本不受支持");
  }
  const personId = uuid(object.person_id, "person_id");
  const authorizationVersion = version(object.authorization_version, "authorization_version");
  assertIdentity(personId, authorizationVersion, expected);
  if (!Array.isArray(object.items) || object.items.length > 100) {
    return invalid("material_request_work_order_option_items_invalid", "工单选项数量无效");
  }
  const items = object.items.map(option);
  if (
    new Set(items.map((item) => item.work_order_id)).size !== items.length
    || new Set(items.map((item) => item.work_order_no)).size !== items.length
  ) {
    return invalid("material_request_work_order_option_items_ambiguous", "工单选项包含重复记录");
  }
  const nextAfterId = object.next_after_id === null
    ? null
    : uuid(object.next_after_id, "next_after_id");
  if (nextAfterId && items.some((item) => item.work_order_id === nextAfterId)) {
    return invalid(
      "material_request_work_order_option_cursor_invalid",
      "工单选项下一页游标指向当前页记录",
    );
  }
  return Object.freeze({
    schema_version: MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION,
    person_id: personId,
    authorization_version: authorizationVersion,
    items: Object.freeze(items),
    next_after_id: nextAfterId,
  });
}

export function validateMaterialRequestWorkOrderOptionDetail(
  value: unknown,
  expected: MaterialRequestWorkOrderOptionIdentity & Readonly<{ work_order_id: string }>,
): MaterialRequestWorkOrderOptionDetail {
  const object = record(value, "正式工单选项详情");
  exactKeys(
    object,
    ["schema_version", "person_id", "authorization_version", "item"],
    "正式工单选项详情",
  );
  if (object.schema_version !== MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION) {
    return invalid("material_request_work_order_option_version_unsupported", "工单选项版本不受支持");
  }
  const personId = uuid(object.person_id, "person_id");
  const authorizationVersion = version(object.authorization_version, "authorization_version");
  assertIdentity(personId, authorizationVersion, expected);
  const item = option(object.item);
  if (item.work_order_id !== uuid(expected.work_order_id, "expected_work_order_id")) {
    return invalid(
      "material_request_work_order_option_detail_mismatch",
      "工单选项详情与草稿引用不一致",
    );
  }
  return Object.freeze({
    schema_version: MATERIAL_REQUEST_WORK_ORDER_OPTION_SCHEMA_VERSION,
    person_id: personId,
    authorization_version: authorizationVersion,
    item,
  });
}
