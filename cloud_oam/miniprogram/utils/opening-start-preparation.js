// One page's revocable, memory-only read invocation leases. No business lock,
// persistent sentinel, command coordinate, task or inventory state is created.
const contract = require('./opening-start-options')
const LABELS = ['任务区域', '资产所有组织', '实物库存位置', '初盘执行人员']

function emptyView() {
  return { allowed: false, expanded: false, columns: [], error: '', summary: null,
    startReady: false, controlEvidenceStatus: 'control_evidence_not_evaluated' }
}
function createPreparationController(options) {
  const adapter = options.adapter || contract.createReadAdapter()
  const publish = options.publish
  const currentIdentity = options.currentIdentity
  const limit = options.limit === undefined ? contract.LIMIT : options.limit
  let actor = null
  let active = false
  let expanded = false
  let epoch = 0
  let error = ''
  let slots = freshSlots()
  function freshSlots() { return contract.STAGES.map((stage) => ({ stage, items: [], selected: null, next: null, loading: false, error: '', generation: 0 })) }
  function currentActor() {
    try { return actor && contract.actorKey(contract.activeIdentity(currentIdentity())) === contract.actorKey(actor) }
    catch (_) { return false }
  }
  function reset(disable) {
    epoch += 1
    slots = freshSlots()
    expanded = false
    if (disable) { active = false; actor = null }
  }
  function deny() { reset(true); error = '身份或准备权限无法确认，已清除所有选项，请刷新盘点页面。'; emit() }
  function context(index) {
    const coordinates = {}
    if (index > 0) { if (!slots[0].selected) return null; coordinates.region_org_id = slots[0].selected.id }
    if (index > 1) { if (!slots[1].selected) return null; coordinates.owner_org_id = slots[1].selected.id }
    if (index > 2) { if (!slots[2].selected) return null; coordinates.location_id = slots[2].selected.id }
    return contract.preparationContext(actor, contract.STAGES[index], coordinates)
  }
  function emit() {
    const view = emptyView()
    view.allowed = active && !!actor
    view.expanded = expanded
    view.error = error
    if (view.allowed && expanded) {
      view.columns = slots.filter((slot, index) => context(index)).map((slot) => ({
        stage: slot.stage, label: LABELS[contract.STAGES.indexOf(slot.stage)],
        items: slot.items.map((item) => ({ id: item.id, display: item.name + (item.code ? ` · ${item.code}` : '') })),
        pickerItems: [{ display: '请选择' }].concat(slot.items.map((item) => ({ display: item.name + (item.code ? ` · ${item.code}` : '') }))),
        pickerValue: slot.selected ? slot.items.indexOf(slot.selected) + 1 : 0,
        selectedName: slot.selected ? slot.selected.name : '',
        loading: slot.loading, error: slot.error, hasMore: !!slot.next,
        empty: !slot.loading && !slot.error && !slot.items.length
      }))
      const owner = slots[1].selected
      const location = slots[2].selected
      if (owner && location) view.summary = { assetOwnerName: owner.name,
        physicalOwnerName: location.physicalOwnerName, custodianName: location.custodianName || '未指定保管人（区域仓）',
        assigneeName: slots[3].selected ? slots[3].selected.name : '' }
    }
    publish(view)
  }
  function clearFrom(index) {
    for (let position = index; position < slots.length; position += 1) {
      const next = freshSlots()[position]
      next.generation = slots[position].generation + 1
      slots[position] = next
    }
  }
  function permitted() {
    if (!active || !expanded || !actor) return false
    if (!currentActor()) { deny(); return false }
    return true
  }
  async function load(index, more) {
    if (!permitted() || index < 0 || index >= slots.length || slots[index].loading) return
    const expected = context(index)
    if (!expected || (more && !slots[index].next)) return
    const previous = more ? slots[index].items : []
    const after = more ? slots[index].next : null
    clearFrom(index)
    const slot = slots[index]
    slot.items = previous
    slot.loading = true
    const lease = { epoch, generation: slot.generation, actor: contract.actorKey(actor), key: contract.contextKey(expected) }
    const expectedActor = actor
    const live = () => {
      if (!active || !expanded || epoch !== lease.epoch || slots[index].generation !== lease.generation) return false
      if (!currentActor()) { deny(); return false }
      return contract.actorKey(actor) === lease.actor && contract.contextKey(context(index)) === lease.key
    }
    emit()
    try {
      await adapter.identity(expectedActor, live)
      if (!live()) return
      const page = await adapter.options(expected, after, limit)
      if (!live()) return
      const location = slots[2].selected
      if (index === 3 && location && location.locationType === 'personal'
        && page.items.some((item) => item.id !== location.custodianPersonId)) throw new Error('invalid custodian')
      const merged = contract.mergeOptions(previous, page, expected, after)
      await adapter.identity(expectedActor, live)
      if (!live()) return
      slot.items = merged
      slot.next = page.nextAfterId
    } catch (_) {
      if (!live()) return
      // No server messages or partial options survive a failed identity,
      // permission, transport, paging or projection check.
      deny()
      return
    } finally {
      if (live()) { slot.loading = false; emit() }
    }
  }
  return Object.freeze({
    activate(value) { reset(true); actor = Object.freeze({ person_id: value.person_id, authorization_version: value.authorization_version }); contract.actorKey(actor); active = true; error = ''; emit() },
    hide() { reset(true); error = ''; emit() },
    async toggle() {
      if (!active || !actor) return
      if (!currentActor()) { deny(); return }
      if (expanded) { reset(false); error = ''; emit(); return }
      expanded = true; error = ''; emit(); await load(0, false)
    },
    async refresh(stage) { await load(contract.STAGES.indexOf(stage), false) },
    async more(stage) { await load(contract.STAGES.indexOf(stage), true) },
    async select(stage, indexValue) {
      if (!permitted()) return
      const index = contract.STAGES.indexOf(stage)
      const choice = typeof indexValue === 'string' && /^\d+$/.test(indexValue) ? Number(indexValue) : indexValue
      if (index < 0 || slots[index].loading || !Number.isSafeInteger(choice) || choice < 0 || choice > slots[index].items.length || !context(index)) return
      // Native picker value 0 is a display-only placeholder, never an item
      // identifier or permission to automatically choose the first result.
      const selected = choice === 0 ? null : slots[index].items[choice - 1]
      clearFrom(index + 1)
      slots[index].selected = selected
      emit()
      if (selected && index < slots.length - 1) await load(index + 1, false)
    }
  })
}
module.exports = { emptyView, createPreparationController }
