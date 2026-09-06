// Durable public coordinates for non-opening stocktake review recovery.
// Review bodies, comments, idempotency keys and request payloads never enter
// wx storage. A marker remains sticky until a matching read-only proof clears
// it. The coordinator is process/service-context scoped; it is not a server or
// cross-device lock.
const STORAGE_PREFIX = 'rsc_oam_mini_formal_review_sentinel_v1:'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const STAGES = ['region', 'headquarters']
const FIELDS = ['v', 'kind', 'task_id', 'round_id', 'review_stage', 'actor_person_id',
  'actor_authorization_version', 'expected_task_version', 'trace_request_id']
const COORDINATORS = new WeakMap()

function invalid(message = '日常盘点复核恢复记录无效，已停止写入') {
  const error = new Error(message)
  error.status = 409
  error.responseReceived = false
  throw error
}

function uuid(value) {
  if (typeof value !== 'string' || !UUID.test(value) || value.toLowerCase() === '00000000-0000-0000-0000-000000000000') invalid()
  return value.toLowerCase()
}

function validateFormalStocktakeReviewSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== FIELDS.length
    || FIELDS.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
    || value.v !== 1 || value.kind !== 'formal_stocktake_review'
    || !STAGES.includes(value.review_stage)
    || !Number.isSafeInteger(value.actor_authorization_version) || value.actor_authorization_version < 1
    || !Number.isSafeInteger(value.expected_task_version) || value.expected_task_version < 0
    || typeof value.trace_request_id !== 'string' || !TRACE.test(value.trace_request_id)) invalid()
  return Object.freeze({
    v: 1, kind: 'formal_stocktake_review', task_id: uuid(value.task_id),
    round_id: uuid(value.round_id), review_stage: value.review_stage,
    actor_person_id: uuid(value.actor_person_id),
    actor_authorization_version: value.actor_authorization_version,
    expected_task_version: value.expected_task_version,
    trace_request_id: value.trace_request_id
  })
}

function keyOf(value) {
  const row = value && typeof value === 'object' && Object.keys(value).length === 3
    ? value
    : validateFormalStocktakeReviewSentinel(value)
  if (!row || !UUID.test(row.task_id) || !UUID.test(row.round_id) || !STAGES.includes(row.review_stage)) invalid()
  return `${row.task_id.toLowerCase()}:${row.round_id.toLowerCase()}:${row.review_stage}`
}

function createFormalStocktakeReviewCoordinator() {
  const coordinator = Object.freeze({})
  COORDINATORS.set(coordinator, { active: new Map(), storageFaults: new Set() })
  return coordinator
}

const DEFAULT_COORDINATOR = createFormalStocktakeReviewCoordinator()
let defaultStore

function defaultStorage() {
  try { return typeof wx === 'undefined' ? null : wx } catch (_) { return null }
}

function availableStorage(storage) {
  return storage && ['getStorageInfoSync', 'getStorageSync', 'setStorageSync', 'removeStorageSync']
    .every((name) => typeof storage[name] === 'function')
}

function storedKeys(storage) {
  const info = storage.getStorageInfoSync()
  if (!info || !Array.isArray(info.keys) || info.keys.some((key) => typeof key !== 'string')
    || new Set(info.keys).size !== info.keys.length) invalid('日常盘点复核存储目录无法确认，已停止写入')
  return new Set(info.keys)
}

function coordinates(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === 3
    && Object.prototype.hasOwnProperty.call(value, 'task_id')
    && Object.prototype.hasOwnProperty.call(value, 'round_id')
    && Object.prototype.hasOwnProperty.call(value, 'review_stage')) {
    return { task_id: uuid(value.task_id), round_id: uuid(value.round_id), review_stage: value.review_stage }
  }
  const row = validateFormalStocktakeReviewSentinel(value)
  return { task_id: row.task_id, round_id: row.round_id, review_stage: row.review_stage }
}

function createFormalStocktakeReviewRecoveryStore(options = {}) {
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const coordinator = options.coordinator === undefined ? DEFAULT_COORDINATOR : options.coordinator
  const state = coordinator && COORDINATORS.get(coordinator)

  function unavailable(id) {
    if (state) state.storageFaults.add(id)
    return Object.freeze({ kind: 'unavailable' })
  }

  function read(value) {
    let point
    try { point = coordinates(value) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    const id = keyOf(point)
    if (!state || state.storageFaults.has(id)) return unavailable(id)
    try {
      if (!availableStorage(storage)) return unavailable(id)
      const key = STORAGE_PREFIX + id
      const keys = storedKeys(storage)
      const present = keys.has(key)
      const raw = storage.getStorageSync(key)
      if (storedKeys(storage).has(key) !== present) return unavailable(id)
      if (!present) return raw === '' ? Object.freeze({ kind: 'missing' }) : unavailable(id)
      if (typeof raw !== 'string') return Object.freeze({ kind: 'corrupt' })
      try {
        const checked = validateFormalStocktakeReviewSentinel(JSON.parse(raw))
        return keyOf(checked) === id ? Object.freeze({ kind: 'valid', value: checked }) : Object.freeze({ kind: 'corrupt' })
      } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    } catch (_) { return unavailable(id) }
  }

  function readPending(taskId) {
    let checkedTaskId
    try { checkedTaskId = uuid(taskId) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    if (!state || [...state.storageFaults].some((fault) => fault === checkedTaskId || fault.startsWith(`${checkedTaskId}:`))) return Object.freeze({ kind: 'unavailable' })
    try {
      if (!availableStorage(storage)) return unavailable(checkedTaskId)
      const values = []
      for (const key of storedKeys(storage)) {
        if (!key.startsWith(STORAGE_PREFIX)) continue
        const raw = storage.getStorageSync(key)
        let value
        try { value = validateFormalStocktakeReviewSentinel(JSON.parse(raw)) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
        if (key !== STORAGE_PREFIX + keyOf(value)) return Object.freeze({ kind: 'corrupt' })
        if (value.task_id === checkedTaskId) values.push(value)
      }
      return values.length ? Object.freeze({ kind: 'valid', values: Object.freeze(values) }) : Object.freeze({ kind: 'missing' })
    } catch (_) { return unavailable(checkedTaskId) }
  }

  return Object.freeze({
    read,
    readPending,
    async withTaskLease(value, work) {
      const point = coordinates(value)
      const id = keyOf(point)
      if (!state || !availableStorage(storage)) invalid('日常盘点复核持久恢复或页面协调不可用，已停止写入')
      if (typeof work !== 'function') invalid('日常盘点复核协调回调无效')
      if (state.storageFaults.has(id) || state.active.has(id)) invalid('同一日常盘点复核正在核验，请勿重复提交')
      const token = Object.freeze({})
      state.active.set(id, token)
      let live = true
      let persistedByThisLease = null
      const requireLease = () => {
        if (!live || state.active.get(id) !== token || state.storageFaults.has(id)) invalid('日常盘点复核协调已结束或存储异常，禁止写入')
      }
      const expected = (input) => {
        requireLease()
        const checked = validateFormalStocktakeReviewSentinel(input)
        if (keyOf(checked) !== id) invalid('日常盘点复核恢复坐标与当前对象不一致')
        return checked
      }
      const lease = Object.freeze({
        read() { requireLease(); return read(point) },
        persist(input) {
          const checked = expected(input); const serialized = JSON.stringify(checked); const before = read(point)
          if (before.kind === 'valid') {
            if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return
            invalid('原日常盘点复核仍待核验，禁止覆盖坐标')
          }
          if (before.kind !== 'missing') invalid('日常盘点复核恢复记录不可用，禁止覆盖或新建请求')
          try {
            storage.setStorageSync(STORAGE_PREFIX + id, serialized)
            const after = read(point)
            if (after.kind !== 'valid' || JSON.stringify(after.value) !== serialized) invalid()
            persistedByThisLease = serialized
          } catch (_) {
            state.storageFaults.add(id)
            invalid('日常盘点复核恢复记录写后核验失败，已停止发送请求')
          }
        },
        clearExact(input) {
          const checked = expected(input); const before = read(point)
          if (before.kind !== 'valid' || JSON.stringify(before.value) !== JSON.stringify(checked)) invalid('日常盘点复核恢复坐标不匹配，禁止清理')
          try {
            storage.removeStorageSync(STORAGE_PREFIX + id)
            if (read(point).kind !== 'missing') invalid()
            persistedByThisLease = null
          } catch (_) {
            state.storageFaults.add(id)
            invalid('日常盘点复核恢复记录清理未确认，继续阻塞写入')
          }
        }
      })
      try {
        const initial = read(point)
        if (initial.kind === 'unavailable' || initial.kind === 'corrupt') invalid('日常盘点复核持久恢复记录不可用，已停止写入')
        return await work(lease)
      } finally {
        live = false
        if (state.active.get(id) === token) state.active.delete(id)
      }
    }
  })
}

function getFormalStocktakeReviewRecoveryStore() {
  defaultStore ??= createFormalStocktakeReviewRecoveryStore()
  return defaultStore
}

module.exports = {
  STORAGE_PREFIX,
  validateFormalStocktakeReviewSentinel,
  createFormalStocktakeReviewCoordinator,
  createFormalStocktakeReviewRecoveryStore,
  getFormalStocktakeReviewRecoveryStore
}
