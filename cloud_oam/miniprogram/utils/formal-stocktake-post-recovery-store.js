// Durable public coordinates for a non-opening stocktake difference-post recovery.
// Request bodies, idempotency keys, quantities, inventory payloads and tokens are
// deliberately excluded. A marker stays sticky until an exact read-only proof
// clears it. The coordinator is process/service-context scoped, not server-side.
const STORAGE_PREFIX = 'rsc_oam_mini_formal_post_sentinel_v1:'
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const TRACE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/
const FIELDS = ['v', 'kind', 'task_id', 'expected_task_version', 'actor_person_id', 'actor_authorization_version', 'trace_request_id']
const COORDINATORS = new WeakMap()

function invalid(message = '盘点过账恢复记录无效，已停止写入') {
  const error = new Error(message)
  error.status = 409
  error.responseReceived = false
  throw error
}

function uuid(value, name = '坐标') {
  if (typeof value !== 'string' || !UUID.test(value) || value.toLowerCase() === '00000000-0000-0000-0000-000000000000') invalid(`${name}无效`)
  return value.toLowerCase()
}

function validateFormalStocktakePostSentinel(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).length !== FIELDS.length
    || FIELDS.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
    || value.v !== 1 || value.kind !== 'formal_stocktake_post'
    || !Number.isSafeInteger(value.expected_task_version) || value.expected_task_version < 0
    || !Number.isSafeInteger(value.actor_authorization_version) || value.actor_authorization_version < 1
    || typeof value.trace_request_id !== 'string' || !TRACE.test(value.trace_request_id)) invalid()
  return Object.freeze({
    v: 1, kind: 'formal_stocktake_post', task_id: uuid(value.task_id, 'task_id'),
    expected_task_version: value.expected_task_version,
    actor_person_id: uuid(value.actor_person_id, 'actor_person_id'),
    actor_authorization_version: value.actor_authorization_version,
    trace_request_id: value.trace_request_id
  })
}

function taskId(value) { return uuid(value, 'task_id') }
function keyOf(value) { return taskId(value.task_id || value) }

function createFormalStocktakePostCoordinator() {
  const coordinator = Object.freeze({})
  COORDINATORS.set(coordinator, { active: new Map(), storageFaults: new Set() })
  return coordinator
}

const DEFAULT_COORDINATOR = createFormalStocktakePostCoordinator()
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
    || new Set(info.keys).size !== info.keys.length) invalid('盘点过账存储目录无法确认，已停止写入')
  return new Set(info.keys)
}

function createFormalStocktakePostRecoveryStore(options = {}) {
  const storage = options.storage === undefined ? defaultStorage() : options.storage
  const coordinator = options.coordinator === undefined ? DEFAULT_COORDINATOR : options.coordinator
  const state = coordinator && COORDINATORS.get(coordinator)
  function unavailable(id) {
    if (state) state.storageFaults.add(id)
    return Object.freeze({ kind: 'unavailable' })
  }
  function read(value) {
    let id
    try { id = keyOf(value) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
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
        const checked = validateFormalStocktakePostSentinel(JSON.parse(raw))
        return checked.task_id === id ? Object.freeze({ kind: 'valid', value: checked }) : Object.freeze({ kind: 'corrupt' })
      } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    } catch (_) { return unavailable(id) }
  }
  function readPending(taskIdValue) {
    let filter = null
    try { filter = taskIdValue === undefined ? null : taskId(taskIdValue) } catch (_) { return Object.freeze({ kind: 'corrupt' }) }
    if (!state || (filter && state.storageFaults.has(filter))) return Object.freeze({ kind: 'unavailable' })
    try {
      if (!availableStorage(storage)) return unavailable('__all__')
      const values = []
      for (const key of storedKeys(storage)) {
        if (!key.startsWith(STORAGE_PREFIX)) continue
        const id = key.slice(STORAGE_PREFIX.length)
        if (!UUID.test(id) || id !== id.toLowerCase()) return Object.freeze({ kind: 'corrupt' })
        const result = read(id)
        if (result.kind === 'unavailable' || result.kind === 'corrupt') return Object.freeze({ kind: result.kind })
        if (result.kind === 'valid' && (!filter || result.value.task_id === filter)) values.push(result.value)
      }
      return values.length ? Object.freeze({ kind: 'valid', values: Object.freeze(values) }) : Object.freeze({ kind: 'missing' })
    } catch (_) { return unavailable('__all__') }
  }
  return Object.freeze({
    read,
    readPending,
    async withTaskLease(value, work) {
      const id = keyOf(value)
      if (!state || !availableStorage(storage)) invalid('盘点过账持久恢复或页面协调不可用，已停止写入')
      if (typeof work !== 'function') invalid('盘点过账协调回调无效')
      if (state.storageFaults.has(id) || state.active.has(id)) invalid('同一盘点过账正在核验，请勿重复提交')
      const token = Object.freeze({})
      state.active.set(id, token)
      let live = true
      let persistedByThisLease = null
      const requireLease = () => {
        if (!live || state.active.get(id) !== token || state.storageFaults.has(id)) invalid('盘点过账协调已结束或存储异常，禁止写入')
      }
      const expected = (input) => {
        requireLease()
        const checked = validateFormalStocktakePostSentinel(input)
        if (checked.task_id !== id) invalid('盘点过账恢复记录与当前任务不一致')
        return checked
      }
      const lease = Object.freeze({
        read() { requireLease(); return read(id) },
        persist(input) {
          const checked = expected(input)
          const serialized = JSON.stringify(checked)
          const before = read(id)
          if (before.kind === 'valid') {
            if (persistedByThisLease === serialized && JSON.stringify(before.value) === serialized) return
            invalid('原盘点过账仍待核验，禁止覆盖坐标或切换人员')
          }
          if (before.kind !== 'missing') invalid('盘点过账恢复记录不可用，禁止覆盖或新建请求')
          try {
            storage.setStorageSync(STORAGE_PREFIX + id, serialized)
            const after = read(id)
            if (after.kind !== 'valid' || JSON.stringify(after.value) !== serialized) invalid()
            persistedByThisLease = serialized
          } catch (_) {
            state.storageFaults.add(id)
            invalid('盘点过账恢复记录写后核验失败，已停止发送请求')
          }
        },
        clearExact(input) {
          const checked = expected(input)
          const before = read(id)
          if (before.kind !== 'valid' || JSON.stringify(before.value) !== JSON.stringify(checked)) invalid('盘点过账恢复坐标不匹配，禁止清理')
          try {
            storage.removeStorageSync(STORAGE_PREFIX + id)
            if (read(id).kind !== 'missing') invalid()
            persistedByThisLease = null
          } catch (_) {
            state.storageFaults.add(id)
            invalid('盘点过账恢复记录清理未确认，继续停止写入')
          }
        }
      })
      try {
        const initial = read(id)
        if (initial.kind === 'unavailable' || initial.kind === 'corrupt') invalid('盘点过账持久恢复记录不可用，已停止写入')
        return await work(lease)
      } finally {
        live = false
        if (state.active.get(id) === token) state.active.delete(id)
      }
    }
  })
}

function getFormalStocktakePostRecoveryStore() {
  defaultStore ??= createFormalStocktakePostRecoveryStore()
  return defaultStore
}

module.exports = {
  STORAGE_PREFIX,
  validateFormalStocktakePostSentinel,
  createFormalStocktakePostCoordinator,
  createFormalStocktakePostRecoveryStore,
  getFormalStocktakePostRecoveryStore
}
