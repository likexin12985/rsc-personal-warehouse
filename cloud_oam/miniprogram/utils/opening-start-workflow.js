const core = require('./opening-start-core')
const client = require('./opening-start-client')
const directory = require('./opening-start-options')
function emptyStartView() { return { enabled: false, pending: false, recoverable: false, busy: false, message: '', found: '',
  scopes: [], batches: [], batchValue: 0, batchDescription: '', batchLoading: false, hasMoreBatches: false,
  canAdd: false, canSubmit: false, taskNo: '', note: '', deadline: '', blind: true, freezeValue: 0 } }
function createStartController(options) {
  const enabled = options.enabled === undefined ? client.OPENING_START_ENABLED : options.enabled === true
  const store = options.store || client.getOpeningStartStore()
  const transport = options.adapter || client.createOpeningStartPorts()
  let selection = null, scope = null, epoch = 0, region = '', actor = null
  let busy = false, batchLoading = false, message = '', found = '', scopes = [], batches = [], after = null, batch = null
  let taskNo = '', note = '', deadline = '', blind = true, freeze = 'hard'
  let record = { kind: 'missing' }
  function current() {
    try { return !!selection && directory.actorKey(directory.activeIdentity(options.currentIdentity())) === directory.actorKey(actor) }
    catch (_) { return false }
  }
  function emit() {
    const view = emptyStartView()
    if (selection) {
      Object.assign(view, { enabled, pending: record.kind !== 'missing', recoverable: record.kind === 'valid', busy, message, found,
        scopes: scopes.map((row, index) => ({ index, label: `${row.owner} · ${row.location} · ${row.person} · ${row.scope.freeze_mode === 'hard' ? '整范围冻结' : '截止流水回算'}` })),
        batches: [{ display: '请选择已发布控制批次' }].concat(batches.map((row) => ({ display: `${row.sourceName} · ${row.capturedAt} · ${row.isLatest ? '最新发布' : '历史批次'}` }))),
        batchValue: batch ? batches.indexOf(batch) + 1 : 0, batchLoading, hasMoreBatches: !!after,
        batchDescription: batch ? `采集于 ${batch.capturedAt}；${batch.recordCount} 条控制明细（不是数量）` : '',
        taskNo, note, deadline, blind, freezeValue: freeze === 'hard' ? 0 : 1,
        canAdd: enabled && !busy && record.kind === 'missing' && !!scope && !scopes.some((row) => row.scope.owner_org_id === scope.scope.owner_org_id && row.scope.location_id === scope.scope.location_id),
        canSubmit: enabled && !busy && record.kind === 'missing' && !!batch && batch.isLatest && Date.parse(batch.validUntil) > Date.now() && !!taskNo.trim() && scopes.length > 0 })
    }
    options.publish(view)
  }
  function clear() {
    epoch++; selection = null; actor = null; scope = null; region = ''; busy = false; batchLoading = false
    message = ''; found = ''; scopes = []; batches = []; after = null; batch = null
    taskNo = ''; note = ''; deadline = ''; blind = true; freeze = 'hard'; record = { kind: 'missing' }
  }
  async function loadBatches(more = false) {
    if (!enabled || !current() || batchLoading || busy || (more && !after)) return
    const invocation = epoch, expectedActor = actor, expectedRegion = region, cursor = more ? after : null
    const old = more ? batches : []; batch = null; batches = old; after = null; batchLoading = true; message = ''; emit()
    const live = () => epoch === invocation && current()
    try {
      await transport.ports.identity(expectedActor); if (!live()) return
      const page = await transport.batches(expectedActor, expectedRegion, cursor); if (!live()) return
      if (cursor !== null && (!old.length || old[old.length - 1].publicationId !== cursor)) throw new Error('控制批次分页已变化')
      batches = old.concat(page.items); after = page.nextAfterId
    } catch (_) { if (live()) { batches = []; after = null; message = '控制批次读取失败，请刷新后重选。' } }
    finally { if (live()) { batchLoading = false; emit() } }
  }
  async function run(recovery) {
    if (!current() || busy || (!recovery && !enabled)) return
    busy = true; message = ''; found = ''; emit()
    const invocation = epoch, expectedActor = actor, expectedRegion = region
    const live = () => epoch === invocation && current()
    try {
      const result = recovery === 'seal' ? await core.sealOpeningStart({ region: expectedRegion, actor: expectedActor, store, ports: transport.ports, canContinue: live,
        confirm: () => typeof options.confirmSeal === 'function' ? options.confirmSeal(core.START_SEAL_CONFIRMATION) : Promise.resolve(false) })
        : recovery ? await core.recoverOpeningStart({ region: expectedRegion, actor: expectedActor, store, ports: transport.ports, canContinue: live })
        : await core.submitOpeningStart({ actor: expectedActor, store, ports: transport.ports, enabled, canContinue: live, input: {
          publication_id: batch ? batch.publicationId : '', region_org_id: expectedRegion, task_no: taskNo.trim(),
          scopes: scopes.map((row) => row.scope), blind_count: blind, deadline: deadline ? new Date(deadline + 'T00:00:00').toISOString() : null, note: note.trim()
        } })
      if (!live()) return
      found = result.result ? result.result.task_id : '';  scopes = []; taskNo = ''; note = ''; deadline = ''
      message = result.seal ? '原启动请求已永久终结并核验。请重新选择范围和批次后另行发起。' : result.recovered ? '已核验原启动任务，请打开查看当前进度。' : '盘点任务已创建并核验，请打开任务继续。'
    } catch (error) { if (live()) message = core.startErrorMessage(error) }
    finally { if (live()) { busy = false; record = store.read(expectedRegion); emit() } }
  }
  return Object.freeze({
    hide() { clear(); emit() },
    async select(value) {
      if (!value || !value.region) { clear(); emit(); return }
      const nextActor = core.startActor(value.actor), nextRegion = core.startUuid(value.region.id)
      const changed = !selection || directory.actorKey(nextActor) !== directory.actorKey(actor) || nextRegion !== region
      if (changed) { clear(); actor = nextActor; region = nextRegion; selection = value; record = store.read(region) }
      else selection = value
      scope = value.scope || null; emit()
      if (changed) await loadBatches(false)
    },
    async refreshBatches() { return loadBatches(false) },
    async moreBatches() { return loadBatches(true) },
    selectBatch(index) {
      const i = Number(index); if (!current() || busy || batchLoading || !Number.isSafeInteger(i) || i < 0 || i > batches.length) return
      batch = i ? batches[i - 1] : null; emit()
    },
    addScope() {
      if (!current() || !enabled || busy || record.kind !== 'missing' || !scope || scopes.some((row) => row.scope.owner_org_id === scope.scope.owner_org_id && row.scope.location_id === scope.scope.location_id)) return
      scopes.push({ ...scope, scope: { ...scope.scope, freeze_mode: freeze } }); emit()
    },
    removeScope(index) { if (current() && !busy) { scopes = scopes.filter((_, i) => i !== Number(index)); emit() } },
    edit(field, value) {
      if (!enabled || !current() || busy || record.kind !== 'missing') return
      if (field === 'taskNo') taskNo = String(value).slice(0,100)
      else if (field === 'note') note = String(value).slice(0,10000)
      else if (field === 'deadline') deadline = String(value)
      else if (field === 'blind') blind = value === true
      else if (field === 'freeze') freeze = Number(value) === 1 ? 'cutoff_replay' : 'hard'
      emit()
    },
    async submit() { return run(false) },
    async recover() { return run(true) },
    async seal() { return run('seal') },
    open() { if (current() && !busy && found) options.open(found) }
  })
}
module.exports = { emptyStartView, createStartController }
