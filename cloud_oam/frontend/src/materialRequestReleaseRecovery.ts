import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { axesExceptReservationMatch, reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type ReleaseResult, type ReleaseSentinel, type ReleaseStore, validateReleaseResult, validateReleaseSentinel } from "./materialRequestReservationRelease";

export type ReleaseRecoveryAdapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter, "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay" | "releaseCommandStatusNoReplay">>;
export function hasReleaseRecovery(adapter: FormalMaterialRequestAdapter): adapter is ReleaseRecoveryAdapter {
  return [adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay, adapter.detailNoReplay, adapter.releaseCommandStatusNoReplay].every(method => typeof method === "function");
}
export function matchReleaseResult(sentinel: ReleaseSentinel, command: ReleaseResult): void {
  const input = sentinel.input;
  if (command.request_id !== sentinel.request_id || command.request_line_id !== sentinel.request_line_id
      || command.revision_id !== sentinel.revision_id || command.revision_no !== sentinel.revision_no
      || command.request_version !== input.expected_request_version + 1 || command.reservation_id !== input.reservation_id
      || command.allocation_id !== sentinel.allocation_id || command.released_qty !== input.released_qty || command.reason !== input.reason
      || command.source_stock_account_id !== sentinel.source_stock_account_id || command.target_stock_account_id !== sentinel.target_stock_account_id
      || command.source_balance_version !== input.source_balance_version || command.source_ledger_cursor !== input.source_ledger_cursor
      || !same(command.serial_ids, input.serial_ids) || !axesExceptReservationMatch(command.state_axes, sentinel.state_axes)) {
    throw new Error("释放结果与原占用、数量、SN 或独立状态不一致，保留原请求核验");
  }
}
export async function recoverRelease(adapter: ReleaseRecoveryAdapter, store: ReleaseStore, original: ReleaseSentinel, canCommit: () => boolean = () => true) {
  const sentinel = validateReleaseSentinel(original);
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== sentinel.person_id || identity.authorization_version !== sentinel.authorization_version
      || access.person_id !== sentinel.person_id || access.authorization_version !== sentinel.authorization_version
      || !access.can_read || !access.can_read_allocation_options) throw new Error("登录身份或释放授权已变化，保留原请求核验");
  const raw = await adapter.releaseCommandStatusNoReplay(sentinel.trace);
  if (raw === null) throw new Error("尚未观察到原释放命令，继续保留核验记录；不会自动再次提交");
  const command = validateReleaseResult(raw);
  if (!command.idempotency_replayed) throw new Error("释放历史证据标记无效");
  matchReleaseResult(sentinel, command);
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(sentinel.request_id), sentinel.request_id);
  if (detail.request_version < command.current_request_version || detail.current_revision_id !== sentinel.revision_id
      || detail.current_revision_no !== sentinel.revision_no || !detail.lines.some(line => line.request_line_id === sentinel.request_line_id && line.revision_id === sentinel.revision_id)
      || !axesExceptReservationMatch(detail.states, command.state_axes)
      || (detail.request_version === command.request_version && !same(detail.states, command.state_axes))
      || !["partially_released", "released"].includes(detail.states.reservation_status)) throw new Error("释放历史与当前需求状态未匹配，继续保留原请求核验");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (!same(identity, afterIdentity) || !same(access, afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留原释放坐标");
  const current = store.read();
  if (current.kind !== "valid" || !same(current.value, original)) throw new Error("原释放坐标已变化，禁止清理");
  store.clear(sentinel.trace);
  return { command, detail };
}
