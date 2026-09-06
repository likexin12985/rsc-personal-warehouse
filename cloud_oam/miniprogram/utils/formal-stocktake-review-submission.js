const {
  recoverFormalStocktakeReview,
  createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter,
  sentinelFromIntent
} = require('./formal-stocktake-review-recovery')
const { getFormalStocktakeReviewRecoveryStore } = require('./formal-stocktake-review-recovery-store')

function fail(message) {
  const error = new Error(message)
  error.name = 'FormalStocktakeReviewSubmissionError'
  error.status = 409
  error.responseReceived = false
  throw error
}

class FormalStocktakeReviewSubmissionPendingError extends Error {
  constructor(sentinel) {
    super('原日常盘点复核仍待只读核验，请保留恢复记录；不能重新提交')
    this.name = 'FormalStocktakeReviewSubmissionPendingError'
    this.status = 409
    this.responseReceived = false
    this.task_id = sentinel.task_id
    this.round_id = sentinel.round_id
    this.review_stage = sentinel.review_stage
    this.expected_task_version = sentinel.expected_task_version
    this.actor_person_id = sentinel.actor_person_id
    this.actor_authorization_version = sentinel.actor_authorization_version
    this.trace_request_id = sentinel.trace_request_id
  }
}

function validExpectedIdentity(value) {
  if (!value || typeof value !== 'object' || typeof value.person_id !== 'string'
    || !Number.isSafeInteger(value.authorization_version) || value.authorization_version < 1) {
    fail('当前正式身份无效，已停止写入')
  }
  return { person_id: value.person_id.toLowerCase(), authorization_version: value.authorization_version }
}

// Persist only public coordinates before the single POST. Once persisted, a
// timeout, transport error, or even a response-shaped rejection is resolved
// through the GET-only historical proof; the review body is never replayed.
async function submitDurableFormalStocktakeReview(options) {
  const {
    intent,
    expectedIdentity,
    adapter,
    store = getFormalStocktakeReviewRecoveryStore(),
    canCommit = () => true
  } = options || {}
  if (!adapter || typeof adapter.execute !== 'function'
    || typeof adapter.loadIdentityNoReplay !== 'function'
    || typeof adapter.loadAccessNoReplay !== 'function'
    || typeof adapter.detailNoReplay !== 'function'
    || typeof adapter.reviewCommandStatus !== 'function') {
    fail('当前小程序缺少日常盘点复核只读恢复能力，已停止写入')
  }
  if (!store || typeof store.withTaskLease !== 'function') fail('日常盘点复核持久恢复存储不可用，已停止写入')
  const expected = validExpectedIdentity(expectedIdentity)
  const sentinel = sentinelFromIntent(intent, expected)
  return store.withTaskLease(sentinel, async (lease) => {
    const existing = lease.read()
    if (existing.kind === 'corrupt' || existing.kind === 'unavailable') fail('日常盘点复核恢复记录不可用，已停止写入')
    let recoveryAdapter
    try {
      recoveryAdapter = createFormalStocktakeReviewRecoveryAdapterFromFormalAdapter(expected, adapter)
    } catch (_) {
      if (existing.kind === 'valid') throw new FormalStocktakeReviewSubmissionPendingError(existing.value)
      fail('当前正式身份无效，已停止写入')
    }
    if (existing.kind === 'valid') {
      try {
        return Object.freeze({ recovered: true, ...(await recoverFormalStocktakeReview(lease, existing.value, recoveryAdapter, canCommit)) })
      } catch (_) {
        throw new FormalStocktakeReviewSubmissionPendingError(existing.value)
      }
    }
    await recoveryAdapter.loadIdentity()
    await recoveryAdapter.loadAccess(sentinel.review_stage)
    if (!canCommit()) fail('当前盘点页面已变化，未发送复核请求')
    let persisted = false
    const beforeWrite = async () => {
      await recoveryAdapter.loadIdentity()
      await recoveryAdapter.loadAccess(sentinel.review_stage)
      if (!canCommit()) fail('当前盘点页面已变化，未发送复核请求')
      lease.persist(sentinel)
      const stored = lease.read()
      if (stored.kind !== 'valid' || JSON.stringify(stored.value) !== JSON.stringify(sentinel) || !canCommit()) {
        throw new FormalStocktakeReviewSubmissionPendingError(sentinel)
      }
      persisted = true
    }
    try {
      await adapter.execute(intent, { noReplay: true, beforeWrite })
    } catch (error) {
      if (!persisted) throw error
      // A durable marker makes every result ambiguous from the client point of
      // view. Do not replay or clear it based on an HTTP status; use the exact
      // historical command-status proof below.
    }
    try {
      const recovered = await recoverFormalStocktakeReview(lease, sentinel, recoveryAdapter, canCommit)
      return Object.freeze({ recovered: true, ...recovered })
    } catch (_) {
      throw new FormalStocktakeReviewSubmissionPendingError(sentinel)
    }
  })
}

module.exports = { submitDurableFormalStocktakeReview, FormalStocktakeReviewSubmissionPendingError }
