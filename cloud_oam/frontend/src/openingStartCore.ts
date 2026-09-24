/** Platform-neutral start/recovery protocol. The mini-program copy is generated. */
export type StartActor = Readonly<{ person_id: string; authorization_version: number }>;
export type StartScope = Readonly<{ owner_org_id: string; location_id: string; assignee_user_id: string; freeze_mode: "hard" | "cutoff_replay" }>;
export type StartInput = Readonly<{ publication_id: string; region_org_id: string; task_no: string;
  scopes: readonly StartScope[]; blind_count: boolean; deadline: string | null; note: string }>;
export type StartMarker = Readonly<{ v: 1; kind: "opening_start"; region_org_id: string; publication_id: string;
  actor_person_id: string; actor_authorization_version: number; trace_request_id: string }>;
export type StartRead = Readonly<{ kind: "missing" | "corrupt" | "unavailable" }> | Readonly<{ kind: "valid"; value: StartMarker }>;
export type StartLease = Readonly<{ read(): StartRead; persist(value: StartMarker): void; clearExact(value: StartMarker): void }>;
export type StartStorage = { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void };
export type StartLocks = { request<T>(name: string, options: { mode: "exclusive"; ifAvailable: true }, work: (lock: unknown) => Promise<T>): Promise<T> };
export type StartStore = Readonly<{ read(region: string): StartRead; withRegionLease<T>(region: string, work: (lease: StartLease) => Promise<T>): Promise<T> }>;
export type StartResult = Readonly<{ schema_version: "1.0"; task_id: string; task_no: string; status: "counting";
  cutoff_ledger_cursor: number; initial_round_id: string; scope_count: number; snapshot_line_count: number; control_line_count: number; replayed: boolean }>;
export type StartSeal = Readonly<{ seal_id: string; actor_person_id: string; authorization_version: number;
  region_org_id: string; publication_id: string; trace_request_id: string; sealed_at: string; permanent_nonexecution: true }>;
export type StartOutcome = Readonly<{ result: StartResult; recovered: boolean; seal?: never }> | Readonly<{ result: null; recovered: true; seal: StartSeal }>;
export const START_SEAL_CONFIRMATION = "终结后，该原启动请求将永久禁止执行。如果任务已经创建，只会恢复原任务。确认终结原请求？";
export type StartPorts = Readonly<{
  identity(actor: StartActor): Promise<void>;
  verifySelection(input: StartInput, actor: StartActor): Promise<void>;
  coordinates(): { requestId: string; idempotencyKey: string };
  post(input: StartInput, coordinates: { requestId: string; idempotencyKey: string }): Promise<unknown>;
  lookup(marker: StartMarker): Promise<unknown>;
  seal(marker: StartMarker): Promise<unknown>;
  detail(task: string): Promise<{ task_id: string; task_no: string; region_org_id: string }>;
}>;
export const START_STORAGE_PREFIX = "rsc-opening-start-v1:";
export const START_LOCK_PREFIX = "rsc-opening-start-region-v1:";
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]*$/;
export class OpeningStartError extends Error {}
export class OpeningStartPendingError extends OpeningStartError {
  constructor() { super("原启动请求仍待核验，请查询原结果，不能重新提交"); this.name = "OpeningStartPendingError"; }
}
export function startErrorMessage(error: unknown): string {
  return error instanceof OpeningStartError ? error.message : "启动或结果核验未完成，请保留原记录后重新核验";
}
function fail(message = "启动数据或恢复记录无法确认，已停止写入"): never { throw new OpeningStartError(message); }
function exact(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).length !== fields.length || fields.some((field) => !Object.prototype.hasOwnProperty.call(row, field))) fail();
  return row;
}
export function startUuid(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) fail(); return value; }
function integer(value: unknown, min = 0): number { if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min) fail(); return value; }
function label(value: unknown, max: number, empty = false): string {
  if (typeof value !== "string" || value.trim() !== value || (!empty && !value) || value.length > max || /[\u0000-\u001f\u007f]/.test(value)) fail();
  return value;
}
export function startActor(value: unknown): StartActor {
  const row = exact(value, ["person_id", "authorization_version"]);
  return Object.freeze({ person_id: startUuid(row.person_id), authorization_version: integer(row.authorization_version, 1) });
}
export function startActiveIdentity(value: unknown, expected: StartActor): void {
  const row = exact(value, ['person_id','name','employee_no','organization_code','organization_name',
    'account_status','employment_status','access_mode','authorization_version','role_codes']);
  if (row.person_id !== expected.person_id || row.authorization_version !== expected.authorization_version
    || row.account_status !== 'active' || row.employment_status !== 'active' || row.access_mode !== 'active') fail();
  for (const [key, max] of [['name',160],['employee_no',100],['organization_code',120],['organization_name',240]] as const) label(row[key], max);
  if (!Array.isArray(row.role_codes) || new Set(row.role_codes).size !== row.role_codes.length
    || row.role_codes.some((role) => !['admin','provincial_manager','technician','star_headquarters_approver'].includes(role))
    || !row.role_codes.some((role) => role === 'admin' || role === 'provincial_manager')) fail();
}
export function validateStartBatchPage(value: unknown, actor: StartActor, region: string, after: string | null = null) {
  const page = exact(value, ['schema_version','actor_person_id','authorization_version','region_org_id','start_ready','admission_status','items','next_after_id']);
  if (page.schema_version !== 'rsc.opening_control_batches.v1' || page.actor_person_id !== actor.person_id
    || page.authorization_version !== actor.authorization_version || page.region_org_id !== startUuid(region)
    || page.start_ready !== false || page.admission_status !== 'not_evaluated' || !Array.isArray(page.items) || page.items.length > 50) fail();
  let previous = after === null ? null : startUuid(after);
  const items = page.items.map((value) => {
    const row = exact(value, ['publication_id','source_system_id','source_name','captured_at','published_at','valid_until','record_count','is_latest']);
    const publicationId = startUuid(row.publication_id);
    if (previous !== null && publicationId <= previous) fail(); previous = publicationId;
    const capturedAt = instant(row.captured_at), publishedAt = instant(row.published_at), validUntil = instant(row.valid_until);
    if (typeof row.is_latest !== 'boolean' || Date.parse(capturedAt) > Date.parse(publishedAt) || Date.parse(publishedAt) >= Date.parse(validUntil)) fail();
    return Object.freeze({ publicationId, sourceId: startUuid(row.source_system_id), sourceName: label(row.source_name,200),
      capturedAt, publishedAt, validUntil, recordCount: integer(row.record_count), isLatest: row.is_latest });
  });
  const nextAfterId = page.next_after_id === null ? null : startUuid(page.next_after_id);
  if (nextAfterId !== null && (items.length !== 50 || nextAfterId !== previous)) fail();
  return Object.freeze({ items: Object.freeze(items), nextAfterId });
}
function instant(value: unknown): string {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(value)
    || !Number.isFinite(Date.parse(value))) fail();
  const local = value.slice(0, 19), base = Date.parse(local + "Z");
  if (!Number.isFinite(base) || new Date(base).toISOString().slice(0, 19) !== local) fail();
  return value;
}
export function validateStartInput(value: unknown): StartInput {
  const row = exact(value, ["publication_id", "region_org_id", "task_no", "scopes", "blind_count", "deadline", "note"]);
  const task = label(row.task_no, 100);
  if (!REFERENCE.test(task) || typeof row.blind_count !== "boolean" || !Array.isArray(row.scopes) || !row.scopes.length) fail();
  const seen = new Set<string>();
  const scopes = row.scopes.map((value): StartScope => {
    const scope = exact(value, ["owner_org_id", "location_id", "assignee_user_id", "freeze_mode"]);
    const owner = startUuid(scope.owner_org_id), location = startUuid(scope.location_id);
    const key = owner + ":" + location;
    if (seen.has(key) || !["hard", "cutoff_replay"].includes(scope.freeze_mode as string)) fail();
    seen.add(key);
    const user = label(scope.assignee_user_id, 36);
    if (!REFERENCE.test(user)) fail();
    return Object.freeze({ owner_org_id: owner, location_id: location, assignee_user_id: user, freeze_mode: scope.freeze_mode as StartScope["freeze_mode"] });
  });
  return Object.freeze({ publication_id: startUuid(row.publication_id), region_org_id: startUuid(row.region_org_id),
    task_no: task, scopes: Object.freeze(scopes), blind_count: row.blind_count, deadline: row.deadline === null ? null : instant(row.deadline), note: label(row.note, 10000, true) });
}
export function validateStartMarker(value: unknown): StartMarker {
  const row = exact(value, ["v", "kind", "region_org_id", "publication_id", "actor_person_id", "actor_authorization_version", "trace_request_id"]);
  if (row.v !== 1 || row.kind !== "opening_start" || typeof row.trace_request_id !== "string" || !TRACE.test(row.trace_request_id)) fail();
  return Object.freeze({ v: 1, kind: "opening_start", region_org_id: startUuid(row.region_org_id), publication_id: startUuid(row.publication_id),
    actor_person_id: startUuid(row.actor_person_id), actor_authorization_version: integer(row.actor_authorization_version, 1), trace_request_id: row.trace_request_id });
}
export function validateStartResult(value: unknown): StartResult {
  const row = exact(value, ["schema_version", "task_id", "task_no", "status", "cutoff_ledger_cursor", "initial_round_id", "scope_count", "snapshot_line_count", "control_line_count", "replayed"]);
  if (row.schema_version !== "1.0" || row.status !== "counting" || typeof row.replayed !== "boolean") fail();
  const taskNo = label(row.task_no, 100); if (!REFERENCE.test(taskNo)) fail();
  return Object.freeze({ schema_version: "1.0", task_id: startUuid(row.task_id), task_no: taskNo, status: "counting",
    cutoff_ledger_cursor: integer(row.cutoff_ledger_cursor), initial_round_id: startUuid(row.initial_round_id),
    scope_count: integer(row.scope_count, 1), snapshot_line_count: integer(row.snapshot_line_count), control_line_count: integer(row.control_line_count), replayed: row.replayed });
}
export function validateStartRecovery(value: unknown, marker: StartMarker, actor: StartActor): StartResult | StartSeal | null {
  const row = exact(value, ["schema_version", "actor_person_id", "authorization_version", "region_org_id", "publication_id", "outcome", "automatic_retry_allowed", "result", "seal"]);
  if (row.schema_version !== "rsc.opening_start_recovery.v2" || row.automatic_retry_allowed !== false
    || row.actor_person_id !== actor.person_id || row.authorization_version !== actor.authorization_version
    || row.region_org_id !== marker.region_org_id || row.publication_id !== marker.publication_id) fail();
  if (row.outcome === "not_observed") { if (row.result !== null || row.seal !== null) fail(); return null; }
  if (row.outcome === "sealed") {
    if (row.result !== null) fail();
    const seal = exact(row.seal, ['seal_id','actor_person_id','authorization_version','region_org_id','publication_id','trace_request_id','sealed_at','permanent_nonexecution']);
    const version = integer(seal.authorization_version, 1), sealedAt = instant(seal.sealed_at);
    if (seal.actor_person_id !== marker.actor_person_id || seal.actor_person_id !== actor.person_id
      || seal.region_org_id !== marker.region_org_id || seal.publication_id !== marker.publication_id
      || seal.trace_request_id !== marker.trace_request_id || version < marker.actor_authorization_version
      || version > actor.authorization_version || seal.permanent_nonexecution !== true) fail();
    return Object.freeze({ seal_id: startUuid(seal.seal_id), actor_person_id: actor.person_id, authorization_version: version,
      region_org_id: marker.region_org_id, publication_id: marker.publication_id, trace_request_id: marker.trace_request_id,
      sealed_at: sealedAt, permanent_nonexecution: true });
  }
  if (row.outcome !== "found" || row.seal !== null) fail();
  const result = validateStartResult(row.result);
  if (!result.replayed) fail();
  return result;
}

/** The region lease spans preflight, persistence, POST and all recovery reads. */
export function createStartStore(storage: StartStorage | null, locks: StartLocks | null): StartStore {
  const faults = new Set<string>();
  function read(region: string): StartRead {
    if (!storage || faults.has(region)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(START_STORAGE_PREFIX + region);
      if (raw === null) return { kind: "missing" };
      try {
        const marker = validateStartMarker(JSON.parse(raw));
        return marker.region_org_id === region ? { kind: "valid", value: marker } : { kind: "corrupt" };
      } catch { return { kind: "corrupt" }; }
    } catch { faults.add(region); return { kind: "unavailable" }; }
  }
  return Object.freeze({
    read(region) { return read(startUuid(region)); },
    async withRegionLease(region, work) {
      startUuid(region);
      if (!locks || !storage || faults.has(region)) fail("持久恢复或页面协调不可用，已停止启动");
      return locks.request(START_LOCK_PREFIX + region, { mode: "exclusive", ifAvailable: true }, async (lock) => {
        if (!lock) fail("其他页面正在核验本区域启动，请稍后查询结果");
        let live = true;
        const check = () => { if (!live || faults.has(region)) fail(); };
        const checked = (value: StartMarker) => { check(); const marker = validateStartMarker(value); if (marker.region_org_id !== region) fail(); return marker; };
        const lease: StartLease = Object.freeze({
          read() { check(); return read(region); },
          persist(value) {
            const marker = checked(value), serialized = JSON.stringify(marker);
            if (read(region).kind !== "missing") fail("本区域已有待核验启动，禁止覆盖原记录");
            try {
              storage.setItem(START_STORAGE_PREFIX + region, serialized);
              const after = read(region);
              if (after.kind !== "valid" || JSON.stringify(after.value) !== serialized) fail();
            } catch { faults.add(region); fail("恢复记录未可靠保存，未发送启动请求"); }
          },
          clearExact(value) {
            const marker = checked(value), before = read(region);
            if (before.kind !== "valid" || JSON.stringify(before.value) !== JSON.stringify(marker)) fail();
            try { storage.removeItem(START_STORAGE_PREFIX + region); if (read(region).kind !== "missing") fail(); }
            catch { faults.add(region); fail("恢复记录清理未确认，请继续查询原结果"); }
          },
        });
        try { if (read(region).kind === "unavailable") fail(); return await work(lease); } finally { live = false; }
      });
    },
  });
}
function current(canContinue: () => boolean): void { if (!canContinue()) fail("页面或身份已变化，请重新打开后核验"); }
async function identity(ports: StartPorts, actor: StartActor, live: () => boolean) {
  current(live); await ports.identity(actor); current(live);
}
async function recover(lease: StartLease, marker: StartMarker, actor: StartActor, ports: StartPorts, live: () => boolean,
  direct: StartResult | null = null): Promise<StartOutcome> {
  try {
    // Historical authorization is not a new permission. A later current grant
    // may read history only for the same person, under fresh server checks.
    if (actor.person_id !== marker.actor_person_id || actor.authorization_version < marker.actor_authorization_version) fail();
    await identity(ports, actor, live);
    const first = validateStartRecovery(await ports.lookup(marker), marker, actor); current(live);
    if (!first) throw new OpeningStartPendingError();
    if (direct && JSON.stringify({ ...direct, replayed: true }) !== JSON.stringify(first)) fail();
    if (!('seal_id' in first)) {
      const detail = await ports.detail(first.task_id); current(live);
      if (detail.task_id !== first.task_id || detail.task_no !== first.task_no || detail.region_org_id !== marker.region_org_id) fail();
    }
    await identity(ports, actor, live);
    const second = validateStartRecovery(await ports.lookup(marker), marker, actor); current(live);
    if (!second || JSON.stringify(first) !== JSON.stringify(second)) fail();
    await identity(ports, actor, live);
    lease.clearExact(marker);
    return 'seal_id' in first ? Object.freeze({ result: null, recovered: true, seal: first })
      : Object.freeze({ result: first, recovered: direct === null || direct.replayed });
  } catch { throw new OpeningStartPendingError(); }
}
export async function recoverOpeningStart(options: { region: string; actor: StartActor; store: StartStore; ports: StartPorts; canContinue: () => boolean }): Promise<StartOutcome> {
  const actor = startActor(options.actor);
  return options.store.withRegionLease(options.region, async (lease) => {
    const row = lease.read();
    if (row.kind !== "valid") fail("没有可核验的完整启动记录；不能据此重试");
    return recover(lease, row.value, actor, options.ports, options.canContinue);
  });
}
/** Only an explicit user confirmation may request a permanent negative fact. */
export async function sealOpeningStart(options: { region: string; actor: StartActor; store: StartStore; ports: StartPorts;
  canContinue: () => boolean; confirm: () => Promise<boolean> }): Promise<StartOutcome> {
  const actor = startActor(options.actor), { ports, canContinue: live } = options;
  return options.store.withRegionLease(options.region, async (lease) => {
    const row = lease.read();
    if (row.kind !== 'valid') fail('没有可核验的完整原启动记录，不能终结');
    const marker = row.value;
    if (actor.person_id !== marker.actor_person_id || actor.authorization_version < marker.actor_authorization_version) fail();
    await identity(ports, actor, live);
    const known = validateStartRecovery(await ports.lookup(marker), marker, actor); current(live);
    if (known) return recover(lease, marker, actor, ports, live);
    if (await options.confirm() !== true) fail('已取消终结，原启动记录已保留');
    await identity(ports, actor, live);
    const before = lease.read();
    if (before.kind !== 'valid' || JSON.stringify(before.value) !== JSON.stringify(marker)) fail();
    try { await ports.seal(marker); } catch { /* Unknown delivery is resolved by independent reads only. */ }
    return recover(lease, marker, actor, ports, live);
  });
}
function definitiveRejection(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const error = value as Record<string, unknown>;
  // Only the first direct POST's named rollback-before-start admission refusal.
  // Generic 401/403/409/422/5xx or later GET errors never release a marker.
  return error.responseReceived === true && error.status === 412 && error.category === "precondition_failed"
    && error.code === "control_publication_not_admissible";
}
export async function submitOpeningStart(options: { input: StartInput; actor: StartActor; store: StartStore; ports: StartPorts; canContinue: () => boolean; enabled: boolean }): Promise<StartOutcome> {
  const actor = startActor(options.actor), input = validateStartInput(JSON.parse(JSON.stringify(options.input)));
  const { ports, canContinue: live } = options;
  return options.store.withRegionLease(input.region_org_id, async (lease) => {
    const existing = lease.read();
    if (existing.kind === "valid") return recover(lease, existing.value, actor, ports, live);
    if (existing.kind !== "missing") fail("本区域启动恢复记录不可用，禁止新提交");
    if (options.enabled !== true) fail("启动功能尚未开放");
    try {
      await identity(ports, actor, live);
      await ports.verifySelection(input, actor); current(live);
      await identity(ports, actor, live);
    } catch { fail("当前身份、权限或所选范围与批次未核验通过，请刷新后重新选择"); }
    const coordinates = Object.freeze({ ...ports.coordinates() });
    if (!TRACE.test(coordinates.requestId) || !/^[A-Za-z0-9][A-Za-z0-9._:-]{15,199}$/.test(coordinates.idempotencyKey)) fail();
    const marker = validateStartMarker({ v: 1, kind: "opening_start", region_org_id: input.region_org_id,
      publication_id: input.publication_id, actor_person_id: actor.person_id, actor_authorization_version: actor.authorization_version,
      trace_request_id: coordinates.requestId });
    lease.persist(marker);
    let raw: unknown;
    try {
      current(live);
      const read = lease.read(); if (read.kind !== "valid" || JSON.stringify(read.value) !== JSON.stringify(marker)) throw new OpeningStartPendingError();
      raw = await ports.post(input, coordinates);
    } catch (error) {
      if (definitiveRejection(error)) {
        try { await identity(ports, actor, live); lease.clearExact(marker); } catch { throw new OpeningStartPendingError(); }
        fail("所选批次已不能启动，请刷新并重新核验范围与批次");
      }
      return recover(lease, marker, actor, ports, live);
    }
    let direct: StartResult | null = null;
    try { direct = validateStartResult(raw); if (direct.task_no !== input.task_no || direct.scope_count !== input.scopes.length) direct = null; } catch { /* GET is authoritative. */ }
    return recover(lease, marker, actor, ports, live, direct);
  });
}
