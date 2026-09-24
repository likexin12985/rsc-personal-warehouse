import { ApiError, apiNoReplay } from "./api";
import { openingPreparationContext, openingPreparationContextKey, type OpeningPreparationActor } from "./formalOpeningStartOptions";

export type ControlBatch = Readonly<{
  publicationId: string; sourceId: string; sourceName: string; capturedAt: string;
  publishedAt: string; validUntil: string; recordCount: number; isLatest: boolean;
}>;
export type ControlBatchPage = Readonly<{ items: readonly ControlBatch[]; nextAfterId: string | null }>;
const LIMIT = 50;
function invalid(): never { throw new ApiError(409, "控制批次目录已变化，请刷新后重选"); }
function exact(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== keys.length || keys.some((key) => !Object.hasOwn(row, key))) invalid();
  return row;
}
function uuid(value: unknown): string {
  if (typeof value !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
    || value === "00000000-0000-0000-0000-000000000000") invalid();
  return value.toLowerCase();
}
function instant(value: unknown): string {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value)
    || !Number.isFinite(Date.parse(value))) invalid();
  return value;
}
export function controlDirectoryKey(actor: OpeningPreparationActor, regionId: string): string {
  return openingPreparationContextKey(openingPreparationContext(actor, "asset-owners", { region_org_id: regionId }));
}
export function validateControlBatchPage(value: unknown, actor: OpeningPreparationActor, regionId: string,
  after: string | null = null): ControlBatchPage {
  const context = openingPreparationContext(actor, "asset-owners", { region_org_id: regionId });
  let previous = after === null ? null : uuid(after);
  const page = exact(value, ["schema_version", "actor_person_id", "authorization_version", "region_org_id",
    "start_ready", "admission_status", "items", "next_after_id"]);
  if (page.schema_version !== "rsc.opening_control_batches.v1" || page.start_ready !== false
    || page.admission_status !== "not_evaluated" || uuid(page.actor_person_id) !== context.actor_person_id
    || page.authorization_version !== context.authorization_version || uuid(page.region_org_id) !== context.region_org_id
    || !Array.isArray(page.items) || page.items.length > LIMIT) invalid();
  const items = page.items.map((value) => {
    const row = exact(value, ["publication_id", "source_system_id", "source_name", "captured_at", "published_at", "valid_until", "record_count", "is_latest"]);
    const id = uuid(row.publication_id);
    if (previous !== null && id <= previous) invalid();
    previous = id;
    if (typeof row.source_name !== "string" || !row.source_name || row.source_name !== row.source_name.trim()
      || row.source_name.length > 200 || /[\u0000-\u001f\u007f]/.test(row.source_name)
      || typeof row.record_count !== "number" || !Number.isSafeInteger(row.record_count) || row.record_count < 0
      || typeof row.is_latest !== "boolean") invalid();
    const capturedAt = instant(row.captured_at), publishedAt = instant(row.published_at), validUntil = instant(row.valid_until);
    if (Date.parse(capturedAt) > Date.parse(publishedAt) || Date.parse(publishedAt) >= Date.parse(validUntil)) invalid();
    return Object.freeze({ publicationId: id, sourceId: uuid(row.source_system_id), sourceName: row.source_name,
      capturedAt, publishedAt, validUntil, recordCount: row.record_count, isLatest: row.is_latest });
  });
  const nextAfterId = page.next_after_id === null ? null : uuid(page.next_after_id);
  if (nextAfterId !== null && (items.length !== LIMIT || previous !== nextAfterId)) invalid();
  return Object.freeze({ items: Object.freeze(items), nextAfterId });
}
export async function loadControlBatches(actor: OpeningPreparationActor, regionId: string, after: string | null = null): Promise<ControlBatchPage> {
  controlDirectoryKey(actor, regionId);
  const params = new URLSearchParams({ region_org_id: uuid(regionId), limit: String(LIMIT) });
  if (after !== null) params.set("after_id", uuid(after));
  const value = await apiNoReplay<unknown>(`/v1/stocktakes/opening/start-options/control-batches?${params}`, {
    method: "GET", cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
  });
  return validateControlBatchPage(value, actor, regionId, after);
}
