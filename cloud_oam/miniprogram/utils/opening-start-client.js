const api = require('./api')
const directory = require('./opening-start-options')
const core = require('./opening-start-core')
const { validateOpeningStocktakeDetail } = require('./stocktake-contract')
const OPENING_START_ENABLED = false
const active = new Map()
// Shared only across pages/factories in this mini-program service context.
// Durable markers and server guards cover reloads/devices; this is not a native process lock.
const locks = Object.freeze({
  async request(name, options, work) {
    if (active.has(name)) return work(null)
    const token = Object.freeze({})
    active.set(name, token)
    try { return await work(token) } finally { if (active.get(name) === token) active.delete(name) }
  }
})
let defaultStore
function createOpeningStartStore(storage = typeof wx === 'undefined' ? null : wx) {
  if (!storage || !['getStorageInfoSync','getStorageSync','setStorageSync','removeStorageSync'].every((key) => typeof storage[key] === 'function')) return core.createStartStore(null, null)
  function keys() {
    const result = storage.getStorageInfoSync()
    if (!result || !Array.isArray(result.keys) || result.keys.some((key) => typeof key !== 'string') || new Set(result.keys).size !== result.keys.length) throw new Error('启动存储目录无法确认')
    return result.keys
  }
  return core.createStartStore({
    getItem(key) {
      const present = keys().includes(key), raw = storage.getStorageSync(key)
      if (keys().includes(key) !== present) throw new Error('启动存储目录已变化')
      if (!present) { if (raw !== '') throw new Error('启动存储值无法确认'); return null }
      if (typeof raw !== 'string') throw new Error('启动存储格式无效')
      return raw
    },
    setItem(key, value) { storage.setStorageSync(key, value) },
    removeItem(key) { storage.removeStorageSync(key) }
  }, locks)
}
function getOpeningStartStore() { if (!defaultStore) defaultStore = createOpeningStartStore(); return defaultStore }
function createOpeningStartPorts(transport = api) {
  const reader = directory.createReadAdapter(transport)
  async function batches(actor, region, after = null) {
    const query = `region_org_id=${encodeURIComponent(core.startUuid(region))}&limit=50` + (after ? `&after_id=${encodeURIComponent(core.startUuid(after))}` : '')
    return core.validateStartBatchPage(await transport.request(`/v1/stocktakes/opening/start-options/control-batches?${query}`, directory.NO_STORE), actor, region, after)
  }
  const ports = Object.freeze({
    async identity(actor) { await reader.identity(actor) },
    async verifySelection(input, actor) {
      async function contains(stage, coordinates, id) {
        let cursor = null
        do {
          const page = await reader.options(directory.preparationContext(actor, stage, coordinates), cursor)
          if (page.items.some((item) => (item.stage === 'assignees' ? item.userId : item.id) === id)) return
          cursor = page.nextAfterId
        } while (cursor)
        throw new Error('选定范围或人员已变化，请重新选择')
      }
      await contains('regions', {}, input.region_org_id)
      for (const scope of input.scopes) {
        await contains('asset-owners', { region_org_id: input.region_org_id }, scope.owner_org_id)
        await contains('locations', { region_org_id: input.region_org_id, owner_org_id: scope.owner_org_id }, scope.location_id)
        await contains('assignees', { region_org_id: input.region_org_id, owner_org_id: scope.owner_org_id, location_id: scope.location_id }, scope.assignee_user_id)
      }
      let cursor = null
      do {
        const page = await batches(actor, input.region_org_id, cursor)
        const batch = page.items.find((row) => row.publicationId === input.publication_id)
        if (batch) { if (!batch.isLatest || Date.parse(batch.validUntil) <= Date.now()) throw new Error('请重新选择最新有效批次'); return }
        cursor = page.nextAfterId
      } while (cursor)
      throw new Error('控制发布批次已变化')
    },
    coordinates() { return { requestId: transport.createRequestId(), idempotencyKey: transport.createIdempotencyKey() } },
    post(input, coordinates) { return transport.request('/v1/stocktakes/opening/from-publication', { method: 'POST', noRefresh: true,
      data: input, requestId: coordinates.requestId, idempotencyKey: coordinates.idempotencyKey }) },
    seal(marker) { return transport.request('/v1/stocktakes/opening/seal-start-command', { method: 'POST', noRefresh: true,
      data: { region_org_id: marker.region_org_id, publication_id: marker.publication_id, trace_request_id: marker.trace_request_id },
      requestId: marker.trace_request_id, idempotencyKey: 'opening-start-seal:' + marker.trace_request_id }) },
    lookup(marker) {
      const query = ['region_org_id','publication_id','trace_request_id'].map((key) => `${key}=${encodeURIComponent(marker[key])}`).join('&')
      return transport.request(`/v1/stocktakes/opening/start-command-result?${query}`, directory.NO_STORE)
    },
    async detail(task) { return validateOpeningStocktakeDetail(await transport.request(`/v1/stocktakes/opening/${core.startUuid(task)}`, directory.NO_STORE)) }
  })
  return { ports, batches }
}
module.exports = { OPENING_START_ENABLED, createOpeningStartStore, getOpeningStartStore, createOpeningStartPorts }
