import { hasFormalPermission } from './clientPolicy';
import { currentDailyAccess, parseDailyReference, requestDailyCommand, requestDailyRecovery, requireDaily,
  type DailyRecovery, type PreparedDailyCommand } from './dailyReviewProtocol';
import { type DailyReviewRecoveryStore, type DailyReviewSentinel } from './dailyReviewRecoveryStore';
import type { AccessContext } from './types';

const permission = { open: 'create_daily', explain: 'explain_daily', approve: 'approve_daily', request_changes: 'approve_daily' };
function reference(marker: DailyReviewSentinel) { const { v, kind, ...ref } = marker; return parseDailyReference(ref); }
function live(check: () => boolean) { requireDaily(check(), '页面身份或对账对象已变化，原请求继续保留待核验'); }
export async function submitDailyCommand(store: DailyReviewRecoveryStore, prepared: PreparedDailyCommand, expected: AccessContext, check: () => boolean): Promise<DailyRecovery> {
  return store.withCutoffLease(prepared.reference.cutoff_id, async lease => {
    requireDaily(['valid', 'missing'].includes(store.readPending().kind), '浏览器恢复记录不可用，已停止新提交');
    live(check); const access = await currentDailyAccess(expected); live(check);
    requireDaily(prepared.reference.actor_person_id === access.person_id && prepared.reference.original_authorization_version === access.authorization_version);
    requireDaily(hasFormalPermission(access, 'reconciliation', permission[prepared.reference.operation]), '当前身份无此审核操作权限');
    const marker: DailyReviewSentinel = { v: 1, kind: 'daily_review', ...prepared.reference };
    lease.persist(marker); // Verified durable storage must precede the first command byte.
    live(check);
    const receipt = await requestDailyCommand(prepared, access); live(check);
    const recovered = await requestDailyRecovery(prepared.reference, access, false); live(check);
    requireDaily(recovered.outcome === 'found' && JSON.stringify(recovered.receipt) === JSON.stringify(receipt));
    await currentDailyAccess(expected); live(check);
    lease.clearExact(marker); return recovered;
  });
}
export async function recoverDailyCommand(store: DailyReviewRecoveryStore, marker: DailyReviewSentinel, expected: AccessContext, seal: boolean, check: () => boolean): Promise<DailyRecovery> {
  return store.withCutoffLease(marker.cutoff_id, async lease => {
    live(check); const before = lease.read();
    requireDaily(before.kind === 'valid' && JSON.stringify(before.value) === JSON.stringify(marker));
    const ref = reference(marker), access = await currentDailyAccess(expected); live(check);
    requireDaily(ref.actor_person_id === access.person_id && ref.original_authorization_version <= access.authorization_version, '请使用原操作人员账号核验，不能覆盖其他人员的请求');
    const result = await requestDailyRecovery(ref, access, seal); live(check);
    await currentDailyAccess(expected); live(check);
    if (result.outcome !== 'not_observed') lease.clearExact(marker);
    return result;
  });
}
