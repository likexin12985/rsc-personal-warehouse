// Durable public coordinates for non-opening scope count recovery.
// Never store the count body, quantities, evidence IDs, idempotency key or token.
// This coordinates one scope in one WeChat service context; it is not a
// cross-device or server-side lock.
const STORAGE_PREFIX = 'rsc_oam_mini_formal_count_sentinel_v1:'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const OPERATIONS = ['initial_count', 'recount_count']
const FIELDS = ['v', 'kind', 'task_id', 'round_id', 'round_no', 'scope_id', 'operation', 'expected_task_version', 'actor_person_id', 'actor_authorization_version', 'trace_request_id']
const COORDINATORS = new WeakMap()

function invalid(message = '日常盘点恢复记录无效，已停止写入') {
  const error = new Error(message)
  error.status = 409
  error.responseReceived = false
  throw error
}

function nonzeroUuid(value) {
  if (typeof value !== 'string' || !UUID.test(value)) invalid()
  return value.toLowerCase()
}

function keyOf(value) {
  const row = validateFormalStocktakeCountSentinel(value)
  return `${row.task_id}:${row.round_id}:${row.scope_id}:${row.operation}`
}

function validateFormalStocktakeCountSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== FIELDS.length
    || FIELDS.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
    || value.v !== 1 || value.kind !== 'formal_scope_count'
    || !OPERATIONS.includes(value.operation)
    || !Number.isSafeInteger(value.round_no) || value.round_no < 1
    || !Number.isSafeInteger(value.expected_task_version) || value.expected_task_version < 0
    || !Number.isSafeInteger(value.actor_authorization_version) || value.actor_authorization_version < 1
    || typeof value.trace_request_id !== 'string' || !TRACE.test(value.trace_request_id)) invalid()
  return Object.freeze({
    v: 1, kind: 'formal_scope_count', task_id: nonzeroUuid(value.task_id),
    round_id: nonzeroUuid(value.round_id), round_no: value.round_no,
    scope_id: nonzeroUuid(value.scope_id), operation: value.operation,
    expected_task_version: value.expected_task_version,
    actor_person_id: nonzeroUuid(value.actor_person_id),
    actor_authorization_version: value.actor_authorization_version,
    trace_request_id: value.trace_request_id
  })
}

function createFormalStocktakeCountCoordinator() {
  const coordinator = Object.freeze({})
  COORDINATORS.set(coordinator, { active: new Map(), storageFaults: new Set() })
  return coordinator
}

const DEFAULT_COORDINATOR = createFormalStocktakeCountCoordinator()
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
    || new Set(info.keys).size !== info.keys.length) invalid('日常盘点存储目录无法确认，已停止写入')
  return new Set(info.keys)
}

function normalizeCoordinates(value) {
  if (!value || typeof value !== 'object') invalid('日常盘点恢复坐标无效')
  return {
    task_id: nonzeroUuid(value.task_id), round_id: nonzeroUuid(value.round_id),
    scope_id: nonzeroUuid(value.scope_id), operation: value.operation
  }
}

function createFormalStocktakeCountRecoveryStore(options = {}) {
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const coordinator = options.coordinator === undefined ? DEFAULT_COORDINATOR : options.coordinator
  const state = coordinator && COORDINATORS.get(coordinator)

  function unavailable(id) {
    if (state) state.storageFaults.add(id)
    return Object.freeze({ kind: 'unavailable' })
  }

  function read(value) {
    let coordinates
    try { coordinates = normalizeCoordinates(value) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    const id = `${coordinates.task_id}:${coordinates.round_id}:${coordinates.scope_id}:${coordinates.operation}`
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
        const checked = validateFormalStocktakeCountSentinel(JSON.parse(raw))
        return keyOf(checked) === id ? Object.freeze({ kind: 'valid', value: checked }) : Object.freeze({ kind: 'corrupt' })
      } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    } catch (_) { return unavailable(id) }
  }

  function readPending(taskId) {
    const id = nonzeroUuid(taskId)
    if (!state || [...state.storageFaults].some((fault) => fault === id || fault.startsWith(`${id}:`))) return Object.freeze({ kind: 'unavailable' })
    try {
      if (!availableStorage(storage)) return unavailable(id)
      const values = []
      for (const key of storedKeys(storage)) {
        if (!key.startsWith(STORAGE_PREFIX)) continue
        const raw = storage.getStorageSync(key)
        let value
        try { value = validateFormalStocktakeCountSentinel(JSON.parse(raw)) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
        if (key !== STORAGE_PREFIX + keyOf(value)) return Object.freeze({ kind: 'corrupt' })
        if (value.task_id === id) values.push(value)
      }
      return values.length ? Object.freeze({ kind: 'valid', values: Object.freeze(values) }) : Object.freeze({ kind: 'missing' })
    } catch (_) {
      // A task-level scan cannot distinguish a transient directory/read
      // failure from a hidden pending marker.  Latch the fault so a later
      // refresh cannot turn unavailable into missing and reopen writes.
      return unavailable(id)
    }
  }

  return Object.freeze({
    read,
    readPending,
    async withScopeLease(value, work) {
      const coordinates = normalizeCoordinates(value)
      if (!state || !availableStorage(storage)) invalid('日常盘点持久恢复或页面协调不可用，已停止写入')
      if (typeof work !== 'function') invalid('日常盘点协调回调无效')
      const id = `${coordinates.task_id}:${coordinates.round_id}:${coordinates.scope_id}:${coordinates.operation}`
      if (state.storageFaults.has(id) || state.active.has(id)) invalid('同一日常盘点范围正在核验，请勿重复提交')
      const token = Object.freeze({})
      state.active.set(id, token)
      let live = true
      let persistedByThisLease = null
      const requireLease = () => {
        if (!live || state.active.get(id) !== token || state.storageFaults.has(id)) invalid('日常盘点协调已结束或存储异常，禁止写入')
      }
      const expected = (input) => {
        requireLease()
        const checked = validateFormalStocktakeCountSentinel(input)
        if (keyOf(checked) !== id) invalid('日常盘点恢复坐标与当前范围不一致')
        return checked
      }
      const lease = Object.freeze({
        read() { requireLease(); return read(coordinates) },
        persist(input) {
          const checked = expected(input)
          const serialized = JSON.stringify(checked)
          const before = read(coordinates)
          if (before.kind === 'valid') {
            if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return
            invalid('原日常盘点请求仍待核验，禁止覆盖坐标')
          }
          if (before.kind !== 'missing') invalid('日常盘点恢复记录不可用，禁止覆盖或新建请求')
          try {
            storage.setStorageSync(STORAGE_PREFIX + id, serialized)
            const after = read(coordinates)
            if (after.kind !== 'valid' || JSON.stringify(after.value) !== serialized) invalid()
            persistedByThisLease = serialized
          } catch (_) {
            state.storageFaults.add(id)
            invalid('日常盘点恢复记录写后核验失败，已停止发送请求')
          }
        },
        clearExact(input) {
          const checked = expected(input)
          const before = read(coordinates)
          if (before.kind !== 'valid' || JSON.stringify(before.value) !== JSON.stringify(checked)) invalid('日常盘点恢复坐标不匹配，禁止清理')
          try {
            storage.removeStorageSync(STORAGE_PREFIX + id)
            if (read(coordinates).kind !== 'missing') invalid()
            persistedByThisLease = null
          } catch (_) {
            state.storageFaults.add(id)
            invalid('日常盘点恢复记录清理未确认，继续阻塞写入')
          }
        }
      })
      try {
        const initial = read(coordinates)
        if (initial.kind === 'unavailable' || initial.kind === 'corrupt') invalid('日常盘点持久恢复记录不可用，已停止写入')
        return await work(lease)
      } finally {
        live = false
        if (state.active.get(id) === token) state.active.delete(id)
      }
    }
  })
}

function getFormalStocktakeCountRecoveryStore() {
  defaultStore ??= createFormalStocktakeCountRecoveryStore()
  return defaultStore
}

module.exports = {
  STORAGE_PREFIX,
  validateFormalStocktakeCountSentinel,
  createFormalStocktakeCountCoordinator,
  createFormalStocktakeCountRecoveryStore,
  getFormalStocktakeCountRecoveryStore
}
