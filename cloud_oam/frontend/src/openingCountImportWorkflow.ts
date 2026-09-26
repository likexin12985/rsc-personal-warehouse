import { createIdempotencyKey } from "./api";
import { createOpeningCountRecoveryAdapter, type OpeningCountRecoveryAdapter } from "./openingCountRecovery";
import { validateOpeningStocktakeTaskDetail } from "./formalOpeningStocktake";
import { createOpeningImportClient, prepareOpeningImportSource, type OpeningImportReview,
  type OpeningImportManagementReview, parseOpeningImportManagementReview, type PreparedOpeningImportSource,
  type OpeningImportSeal, parseOpeningImportSeal } from "./openingCountImportClient";
import { createOpeningImportRecoveryStore, type OpeningImportLease, type OpeningImportRecord, openingImportRecordBinding,
  validateOpeningImportRecord, type OpeningImportManagementLease, type OpeningImportSealLease,
  openingImportSealRecordBinding } from "./openingCountImportRecoveryStore";

export type OpeningImportActor = Readonly<{ person_id: string; authorization_version: number }>;
export type OpeningImportWorkflow = ReturnType<typeof createOpeningImportWorkflow>;
export type OpeningImportWorkflowResult = Readonly<{ record: OpeningImportRecord; review: OpeningImportReview }>;
type FileInput = Blob & { name: string };
function stop(message = "原导入尚未完成核验，请保留原记录，不要重复提交"): never { throw new Error(message); }
export type OpeningImportManagementResult = Readonly<{ record: OpeningImportRecord; review: OpeningImportManagementReview }>;
export type OpeningImportSealResult = Readonly<{ record: OpeningImportRecord; proof: OpeningImportSeal }>;
const binding = openingImportRecordBinding;

export function createOpeningImportWorkflow(actor: OpeningImportActor, dependencies: Readonly<{
  client?: ReturnType<typeof createOpeningImportClient>;
  store?: ReturnType<typeof createOpeningImportRecoveryStore>;
  access?: Pick<OpeningCountRecoveryAdapter, "loadIdentity" | "loadAccess" | "detail">;
  isCurrent?: () => boolean;
}> = {}) {
  const identity = Object.freeze({ ...actor });
  const client = dependencies.client ?? createOpeningImportClient();
  const store = dependencies.store ?? createOpeningImportRecoveryStore();
  const access = dependencies.access ?? createOpeningCountRecoveryAdapter(identity);
  const current = dependencies.isCurrent ?? (() => true);
  function live() { if (!current()) stop("页面或登录身份已变化，原请求记录已保留"); }
  async function authority(taskId: string) {
    live();
    const me = await access.loadIdentity();
    const permissions = await access.loadAccess();
    if (me.person_id !== identity.person_id || me.authorization_version !== identity.authorization_version
      || permissions.person_id !== identity.person_id || permissions.authorization_version !== identity.authorization_version
      || !permissions.can_read || !permissions.can_count) stop("当前身份或权限不允许继续原导入");
    const detail = validateOpeningStocktakeTaskDetail(await access.detail(taskId));
    if (detail.task_id !== taskId) stop();
    live();
    return detail;
  }
  async function managementAuthority() {
    live();
    const me = await access.loadIdentity();
    const permissions = await access.loadAccess();
    if (me.person_id !== identity.person_id || me.authorization_version !== identity.authorization_version
      || permissions.person_id !== identity.person_id || permissions.authorization_version !== identity.authorization_version
      || !permissions.can_read || !permissions.can_manage) stop("当前身份无权核验原导入");
    live();
  }
  async function inspectManagement(lease: OpeningImportManagementLease): Promise<OpeningImportManagementResult> {
    const read = lease.read();
    if (read.kind !== "valid" || !read.value.file_id || !["job_requested", "job_bound", "confirmation_requested", "cancellation_requested", "seal_requested", "seal_conflict"].includes(read.value.phase)) stop();
    const record = read.value;
    await managementAuthority();
    const review = await client.managementReview(record.import_key, binding(record), identity, record.job_id ?? undefined);
    await managementAuthority();
    const after = lease.read();
    if (after.kind !== "valid" || JSON.stringify(after.value) !== JSON.stringify(record)) stop();
    return { record, review };
  }
  async function inspectSeal(lease: OpeningImportSealLease): Promise<OpeningImportSealResult> {
    const read = lease.read();
    if (read.kind !== "valid" || read.value.phase !== "seal_requested") stop();
    const record = read.value;
    await managementAuthority();
    const proof = await client.recoverSeal(record.import_key, record.upload_key, openingImportSealRecordBinding(record), identity);
    await managementAuthority();
    const after = lease.read();
    if (after.kind !== "valid" || JSON.stringify(after.value) !== JSON.stringify(record)) stop();
    return { record, proof };
  }
  async function counting(record: Pick<OpeningImportRecord, "task_id" | "round_id" | "scope_id">) {
    const detail = await authority(record.task_id);
    const scope = detail.scopes.find(item => item.scope_id === record.scope_id);
    if (detail.status !== "counting" || !detail.allowed_actions.includes("count")
      || detail.current_round?.round_id !== record.round_id || detail.current_round.status !== "counting"
      || !scope?.assigned_to_me || scope.completion_status !== "pending") {
      stop("原盘点轮次或范围已变化，请核验原导入结果");
    }
    return detail;
  }
  function original(lease: OpeningImportLease): OpeningImportRecord {
    const row = lease.read();
    if (row.kind !== "valid" || row.value.actor_person_id !== identity.person_id
      || row.value.actor_authorization_version !== identity.authorization_version) stop();
    return row.value;
  }
  function advance(lease: OpeningImportLease, record: OpeningImportRecord,
    phase: OpeningImportRecord["phase"], fileId = record.file_id, jobId = record.job_id) {
    live();
    return lease.advance(record, { phase, file_id: fileId, job_id: jobId });
  }
  async function observe(lease: OpeningImportLease, record: OpeningImportRecord): Promise<OpeningImportWorkflowResult> {
    await authority(record.task_id);
    const status = await client.recover(record.import_key, record.job_id ?? undefined);
    live();
    if (!record.job_id) record = advance(lease, record, "job_bound", record.file_id, status.job_id);
    const review = await client.review(record.job_id!, binding(record));
    await authority(record.task_id);
    live();
    return { record, review };
  }
  async function continueSource(lease: OpeningImportLease, initial: OpeningImportRecord,
    file?: FileInput, prepared?: PreparedOpeningImportSource): Promise<OpeningImportWorkflowResult> {
    let record = initial;
    if (record.phase === "seal_requested") stop("原导入已请求永久停止，请由管理员核验原终结记录");
    if (["job_requested", "job_bound", "confirmation_requested", "cancellation_requested", "seal_conflict"].includes(record.phase)) {
      return observe(lease, record); // No repeated POST, even when GET returns 404.
    }
    await counting(record);
    if (["prepared", "intent_requested", "source_bound"].includes(record.phase)) {
      if (!prepared) { if (!file) stop("请选择原 XLSX 文件以继续上传前的核验"); prepared = await prepareOpeningImportSource(file); }
      if (prepared.sha256 !== record.source_sha256 || prepared.size_bytes !== record.size_bytes) stop("所选文件与原导入不一致");
      await counting(record);
      if (record.phase === "prepared") record = advance(lease, record, "intent_requested");
      // Re-issuing this metadata intent uses the original key, never a new file.
      const intent = await client.sourceIntent(prepared, record.upload_key);
      live();
      if (record.file_id !== null && record.file_id !== intent.file_id) stop();
      if (record.phase === "intent_requested") {
        record = advance(lease, record, intent.status === "available" ? "source_available" : "source_bound", intent.file_id);
      } else if (intent.status === "available") record = advance(lease, record, "source_available");
      if (record.phase === "source_bound") {
        await counting(record);
        await client.uploadOnce(prepared, intent, async (fileId, sha) => {
          if (fileId !== record.file_id || sha !== record.source_sha256) stop();
          record = advance(lease, record, "upload_started");
          return true;
        }, current);
        record = advance(lease, record, "source_available");
      }
    } else if (record.phase === "upload_started") {
      // The original PUT may have succeeded. This performs HEAD verification
      // through the API; neither a fresh intent nor another PUT is permitted.
      await client.completeSource(record.file_id!);
      record = advance(lease, record, "source_available");
    }
    if (record.phase !== "source_available") stop();
    await counting(record);
    record = advance(lease, record, "job_requested");
    try { await client.request({ task_id: record.task_id, round_id: record.round_id,
      scope_id: record.scope_id, source_file_id: record.file_id! }, record.import_key); }
    catch { /* Only original-key GET resolves an uncertain command. */ }
    return observe(lease, record);
  }
  return Object.freeze({
    store,
    available: client.available,
    async requestSeal(taskId: string, displayed: OpeningImportRecord) {
      const expected = validateOpeningImportRecord(displayed);
      if (expected.task_id !== taskId) stop();
      return store.withSealLease(taskId, identity, async lease => {
        await managementAuthority();
        // Durable compare-and-set precedes the only POST. A refresh after this
        // point can only inspect; neither 404 nor a lost acknowledgement resets it.
        const record = lease.request(expected);
        live();
        try { await client.requestSeal(record.import_key, record.upload_key, openingImportSealRecordBinding(record), identity); }
        catch { /* Resolve exclusively through the original command's read endpoint. */ }
        return inspectSeal(lease);
      });
    },
    async inspectSeal(taskId: string) {
      return store.withSealLease(taskId, identity, lease => inspectSeal(lease));
    },
    async bindAcceptedSeal(taskId: string, displayed: OpeningImportManagementResult) {
      const record = validateOpeningImportRecord(displayed.record);
      const proof = parseOpeningImportManagementReview(displayed.review, binding(record), identity);
      if (record.task_id !== taskId || record.phase !== "seal_requested" || proof.terminal_verified) stop();
      return store.withSealLease(taskId, identity, async lease => {
        const currentRecord = lease.read();
        if (currentRecord.kind !== "valid" || JSON.stringify(currentRecord.value) !== JSON.stringify(record)) stop();
        await managementAuthority();
        const fresh = await client.managementReview(record.import_key, binding(record), identity, proof.job_id);
        await managementAuthority();
        if (Object.entries(proof).some(([k, v]) => fresh[k as keyof OpeningImportManagementReview] !== v)) stop();
        live();
        return lease.bindAccepted(record, fresh);
      });
    },
    async finishSeal(taskId: string, displayed: OpeningImportSealResult) {
      const record = validateOpeningImportRecord(displayed.record);
      const proof = parseOpeningImportSeal(displayed.proof, openingImportSealRecordBinding(record), identity);
      if (record.task_id !== taskId || record.phase !== "seal_requested") stop();
      return store.withSealLease(taskId, identity, async lease => {
        const fresh = await inspectSeal(lease);
        if (JSON.stringify(fresh.record) !== JSON.stringify(record)
          || Object.entries(proof).some(([k, v]) => fresh.proof[k as keyof OpeningImportSeal] !== v)) stop();
        live();
        lease.clearTerminal(fresh.record, fresh.proof);
        return fresh.proof;
      });
    },
    async inspectManagement(taskId: string) {
      return store.withManagementLease(taskId, identity, lease => inspectManagement(lease));
    },
    async finishManagement(taskId: string, displayed: OpeningImportManagementResult) {
      // Snapshot the exact record and reviewed proof before awaiting the lock.
      const record = validateOpeningImportRecord(displayed.record);
      const proof = parseOpeningImportManagementReview(displayed.review, binding(record), identity, record.job_id ?? undefined);
      if (record.task_id !== taskId || !proof.terminal_verified) stop();
      return store.withManagementLease(taskId, identity, async lease => {
        const fresh = await inspectManagement(lease);
        if (JSON.stringify(fresh.record) !== JSON.stringify(record)
          || Object.entries(proof).some(([key, value]) => fresh.review[key as keyof OpeningImportManagementReview] !== value)) stop();
        live();
        lease.clearTerminal(fresh.record, fresh.review);
        return fresh.review;
      });
    },
    async start(taskId: string, roundId: string, scopeId: string, file: FileInput) {
      const prepared = await prepareOpeningImportSource(file);
      live();
      return store.withTaskLease(taskId, identity, async lease => {
        if (lease.read().kind !== "missing") stop();
        await counting({ task_id: taskId, round_id: roundId, scope_id: scopeId });
        const record: OpeningImportRecord = { v: 1, task_id: taskId, round_id: roundId, scope_id: scopeId,
          actor_person_id: identity.person_id, actor_authorization_version: identity.authorization_version,
          source_sha256: prepared.sha256, size_bytes: prepared.size_bytes,
          upload_key: createIdempotencyKey("opening-upload"), import_key: createIdempotencyKey("opening-import"),
          file_id: null, job_id: null, phase: "prepared" };
        lease.create(record);
        return continueSource(lease, record, file, prepared);
      });
    },
    async resume(taskId: string, file?: FileInput) {
      return store.withTaskLease(taskId, identity, lease => continueSource(lease, original(lease), file));
    },
    async discardPrepared(taskId: string) {
      return store.withTaskLease(taskId, identity, async lease => {
        live();
        const record = original(lease);
        if (record.phase !== "prepared") stop();
        const me = await access.loadIdentity();
        live();
        if (me.person_id !== identity.person_id || me.authorization_version !== identity.authorization_version) stop();
        // This only removes an unsent local draft. It needs no current count
        // permission, source request, server task cancellation or inventory write.
        lease.discardPrepared(record);
      });
    },
    async decide(taskId: string, displayed: OpeningImportReview, action: "confirm" | "cancel") {
      return store.withTaskLease(taskId, identity, async lease => {
        let record = original(lease);
        if (record.job_id !== displayed.job_id) stop();
        const fresh = await observe(lease, record);
        record = fresh.record;
        if (["succeeded", "failed", "cancelled"].includes(fresh.review.result.status)) return fresh;
        if (action === "confirm") {
          if (record.phase !== "job_bound" || !displayed.can_confirm || !fresh.review.can_confirm
            || JSON.stringify(binding(record)) !== JSON.stringify({ task_id: displayed.task_id,
              round_id: displayed.round_id, scope_id: displayed.scope_id, source_file_id: displayed.source_file_id,
              source_sha256: displayed.source_sha256, size_bytes: displayed.size_bytes,
              actor_person_id: displayed.actor_person_id, authorization_version: displayed.authorization_version })
            || fresh.review.result.row_count !== displayed.result.row_count) stop();
          await counting(record);
          record = advance(lease, record, "confirmation_requested");
        } else if (action === "cancel") {
          if (!["job_bound", "confirmation_requested", "seal_conflict"].includes(record.phase)) stop();
          await authority(taskId);
          record = advance(lease, record, "cancellation_requested");
        } else stop();
        try { await client.decide(record.job_id!, record.import_key, action); }
        catch { /* Never replay; retain the submitted phase and read the original. */ }
        return observe(lease, record);
      });
    },
    async finish(taskId: string) {
      return store.withTaskLease(taskId, identity, async lease => {
        const result = await observe(lease, original(lease));
        live();
        lease.clearTerminal(result.record, result.review.result);
        return result.review.result;
      });
    },
    async errorDownload(taskId: string) {
      return store.withTaskLease(taskId, identity, async lease => {
        const result = await observe(lease, original(lease));
        if (!result.review.result.error_file_available) stop("该任务尚无可下载的错误报告");
        live();
        const download = await client.errorDownload(result.review.job_id);
        live();
        return download;
      });
    },
  });
}
