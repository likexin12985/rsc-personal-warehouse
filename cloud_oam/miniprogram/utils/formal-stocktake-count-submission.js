const { validateFormalStocktakeCountSentinel } = require('./formal-stocktake-count-recovery-store')
const { recoverFormalStocktakeCount, createFormalStocktakeCountRecoveryAdapterFromFormalAdapter } = require('./formal-stocktake-count-recovery')
const { getFormalStocktakeCountRecoveryStore } = require('./formal-stocktake-count-recovery-store')

function fail(message) { const error = new Error(message); error.status = 409; throw error }

// A response-backed 4xx from the single POST is definitive only for input,
// authorization, or object lookup rejection.  Keep state/conflict responses
// durable: another transaction may already have completed the same scope, and
// only the GET-only command-status proof may clear that marker.
function definitiveCountRejection(error) {
  return Boolean(error && error.responseReceived === true
    && [400, 403, 404, 422].includes(error.status)
    && ['invalid_request', 'forbidden', 'not_found'].includes(error.category))
}

class FormalStocktakeCountSubmissionPendingError extends Error {
  constructor(sentinel) {
    super('原日常盘点范围计数仍待只读核验，请保留恢复记录；不能重新提交')
    this.name = 'FormalStocktakeCountSubmissionPendingError'
    this.status = 409
    this.task_id = sentinel.task_id
    this.round_id = sentinel.round_id
    this.scope_id = sentinel.scope_id
    this.trace_request_id = sentinel.trace_request_id
  }
}

function sentinelFromIntent(intent, expected, roundNo) {
  if (!intent || intent.method !== 'POST' || !['submit_initial_count', 'submit_recount_count'].includes(intent.action)
    || !intent.taskId || !intent.roundId || !intent.scopeId || !Number.isSafeInteger(intent.expectedTaskVersion) || intent.expectedTaskVersion < 0
    || !Number.isSafeInteger(roundNo) || roundNo < 1
    || !intent.headers || typeof intent.headers['X-Request-ID'] !== 'string') fail('日常盘点范围计数意图无效')
  const operation = intent.action === 'submit_initial_count' ? 'initial_count' : 'recount_count'
  if ((operation === 'initial_count' && roundNo !== 1) || (operation === 'recount_count' && roundNo <= 1)) {
    fail('日常盘点范围计数轮次与动作不一致')
  }
  return validateFormalStocktakeCountSentinel({
    v: 1, kind: 'formal_scope_count', task_id: intent.taskId, round_id: intent.roundId,
    round_no: roundNo, scope_id: intent.scopeId, operation,
    expected_task_version: intent.expectedTaskVersion,
    actor_person_id: expected.person_id, actor_authorization_version: expected.authorization_version,
    trace_request_id: intent.headers['X-Request-ID']
  })
}

// Reuse the production adapter's no-replay methods instead of constructing a
// second adapter against the module-global transport. This keeps recovery on
// the same authenticated/test transport and prevents an accidental cross-
// session or cross-device request path.
async function submitDurableFormalStocktakeCount(options) {
  const { intent, expectedIdentity, adapter, roundNo, store = getFormalStocktakeCountRecoveryStore(), canCommit = () => true } = options
  if (!adapter || typeof adapter.execute !== 'function' || typeof adapter.loadIdentityNoReplay !== 'function'
    || typeof adapter.loadAccessNoReplay !== 'function' || typeof adapter.detailNoReplay !== 'function'
    || typeof adapter.countCommandStatus !== 'function') fail('当前小程序缺少日常盘点只读恢复能力，已停止写入')
  const expected = { person_id: String(expectedIdentity.person_id).toLowerCase(), authorization_version: expectedIdentity.authorization_version }
  const sentinel = sentinelFromIntent(intent, expected, roundNo)
  return store.withScopeLease(sentinel, async (lease) => {
    const existing = lease.read()
    if (existing.kind === 'corrupt' || existing.kind === 'unavailable') fail('日常盘点恢复记录不可用，已停止写入')
    let recoveryAdapter
    try {
      recoveryAdapter = createFormalStocktakeCountRecoveryAdapterFromFormalAdapter(expected, adapter)
    } catch (_) {
      if (existing.kind === 'valid') throw new FormalStocktakeCountSubmissionPendingError(existing.value)
      fail('当前正式身份无效，已停止写入')
    }
    if (existing.kind === 'valid') {
      try { return Object.freeze({ recovered: true, ...(await recoverFormalStocktakeCount(lease, existing.value, recoveryAdapter, canCommit)) }) }
      catch (_) { throw new FormalStocktakeCountSubmissionPendingError(existing.value) }
    }
    await recoveryAdapter.loadIdentity()
    await recoveryAdapter.loadAccess()
    if (!canCommit()) fail('当前盘点页面已变化，未发送请求')
    let persisted = false
    const beforeWrite = async () => {
      await recoveryAdapter.loadIdentity()
      await recoveryAdapter.loadAccess()
      if (!canCommit()) fail('当前盘点页面已变化，未发送请求')
      lease.persist(sentinel)
      const stored = lease.read()
      if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) throw new FormalStocktakeCountSubmissionPendingError(sentinel)
      persisted = true
    }
    try {
      await adapter.execute(intent, { noReplay: true, beforeWrite })
    } catch (error) {
      if (!persisted) throw error
      if (definitiveCountRejection(error)) {
        // The API contract guarantees these categories return before the
        // count transaction can commit.  Re-check the same identity/access
        // context before removing the marker; otherwise keep it durable and
        // force the normal historical GET proof path.
        try {
          await recoveryAdapter.loadIdentity()
          await recoveryAdapter.loadAccess()
          if (!canCommit()) throw new Error('page changed')
          lease.clearExact(sentinel)
        } catch (_) {
          throw new FormalStocktakeCountSubmissionPendingError(sentinel)
        }
        throw error
      }
      // Once the marker is durable, every other outcome is resolved only
      // through the GET-only command-status proof.
    }
    try {
      const recovered = await recoverFormalStocktakeCount(lease, sentinel, recoveryAdapter, canCommit)
      return Object.freeze({ recovered: true, ...recovered })
    } catch (_) { throw new FormalStocktakeCountSubmissionPendingError(sentinel) }
  })
}

module.exports = { submitDurableFormalStocktakeCount, FormalStocktakeCountSubmissionPendingError, definitiveCountRejection, sentinelFromIntent }
