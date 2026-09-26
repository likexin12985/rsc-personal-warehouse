/** Opaque request coordinates only: never persist workbook content, names,
 * signed URLs, credentials or preview rows. All transitions share the manual
 * count task lock. The API remains the authority for business outcomes. */
import { createOpeningCountRecoveryStore, OPENING_IMPORT_RECORD_PREFIX,
  type OpeningCountLockManager } from "./openingCountRecoveryStore";
import { parseOpeningImportStatus, parseOpeningImportManagementReview, type OpeningImportManagementReview,
  parseOpeningImportSeal, type OpeningImportSeal, type OpeningImportSealBinding,
  type OpeningImportReviewBinding, type OpeningImportStatus } from "./openingCountImportClient";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const KEY = /^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/;
const PHASES = ["prepared", "intent_requested", "source_bound", "upload_started", "source_available",
  "job_requested", "job_bound", "confirmation_requested", "cancellation_requested", "seal_requested", "seal_conflict"] as const;
export const OPENING_IMPORT_SEALABLE_PHASES = ["intent_requested", "source_bound", "upload_started", "source_available", "job_requested"] as const;
export type OpeningImportRecord = Readonly<{
  v: 1; task_id: string; round_id: string; scope_id: string; actor_person_id: string;
  actor_authorization_version: number; source_sha256: string; size_bytes: number;
  upload_key: string; import_key: string; file_id: string | null; job_id: string | null;
  phase: typeof PHASES[number];
}>;
type Identity = Readonly<{ person_id: string; authorization_version: number }>;
export type OpeningImportRecordRead = Readonly<{ kind: "missing" | "corrupt" | "unavailable" }>
  | Readonly<{ kind: "valid"; value: OpeningImportRecord }>;
export type OpeningImportLease = Readonly<{
  read(): OpeningImportRecordRead;
  create(value: OpeningImportRecord): void;
  advance(expected: OpeningImportRecord, patch: Pick<OpeningImportRecord, "phase" | "file_id" | "job_id">): OpeningImportRecord;
  discardPrepared(expected: OpeningImportRecord): void;
  clearTerminal(expected: OpeningImportRecord, status: OpeningImportStatus): void;
}>;
export type OpeningImportManagementLease = Readonly<{
  read(): OpeningImportRecordRead;
  clearTerminal(expected: OpeningImportRecord, proof: OpeningImportManagementReview): void;
}>;
export type OpeningImportSealLease = Readonly<{
  read(): OpeningImportRecordRead;
  request(expected: OpeningImportRecord): OpeningImportRecord;
  clearTerminal(expected: OpeningImportRecord, proof: OpeningImportSeal): void;
  bindAccepted(expected: OpeningImportRecord, proof: OpeningImportManagementReview): OpeningImportRecord;
}>;
export function openingImportSealRecordBinding(record: OpeningImportRecord): OpeningImportSealBinding {
  const row = validateOpeningImportRecord(record);
  return { task_id: row.task_id, round_id: row.round_id, scope_id: row.scope_id,
    source_file_id: row.file_id, source_sha256: row.source_sha256, size_bytes: row.size_bytes,
    actor_person_id: row.actor_person_id, authorization_version: row.actor_authorization_version };
}
export function openingImportRecordBinding(record: OpeningImportRecord): OpeningImportReviewBinding {
  const row = validateOpeningImportRecord(record);
  if (!row.file_id) fail();
  return { task_id: row.task_id, round_id: row.round_id, scope_id: row.scope_id,
    source_file_id: row.file_id, source_sha256: row.source_sha256, size_bytes: row.size_bytes,
    actor_person_id: row.actor_person_id, authorization_version: row.actor_authorization_version };
}
const KEYS = ["v", "task_id", "round_id", "scope_id", "actor_person_id", "actor_authorization_version",
  "source_sha256", "size_bytes", "upload_key", "import_key", "file_id", "job_id", "phase"];
const NEXT: Record<OpeningImportRecord["phase"], readonly OpeningImportRecord["phase"][]> = {
  prepared: ["intent_requested"], intent_requested: ["source_bound", "source_available"],
  source_bound: ["upload_started", "source_available"], upload_started: ["source_available"],
  source_available: ["job_requested"], job_requested: ["job_bound"],
  job_bound: ["confirmation_requested", "cancellation_requested"],
  confirmation_requested: ["cancellation_requested"], cancellation_requested: [], seal_requested: [], seal_conflict: ["cancellation_requested"],
};
function fail(): never { throw new Error("导入恢复记录不匹配或不可用，已停止写入；请保留原任务核验"); }
function id(value: unknown): string { if (typeof value !== "string" || !UUID.test(value)) fail(); return value; }
export function validateOpeningImportRecord(value: unknown): OpeningImportRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const row = value as OpeningImportRecord;
  if (Object.keys(row).length !== KEYS.length || KEYS.some(key => !Object.hasOwn(row, key)) || row.v !== 1
    || !PHASES.includes(row.phase) || !Number.isSafeInteger(row.actor_authorization_version) || row.actor_authorization_version < 1
    || !Number.isSafeInteger(row.size_bytes) || row.size_bytes < 1 || row.size_bytes > 8 * 1024 * 1024
    || typeof row.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(row.source_sha256)
    || typeof row.upload_key !== "string" || !KEY.test(row.upload_key)
    || typeof row.import_key !== "string" || !KEY.test(row.import_key) || row.upload_key === row.import_key) fail();
  [row.task_id, row.round_id, row.scope_id, row.actor_person_id].forEach(id);
  if (row.file_id !== null) id(row.file_id);
  if (row.job_id !== null) id(row.job_id);
  if ((row.phase !== "seal_requested" && ["prepared", "intent_requested"].includes(row.phase) !== (row.file_id === null))
    || ["job_bound", "confirmation_requested", "cancellation_requested", "seal_conflict"].includes(row.phase) !== (row.job_id !== null)) fail();
  return Object.freeze(Object.fromEntries(KEYS.map(key => [key, (row as unknown as Record<string, unknown>)[key]]))) as OpeningImportRecord;
}
function same(left: OpeningImportRecord, right: OpeningImportRecord): boolean {
  return JSON.stringify(validateOpeningImportRecord(left)) === JSON.stringify(validateOpeningImportRecord(right));
}
function browserStorage(): StorageLike | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}
export function createOpeningImportRecoveryStore(options: Readonly<{
  storage?: StorageLike | null; locks?: OpeningCountLockManager | null;
}> = {}) {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const counts = createOpeningCountRecoveryStore({ ...options, storage });
  const faults = new Set<string>();
  function read(taskId: string): OpeningImportRecordRead {
    id(taskId);
    if (!storage || faults.has(taskId)) return { kind: "unavailable" };
    try {
      const raw = storage.getItem(OPENING_IMPORT_RECORD_PREFIX + taskId);
      if (raw === null) return { kind: "missing" };
      try {
        const value = validateOpeningImportRecord(JSON.parse(raw));
        return value.task_id === taskId ? { kind: "valid", value } : { kind: "corrupt" };
      } catch { return { kind: "corrupt" }; }
    } catch { return { kind: "unavailable" }; }
  }
  return Object.freeze({
    read,
    async withSealLease<T>(taskId: string, reviewer: Identity,
      work: (lease: OpeningImportSealLease) => Promise<T>): Promise<T> {
      id(reviewer.person_id);
      if (!Number.isSafeInteger(reviewer.authorization_version) || reviewer.authorization_version < 1) fail();
      const identity = Object.freeze({ ...reviewer });
      return counts.withTaskLease(taskId, async countLease => {
        let live = true;
        function checked(expected: OpeningImportRecord) {
          if (!live || !storage || faults.has(taskId) || countLease.read().kind !== "missing") fail();
          const row = validateOpeningImportRecord(expected), actual = read(taskId);
          if (row.task_id !== taskId || actual.kind !== "valid" || !same(actual.value, row)) fail();
          return row;
        }
        function persist(next: OpeningImportRecord) {
          try {
            storage!.setItem(OPENING_IMPORT_RECORD_PREFIX + taskId, JSON.stringify(next));
            const reread = read(taskId);
            if (reread.kind !== "valid" || !same(reread.value, next)) fail();
          } catch { faults.add(taskId); fail(); }
          return next;
        }
        const lease: OpeningImportSealLease = Object.freeze({
          read() {
            if (!live || !storage || faults.has(taskId) || countLease.read().kind !== "missing") fail();
            return read(taskId);
          },
          request(expected) {
            const row = checked(expected);
            if (!(OPENING_IMPORT_SEALABLE_PHASES as readonly string[]).includes(row.phase) || row.job_id !== null) fail();
            const next = validateOpeningImportRecord({ ...row, phase: "seal_requested" });
            return persist(next);
          },
          bindAccepted(expected, proof) {
            const row = checked(expected);
            if (row.phase !== "seal_requested") fail();
            const verified = parseOpeningImportManagementReview(proof, openingImportRecordBinding(row), identity);
            // Preserve the intent to stop; the original owner may subsequently
            // cancel this accepted job, but may not confirm or retry the seal.
            if (verified.terminal_verified) fail();
            return persist(validateOpeningImportRecord({ ...row, phase: "seal_conflict", job_id: verified.job_id }));
          },
          clearTerminal(expected, proof) {
            const row = checked(expected);
            if (row.phase !== "seal_requested") fail();
            parseOpeningImportSeal(proof, openingImportSealRecordBinding(row), identity);
            try {
              storage!.removeItem(OPENING_IMPORT_RECORD_PREFIX + taskId);
              if (read(taskId).kind !== "missing") fail();
            } catch { faults.add(taskId); fail(); }
          },
        });
        try { return await work(lease); } finally { live = false; }
      });
    },
    async withManagementLease<T>(taskId: string, reviewer: Identity,
      work: (lease: OpeningImportManagementLease) => Promise<T>): Promise<T> {
      id(reviewer.person_id);
      if (!Number.isSafeInteger(reviewer.authorization_version) || reviewer.authorization_version < 1) fail();
      const identity = Object.freeze({ ...reviewer });
      // Same lock as original imports and manual count commands. This lease
      // intentionally exposes no create, advance, confirm or cancel capability.
      return counts.withTaskLease(taskId, async countLease => {
        let live = true;
        function requireLease() {
          if (!live || !storage || faults.has(taskId) || countLease.read().kind !== "missing") fail();
        }
        requireLease();
        const lease: OpeningImportManagementLease = Object.freeze({
          read() { requireLease(); return read(taskId); },
          clearTerminal(expected, proof) {
            requireLease();
            const row = validateOpeningImportRecord(expected), actual = read(taskId);
            if (row.task_id !== taskId || actual.kind !== "valid" || !same(actual.value, row)
              || !["job_requested", "job_bound", "confirmation_requested", "cancellation_requested", "seal_requested", "seal_conflict"].includes(row.phase)) fail();
            const result = parseOpeningImportManagementReview(proof, openingImportRecordBinding(row), identity, row.job_id ?? undefined);
            if (!result.terminal_verified) fail();
            try {
              storage!.removeItem(OPENING_IMPORT_RECORD_PREFIX + taskId);
              if (read(taskId).kind !== "missing") fail();
            } catch { faults.add(taskId); fail(); }
          },
        });
        try { return await work(lease); } finally { live = false; }
      });
    },
    async withTaskLease<T>(taskId: string, actor: Identity, work: (lease: OpeningImportLease) => Promise<T>): Promise<T> {
      id(actor.person_id);
      if (!Number.isSafeInteger(actor.authorization_version) || actor.authorization_version < 1) fail();
      // Snapshot identity; mutating a caller's object must not transfer this lease.
      const identity = Object.freeze({ ...actor });
      return counts.withTaskLease(taskId, async countLease => {
        if (!storage || countLease.read().kind !== "missing") fail();
        let live = true;
        function requireLease(): void {
          if (!live || faults.has(taskId) || countLease.read().kind !== "missing") fail();
        }
        function checked(value: OpeningImportRecord): OpeningImportRecord {
          requireLease();
          const result = validateOpeningImportRecord(value);
          if (result.task_id !== taskId || result.actor_person_id !== identity.person_id
            || result.actor_authorization_version !== identity.authorization_version) fail();
          return result;
        }
        function expectCurrent(value: OpeningImportRecord): OpeningImportRecord {
          const result = checked(value), current = read(taskId);
          if (current.kind !== "valid" || !same(current.value, result)) fail();
          return result;
        }
        function persist(value: OpeningImportRecord): void {
          try {
            storage!.setItem(OPENING_IMPORT_RECORD_PREFIX + taskId, JSON.stringify(value));
            const reread = read(taskId);
            if (reread.kind !== "valid" || !same(reread.value, value)) fail();
          } catch { faults.add(taskId); fail(); }
        }
        function remove(): void {
          try {
            storage!.removeItem(OPENING_IMPORT_RECORD_PREFIX + taskId);
            if (read(taskId).kind !== "missing") fail();
          } catch { faults.add(taskId); fail(); }
        }
        const lease: OpeningImportLease = Object.freeze({
          read() { requireLease(); return read(taskId); },
          create(value) {
            const next = checked(value);
            if (next.phase !== "prepared" || read(taskId).kind !== "missing") fail();
            persist(next);
          },
          advance(expected, patch) {
            const before = expectCurrent(expected);
            if (!patch || Object.keys(patch).length !== 3
              || ["phase", "file_id", "job_id"].some(key => !Object.hasOwn(patch, key))
              || !NEXT[before.phase].includes(patch.phase)) fail();
            const next = checked({ ...before, ...patch });
            if (before.file_id !== null && next.file_id !== before.file_id
              || before.job_id !== null && next.job_id !== before.job_id) fail();
            persist(next);
            return next;
          },
          discardPrepared(expected) {
            const before = expectCurrent(expected);
            // The durable intent marker is saved before any source API call.
            // Once it exists, even a 404/timeout cannot justify discarding it.
            if (before.phase !== "prepared" || before.file_id !== null || before.job_id !== null) fail();
            remove();
          },
          clearTerminal(expected, status) {
            const current = expectCurrent(expected);
            if (!current.job_id) fail();
            const observed = parseOpeningImportStatus(status, current.job_id);
            if (!["succeeded", "failed", "cancelled"].includes(observed.status)) fail();
            remove();
          },
        });
        try { return await work(lease); } finally { live = false; }
      });
    },
  });
}
