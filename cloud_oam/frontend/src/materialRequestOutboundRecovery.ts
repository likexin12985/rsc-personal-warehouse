import { type FormalMaterialRequestAdapter, validateFormalMaterialRequestAccess, validateFormalMaterialRequestFreshIdentity } from "./formalMaterialRequestAdapter";
import { validateMaterialRequestDetail } from "./formalMaterialRequests";
import { reservationSnapshotsMatch as same } from "./materialRequestReservationRecovery";
import { type OutboundResult, type OutboundSentinel, type OutboundStore, validateOutboundResult, validateOutboundSentinel } from "./materialRequestOutbound";

function axesExceptOutboundingMatch(left: Record<string, unknown>, right: Record<string, unknown>) {
  const omit = (value: Record<string, unknown>) => Object.fromEntries(Object.entries(value).filter(([key]) => key !== "outbound_status"));
  return same(omit(left), omit(right));
}
export type OutboundRecoveryAdapter = FormalMaterialRequestAdapter & Required<Pick<FormalMaterialRequestAdapter, "loadIdentityNoReplay" | "loadAccessNoReplay" | "detailNoReplay" | "outboundCommandStatusNoReplay">>;
export function hasOutboundRecovery(adapter: FormalMaterialRequestAdapter): adapter is OutboundRecoveryAdapter {
  return [adapter.loadIdentityNoReplay, adapter.loadAccessNoReplay, adapter.detailNoReplay, adapter.outboundCommandStatusNoReplay].every(method => typeof method === "function");
}
export function matchOutboundResult(sentinel: OutboundSentinel, command: OutboundResult): void {
  const input = sentinel.input;
  if (command.request_id !== sentinel.request_id || command.request_line_id !== sentinel.request_line_id
      || command.revision_id !== sentinel.revision_id || command.revision_no !== sentinel.revision_no
      || command.request_version !== input.expected_request_version + 1 || command.pick_id !== input.pick_id
      || command.allocation_id !== sentinel.allocation_id || command.outbound_qty !== input.outbound_qty || command.reason !== input.reason
      || command.source_stock_account_id !== sentinel.source_stock_account_id || command.target_stock_account_id !== sentinel.target_stock_account_id
      || command.target_stock_account_id !== input.target_stock_account_id
      || command.source_balance_version !== input.source_balance_version || command.source_ledger_cursor !== input.source_ledger_cursor
      || !same(command.serial_ids, input.serial_ids) || !axesExceptOutboundingMatch(command.state_axes, sentinel.state_axes)) {
    throw new Error("出库结果与原拣货、数量、SN 或独立状态不一致，保留原请求核验");
  }
}
export async function recoverOutbound(adapter: OutboundRecoveryAdapter, store: OutboundStore, original: OutboundSentinel, canCommit: () => boolean = () => true) {
  const sentinel = validateOutboundSentinel(original);
  const identity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const access = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (identity.person_id !== sentinel.person_id || identity.authorization_version !== sentinel.authorization_version
      || access.person_id !== sentinel.person_id || access.authorization_version !== sentinel.authorization_version
      || !access.can_read || !access.can_read_allocation_options) throw new Error("登录身份或出库授权已变化，保留原请求核验");
  const raw = await adapter.outboundCommandStatusNoReplay(sentinel.trace);
  if (raw === null) throw new Error("尚未观察到原出库命令，继续保留核验记录；不会自动再次提交");
  const command = validateOutboundResult(raw);
  if (!command.idempotency_replayed) throw new Error("出库历史证据标记无效");
  matchOutboundResult(sentinel, command);
  const detail = validateMaterialRequestDetail(await adapter.detailNoReplay(sentinel.request_id), sentinel.request_id);
  if (detail.request_version < command.current_request_version || detail.current_revision_id !== sentinel.revision_id
      || detail.current_revision_no !== sentinel.revision_no || !detail.lines.some(line => line.request_line_id === sentinel.request_line_id && line.revision_id === sentinel.revision_id)
      || !axesExceptOutboundingMatch(detail.states, command.state_axes)
      || (detail.request_version === command.request_version && !same(detail.states, command.state_axes))
      || !["pending_pick", "picked", "outbound"].includes(detail.states.outbound_status)) throw new Error("出库历史与当前需求状态未匹配，继续保留原请求核验");
  const afterIdentity = validateFormalMaterialRequestFreshIdentity(await adapter.loadIdentityNoReplay());
  const afterAccess = validateFormalMaterialRequestAccess(await adapter.loadAccessNoReplay());
  if (!same(identity, afterIdentity) || !same(access, afterAccess) || !canCommit()) throw new Error("核验期间身份、权限或页面已变化，保留原出库坐标");
  const current = store.read();
  if (current.kind !== "valid" || !same(current.value, original)) throw new Error("原出库坐标已变化，禁止清理");
  store.clear(sentinel.trace);
  return { command, detail };
}
