const session = require('../../utils/session')
const adapterModule = require('../../utils/material-request-adapter')
const contract = require('../../utils/material-request-contract')
const materialCatalog = require('../../utils/material-catalog-contract')
const formalFileUpload = require('../../utils/formal-file-upload')

const transport = adapterModule.formalMaterialRequestAdapter

const AXES = [
  ['request_status', '申请'],
  ['allocation_status', '分配'],
  ['reservation_status', '占用'],
  ['outbound_status', '出库'],
  ['shipment_status', '发货'],
  ['logistics_signature_status', '物流签收'],
  ['oam_receipt_status', 'OAM收货'],
  ['personal_inbound_status', 'RSC/个人仓入库'],
  ['notification_status', '通知送达'],
  ['reconciliation_status', '对账同步']
]

const STATUS_LABELS = {
  draft: '草稿',
  submitted: '已提交',
  approval_in_progress: '审批中',
  returned: '已退回',
  partially_approved: '部分批准',
  approved: '已批准',
  rejected: '已驳回',
  withdrawn: '已撤回',
  cancellation_pending: '取消处理中',
  cancelled: '已取消'
}
const NONZERO_UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const SAFE_EXTERNAL_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$/

function nextLineKey(page) {
  page._lineKey = (page._lineKey || 0) + 1
  return page._lineKey
}

function emptyLine(page) {
  return {
    key: nextLineKey(page),
    material: null,
    requestedQty: '',
    requiredDate: '',
    substituteMaterial: null,
    note: ''
  }
}

function emptyForm(page) {
  return {
    workOrderId: '',
    purpose: '',
    urgency: 'normal',
    expectedDate: '',
    provinceCode: '',
    provinceName: '',
    cityName: '',
    districtName: '',
    addressDetail: '',
    contactName: '',
    contactMobile: '',
    attachmentFileIds: [],
    note: '',
    lines: [emptyLine(page)]
  }
}

function draftToForm(page, draft, materials) {
  return {
    workOrderId: draft.work_order_id || '',
    purpose: draft.purpose,
    urgency: draft.urgency,
    expectedDate: draft.expected_date || '',
    provinceCode: draft.address.province_code,
    provinceName: draft.address.province_name,
    cityName: draft.address.city_name,
    districtName: draft.address.district_name,
    addressDetail: draft.address.detail,
    contactName: draft.contact.name,
    contactMobile: draft.contact.mobile,
    attachmentFileIds: draft.attachment_file_ids.slice(),
    note: draft.note,
    lines: draft.lines.map((line) => ({
      key: nextLineKey(page),
      material: materials.get(line.material_id) || null,
      requestedQty: line.requested_qty,
      requiredDate: line.required_date || '',
      substituteMaterial: line.suggested_substitute_material_id
        ? materials.get(line.suggested_substitute_material_id) || null
        : null,
      note: line.note
    }))
  }
}

function formToDraft(form, uploadedFiles = []) {
  const attachmentFileIds = Array.from(new Set(form.attachmentFileIds.concat(
    uploadedFiles.map((file) => file.file_id)
  )))
  return contract.validateMaterialRequestDraftInput({
    work_order_id: form.workOrderId || null,
    purpose: form.purpose,
    urgency: form.urgency,
    expected_date: form.expectedDate || null,
    address: {
      province_code: form.provinceCode,
      province_name: form.provinceName,
      city_name: form.cityName,
      district_name: form.districtName,
      detail: form.addressDetail
    },
    contact: {
      name: form.contactName,
      mobile: form.contactMobile
    },
    attachment_file_ids: attachmentFileIds,
    note: form.note,
    lines: form.lines.map((line) => ({
      material_id: line.material ? line.material.material_id : '',
      requested_qty: line.requestedQty,
      required_date: line.requiredDate || null,
      suggested_substitute_material_id: line.substituteMaterial
        ? line.substituteMaterial.material_id
        : null,
      note: line.note
    }))
  })
}

function presentSummary(item) {
  return Object.assign({}, item, {
    statusLabel: STATUS_LABELS[item.states.request_status] || item.states.request_status,
    maskedContact: `${item.contact_masked.name_masked} · ${item.contact_masked.mobile_masked}`,
    maskedAddress: `${item.address_snapshot.province_name}${item.address_snapshot.city_name}${item.address_snapshot.district_name}${item.address_snapshot.detail_masked}`
  })
}

function trackingLabel(mode) {
  return {
    none: '不追踪',
    lot: '批次',
    serial: '序列号',
    lot_and_serial: '批次+序列号'
  }[mode] || '未知策略'
}

function presentMaterial(item) {
  return Object.assign({}, item, {
    trackingLabel: trackingLabel(item.tracking_mode),
    fractionLabel: item.allow_fraction ? '允许小数' : '仅整数'
  })
}

function currentApprovalStep(detail) {
  const stepId = detail.approval_instance && detail.approval_instance.current_step_id
  return stepId
    ? detail.approval_instance.steps.find((step) => step.step_id === stepId) || null
    : null
}

function approvalInputLines(detail) {
  const step = currentApprovalStep(detail)
  if (!step) throw new Error('当前审批步骤锚点缺失，已停止处理')
  const predecessor = step.predecessor_step_id
    ? detail.approval_instance.steps.find((item) => item.step_id === step.predecessor_step_id)
    : null
  const previous = new Map(
    ((predecessor && predecessor.line_decisions) || [])
      .map((item) => [item.request_line_id, item.approved_qty])
  )
  const rows = detail.lines.map((line) => {
    const inputQty = step.step_no === 1 ? line.requested_qty : previous.get(line.request_line_id)
    if (!inputQty) throw new Error('审批前序逐行数量不完整，已停止处理')
    return {
      requestLineId: line.request_line_id,
      inputQty,
      decisionQty: inputQty,
      reason: ''
    }
  })
  if (step.step_no > 1 && previous.size !== detail.lines.length) {
    throw new Error('审批前序逐行数量与当前修订不一致，已停止处理')
  }
  return rows
}

function decimalUnits(value) {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  return { whole, fraction: fraction.padEnd(3, '0') }
}

function compareDecimal(left, right) {
  if (left.whole.length !== right.whole.length) return left.whole.length - right.whole.length
  if (left.whole !== right.whole) return left.whole < right.whole ? -1 : 1
  if (left.fraction === right.fraction) return 0
  return left.fraction < right.fraction ? -1 : 1
}

function approvalLinePayload(process) {
  if (process.action === 'reject') return { lines: [], return_lines: [] }
  return process.lines.reduce((result, line) => {
    const input = decimalUnits(line.inputQty)
    const decision = decimalUnits(line.decisionQty)
    if (
      input === null ||
      decision === null ||
      compareDecimal(decision, input) > 0 ||
      (process.action === 'return' && decision.whole === '0' && decision.fraction === '000')
    ) throw new Error('逐行处理数量无效或超过当前级可处理数量')
    if (process.action === 'approve') {
      if (compareDecimal(decision, input) < 0 && !line.reason.trim()) {
        throw new Error('部分驳回的明细必须填写原因')
      }
      result.lines.push({
        request_line_id: line.requestLineId,
        approved_qty: line.decisionQty,
        reason: line.reason.trim()
      })
    } else {
      if (!line.reason.trim()) throw new Error('退回的每条明细必须填写重审原因')
      result.return_lines.push({
        request_line_id: line.requestLineId,
        requested_reapproval_qty: line.decisionQty,
        reason: line.reason.trim()
      })
    }
    return result
  }, { lines: [], return_lines: [] })
}

function approvalNotice(detail) {
  const current = detail.approval_instance && detail.approval_instance.current_step_id
  const step = current
    ? detail.approval_instance.steps.find((item) => item.step_id === current)
    : null
  if (
    step &&
    step.source_mode === 'external_registration' &&
    step.status === 'awaiting_external_evidence'
  ) return '外部审批证据尚未登记；禁止据此推进供给或履约。'
  if (detail.states.request_status === 'returned') {
    return '申请已退回；请按退回意见修改后重新提交，不会自动进入分配或履约。'
  }
  return '审批、供给计划与实际履约是独立事实；本页不推断后续状态。'
}

function presentDetail(detail, access) {
  const editable = ['draft', 'returned'].includes(detail.states.request_status)
  const step = currentApprovalStep(detail)
  const actions = new Set(detail.allowed_actions)
  const internalPermission = step && (
    step.step_no === 1
      ? access.can_approve_region
      : step.step_no === 2 && access.can_approve_headquarters
  )
  const evidence = (
    detail.approval_instance && detail.approval_instance.external_evidence_summaries
  ) || []
  const pendingEvidence = evidence.filter((item) => (
    step && item.step_id === step.step_id && item.status === 'pending_verification'
  ))
  return Object.assign({}, detail, {
    statusLabel: STATUS_LABELS[detail.states.request_status] || detail.states.request_status,
    maskedContact: `${detail.contact_masked.name_masked} · ${detail.contact_masked.mobile_masked}`,
    maskedAddress: `${detail.address_snapshot.province_name}${detail.address_snapshot.city_name}${detail.address_snapshot.district_name}${detail.address_snapshot.detail_masked}`,
    stateAxes: AXES.map(([key, label]) => ({ key, label, value: detail.states[key] })),
    approvalSteps: detail.approval_instance ? detail.approval_instance.steps : [],
    canEdit: editable && detail.allowed_actions.includes('update'),
    canSubmit: editable && detail.allowed_actions.includes('submit'),
    canProcessInternal: !!(
      step &&
      step.source_mode === 'internal' &&
      internalPermission &&
      (actions.has('approve') || actions.has('return') || actions.has('reject'))
    ),
    canRegisterExternal: !!(
      step && step.source_mode === 'external_registration' &&
      access.can_register_external && actions.has('register_external_approval')
    ),
    canVerifyExternal: !!(
      step && step.source_mode === 'external_registration' &&
      access.can_verify_external && actions.has('verify_external_approval') &&
      pendingEvidence.length === 1
    ),
    verificationBlocked: !!(
      access.can_verify_external && actions.has('verify_external_approval') &&
      pendingEvidence.length !== 1
    ),
    approvalNotice: approvalNotice(detail)
  })
}

function axesMatch(result, detail) {
  return result.request_version === detail.request_version &&
    AXES.every(([key]) => result.states[key] === detail.states[key])
}

function approvalMutationMatches(result, detail) {
  const instance = detail.approval_instance
  return axesMatch(result, detail) &&
    result.revision_id === detail.current_revision_id &&
    result.revision_no === detail.current_revision_no &&
    result.approval_instance_id === (instance ? instance.instance_id : null) &&
    result.approval_attempt_no === (instance ? instance.attempt_no : null) &&
    result.current_step_id === (instance ? instance.current_step_id : null)
}

function toast(error, fallback) {
  wx.showToast({ title: (error && error.message) || fallback, icon: 'none' })
}

function ensureRegistries(page) {
  if (!page._createRegistry) {
    page._createRegistry = contract.createMaterialRequestCreateIntentRegistry()
  }
  if (!page._mutationRegistry) {
    page._mutationRegistry = contract.createMaterialRequestIntentRegistry()
  }
}

function ensureFileUploadControllers(page) {
  if (!page._requestAttachmentUploads) {
    page._requestAttachmentUploads = formalFileUpload.createFormalFileUploadController({
      purpose: 'request_attachment',
      multiple: true,
      onChange(snapshot) {
        page.setData({
          requestUploadFiles: snapshot.files,
          requestUploadBlocking: snapshot.blocking,
          requestUploadCanChoose: snapshot.canChoose
        })
      }
    })
  }
  if (!page._externalEvidenceUploads) {
    page._externalEvidenceUploads = formalFileUpload.createFormalFileUploadController({
      purpose: 'external_approval_evidence',
      multiple: false,
      onChange(snapshot) {
        page.setData({
          externalUploadFiles: snapshot.files,
          externalUploadBlocking: snapshot.blocking,
          externalUploadCanChoose: snapshot.canChoose
        })
      }
    })
  }
}

function availableFiles(controller, expectedPurpose) {
  const snapshot = controller.snapshot()
  if (snapshot.blocking) throw new Error('附件尚未完成 available 严格确认，已停止业务写入')
  for (const file of snapshot.availableFiles) {
    if (
      !NONZERO_UUID.test(file.file_id) ||
      file.purpose !== expectedPurpose ||
      file.status !== 'available'
    ) throw new Error('附件用途或 available 状态与当前业务不一致')
  }
  return snapshot.availableFiles
}

function clearFileUploadMemory(page) {
  if (page._requestAttachmentUploads) page._requestAttachmentUploads.clear()
  if (page._externalEvidenceUploads) page._externalEvidenceUploads.clear()
}

function accessUploadIdentity(access) {
  return `${access.person_id}:${access.authorization_version}`
}

Page({
  data: {
    loading: true,
    busy: false,
    accessAllowed: false,
    canCreate: false,
    accessMessage: '正在校验正式需求权限',
    requests: [],
    detail: null,
    formMode: '',
    formRequestId: '',
    formRequestVersion: 0,
    form: null,
    requestUploadFiles: [],
    requestUploadBlocking: false,
    requestUploadCanChoose: true,
    materialPicker: null,
    processing: null,
    externalUploadFiles: [],
    externalUploadBlocking: false,
    externalUploadCanChoose: true,
    submitConfirm: false,
    writePending: false,
    pendingWriteMessage: '',
    notice: ''
  },

  onShow() {
    if (!session.ensureLogin()) return
    this.load()
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh())
  },

  onUnload() {
    this._loadGeneration = (this._loadGeneration || 0) + 1
    this._createRegistry = null
    this._mutationRegistry = null
    this._access = null
    this._uploadIdentity = ''
    clearFileUploadMemory(this)
    this._requestAttachmentUploads = null
    this._externalEvidenceUploads = null
    this.setData({ form: null, materialPicker: null, processing: null, detail: null, submitConfirm: false })
  },

  async load() {
    const generation = (this._loadGeneration || 0) + 1
    this._loadGeneration = generation
    this._access = null
    this.setData({
      loading: true,
      accessAllowed: false,
      canCreate: false,
      accessMessage: '正在校验正式需求权限',
      requests: [],
      detail: null,
      notice: ''
    })
    try {
      const access = adapterModule.validateAccess(await transport.loadAccess())
      if (generation !== this._loadGeneration) return
      if (!access.can_read) {
        this._uploadIdentity = ''
        clearFileUploadMemory(this)
        this.setData({ form: null, formMode: '', processing: null })
        this.setData({ accessMessage: '当前主体没有正式需求读取权限，已失败关闭' })
        return
      }
      const response = contract.validateMaterialRequestPage(await transport.list(null))
      if (generation !== this._loadGeneration) return
      ensureFileUploadControllers(this)
      const nextUploadIdentity = accessUploadIdentity(access)
      if (this._uploadIdentity && this._uploadIdentity !== nextUploadIdentity) {
        clearFileUploadMemory(this)
        this.setData({ form: null, formMode: '', processing: null })
      }
      this._uploadIdentity = nextUploadIdentity
      this._access = access
      this.setData({
        accessAllowed: true,
        canCreate: access.can_create,
        accessMessage: '仅展示当前主体正式授权范围内的脱敏需求',
        requests: response.items.map(presentSummary)
      })
    } catch (error) {
      if (generation !== this._loadGeneration) return
      this.setData({
        accessAllowed: false,
        canCreate: false,
        accessMessage: '无法确认身份、权限或正式需求响应契约，已失败关闭。',
        requests: []
      })
      this._access = null
      this._uploadIdentity = ''
      clearFileUploadMemory(this)
      toast(error, '正式需求读取失败')
    } finally {
      if (generation === this._loadGeneration) this.setData({ loading: false })
    }
  },

  async openRequest(event) {
    const requestId = String(event.currentTarget.dataset.id || '')
    if (!this.data.requests.some((item) => item.request_id === requestId)) {
      toast(null, '需求标识已失效，请刷新')
      return
    }
    ensureFileUploadControllers(this)
    this._externalEvidenceUploads.clear()
    this.setData({ busy: true, notice: '' })
    try {
      const detail = contract.validateMaterialRequestDetail(
        await transport.detail(requestId),
        requestId
      )
      if (!this._access) throw new Error('正式需求访问上下文已失效')
      this.setData({ detail: presentDetail(detail, this._access), processing: null })
    } catch (error) {
      this.setData({ detail: null })
      toast(error, '需求详情读取失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  closeDetail() {
    ensureRegistries(this)
    if (this.data.detail && this._mutationRegistry.get(this.data.detail.request_id)) {
      toast(null, '写入结果尚未确认，必须保留当前详情与请求坐标')
      return
    }
    if (this._externalEvidenceUploads) this._externalEvidenceUploads.clear()
    this.setData({ detail: null, submitConfirm: false })
  },

  startCreate() {
    if (!this.data.canCreate) {
      toast(null, '当前主体没有正式需求创建权限')
      return
    }
    if (!this._access || !this._access.can_read_material_catalog) {
      toast(null, '当前主体没有正式物料目录权限，禁止回退旧目录')
      return
    }
    ensureRegistries(this)
    ensureFileUploadControllers(this)
    this._requestAttachmentUploads.bind(`${this._uploadIdentity}:request:create:new`)
    this.setData({
      detail: null,
      formMode: 'create',
      formRequestId: '',
      formRequestVersion: 0,
      form: emptyForm(this),
      materialPicker: null,
      writePending: false,
      pendingWriteMessage: '',
      notice: ''
    })
  },

  async resolveDraftMaterials(draft) {
    if (!this._access || !this._access.can_read_material_catalog) {
      throw new Error('当前授权不包含正式物料目录读取权限，已停止编辑')
    }
    const requiredIds = new Set(draft.lines.flatMap((line) => [
      line.material_id,
      ...(line.suggested_substitute_material_id ? [line.suggested_substitute_material_id] : [])
    ]))
    const found = new Map()
    const seenIds = new Set()
    const seenCursors = new Set()
    let afterId = null
    for (let pageNo = 0; pageNo < 100 && found.size < requiredIds.size; pageNo += 1) {
      const response = materialCatalog.validatePage(await transport.listMaterials('', afterId))
      for (const item of response.items) {
        if (seenIds.has(item.material_id)) throw new Error('正式物料目录跨页重复，已停止编辑')
        seenIds.add(item.material_id)
        if (requiredIds.has(item.material_id)) found.set(item.material_id, presentMaterial(item))
      }
      if (!response.next_after_id) break
      if (seenCursors.has(response.next_after_id)) throw new Error('正式物料目录游标循环，已停止编辑')
      seenCursors.add(response.next_after_id)
      afterId = response.next_after_id
    }
    if (found.size !== requiredIds.size) {
      throw new Error('草稿引用的物料不在当前正式活动目录中，已停止编辑')
    }
    return found
  },

  async openMaterialPicker(event) {
    const key = Number(event.currentTarget.dataset.key)
    const target = String(event.currentTarget.dataset.target || '')
    if (
      !this.data.form ||
      !this.data.form.lines.some((line) => line.key === key) ||
      !['material', 'substitute'].includes(target) ||
      !this._access ||
      !this._access.can_read_material_catalog
    ) {
      toast(null, '正式物料目录不可用，禁止手填 UUID 或回退旧目录')
      return
    }
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this.setData({
      materialPicker: {
        lineKey: key,
        target,
        query: '',
        items: [],
        nextAfterId: null,
        loading: true,
        error: ''
      }
    })
    await this.readMaterialPickerPage('', null, false)
  },

  materialPickerQueryInput(event) {
    if (!this.data.materialPicker || this.data.materialPicker.loading) return
    this.setData({
      materialPicker: Object.assign({}, this.data.materialPicker, {
        query: event.detail.value
      })
    })
  },

  async searchMaterialPicker() {
    if (!this.data.materialPicker) return
    await this.readMaterialPickerPage(this.data.materialPicker.query, null, false)
  },

  async loadMoreMaterials() {
    const picker = this.data.materialPicker
    if (!picker || !picker.nextAfterId) return
    await this.readMaterialPickerPage(picker.query, picker.nextAfterId, true)
  },

  async readMaterialPickerPage(query, afterId, append) {
    if (!this.data.materialPicker) return
    const generation = (this._pickerGeneration || 0) + 1
    this._pickerGeneration = generation
    this.setData({
      materialPicker: Object.assign({}, this.data.materialPicker, { loading: true, error: '' })
    })
    try {
      const response = materialCatalog.validatePage(await transport.listMaterials(query, afterId))
      if (generation !== this._pickerGeneration || !this.data.materialPicker) return
      const items = (append ? this.data.materialPicker.items : [])
        .concat(response.items.map(presentMaterial))
      if (
        new Set(items.map((item) => item.material_id)).size !== items.length ||
        new Set(items.map((item) => item.sku_code)).size !== items.length
      ) throw new Error('正式物料目录跨页返回重复物料，已失败关闭')
      this.setData({
        materialPicker: Object.assign({}, this.data.materialPicker, {
          items,
          nextAfterId: response.next_after_id,
          loading: false,
          error: ''
        })
      })
    } catch (error) {
      if (generation !== this._pickerGeneration || !this.data.materialPicker) return
      this.setData({
        materialPicker: Object.assign({}, this.data.materialPicker, {
          items: [], nextAfterId: null, loading: false,
          error: error.message || '正式物料目录读取失败'
        })
      })
    }
  },

  chooseMaterial(event) {
    const picker = this.data.materialPicker
    if (!picker || !this.data.form) return
    const materialId = String(event.currentTarget.dataset.id || '')
    const selected = picker.items.find((item) => item.material_id === materialId)
    if (!selected) {
      toast(null, '正式物料选择锚点已失效')
      return
    }
    const targetField = picker.target === 'material' ? 'material' : 'substituteMaterial'
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => line.key === picker.lineKey
          ? Object.assign({}, line, { [targetField]: selected })
          : line)
      }),
      materialPicker: null
    })
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
  },

  clearSubstitute(event) {
    if (!this.data.form) return
    const key = Number(event.currentTarget.dataset.key)
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => line.key === key
          ? Object.assign({}, line, { substituteMaterial: null })
          : line)
      })
    })
  },

  closeMaterialPicker() {
    this._pickerGeneration = (this._pickerGeneration || 0) + 1
    this.setData({ materialPicker: null })
  },

  async startEdit() {
    const detail = this.data.detail
    if (
      !detail ||
      !detail.canEdit ||
      !['draft', 'returned'].includes(detail.states.request_status)
    ) return
    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    try {
      const snapshot = adapterModule.validateEditableDraft(
        await transport.loadDraftForEdit(detail.request_id),
        detail.request_id,
        detail.request_version
      )
      const materials = await this.resolveDraftMaterials(snapshot.draft)
      ensureFileUploadControllers(this)
      this._requestAttachmentUploads.bind(
        `${this._uploadIdentity}:request:edit:${snapshot.request_id}:${snapshot.request_version}`
      )
      this.setData({
        detail: null,
        formMode: 'edit',
        formRequestId: snapshot.request_id,
        formRequestVersion: snapshot.request_version,
        form: draftToForm(this, snapshot.draft, materials),
        writePending: false,
        pendingWriteMessage: ''
      })
    } catch (error) {
      toast(error, '可编辑草稿读取失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  cancelForm() {
    ensureRegistries(this)
    const createPending = this._createRegistry.get()
    const updatePending = this.data.formMode === 'edit'
      ? this._mutationRegistry.get(this.data.formRequestId)
      : null
    if (createPending || updatePending) {
      toast(null, '写入结果尚未确认，必须保留原内容与坐标')
      return
    }
    if (this._requestAttachmentUploads) this._requestAttachmentUploads.clear()
    this.setData({
      formMode: '',
      formRequestId: '',
      formRequestVersion: 0,
      form: null,
      materialPicker: null,
      writePending: false,
      pendingWriteMessage: ''
    })
  },

  formInput(event) {
    const field = String(event.currentTarget.dataset.field || '')
    if (!this.data.form || !Object.prototype.hasOwnProperty.call(this.data.form, field)) return
    this.setData({ form: Object.assign({}, this.data.form, { [field]: event.detail.value }) })
  },

  lineInput(event) {
    const key = Number(event.currentTarget.dataset.key)
    const field = String(event.currentTarget.dataset.field || '')
    const allowed = ['requestedQty', 'requiredDate', 'note']
    if (!this.data.form || !allowed.includes(field)) return
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.map((line) => (
          line.key === key ? Object.assign({}, line, { [field]: event.detail.value }) : line
        ))
      })
    })
  },

  addLine() {
    if (!this.data.form || this.data.busy) return
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.concat(emptyLine(this))
      })
    })
  },

  removeLine(event) {
    if (!this.data.form || this.data.form.lines.length <= 1 || this.data.busy) return
    const key = Number(event.currentTarget.dataset.key)
    this.setData({
      form: Object.assign({}, this.data.form, {
        lines: this.data.form.lines.filter((line) => line.key !== key)
      })
    })
  },

  async chooseRequestAttachment() {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      await this._requestAttachmentUploads.select()
    } catch (error) {
      toast(error, '需求附件上传失败')
    }
  },

  async retryRequestAttachment(event) {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      await this._requestAttachmentUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '需求附件重试失败')
    }
  },

  removeRequestAttachment(event) {
    if (!this.data.form || this.data.busy || this.data.writePending) return
    ensureFileUploadControllers(this)
    try {
      this._requestAttachmentUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '需求附件不能移除')
    }
  },

  async saveDraft() {
    if (!this.data.form || !this.data.formMode) return
    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    let responseValidated = false
    try {
      ensureFileUploadControllers(this)
      const uploaded = availableFiles(this._requestAttachmentUploads, 'request_attachment')
      const draft = formToDraft(this.data.form, uploaded)
      if (this.data.formMode === 'create') {
        const intent = this._createRegistry.begin({ body: draft })
        this.setData({
          writePending: true,
          pendingWriteMessage: `草稿创建结果确认中（${intent.client_draft_key}）；重试只复用原请求坐标。`
        })
        const result = contract.validateMaterialRequestCreateResult(
          await transport.createDraft(intent)
        )
        responseValidated = true
        const reread = contract.validateMaterialRequestDetail(
          await transport.detail(result.request_id),
          result.request_id
        )
        if (!axesMatch(result, reread)) throw new Error('创建响应与详情回读不一致，仍待人工核验')
        this._createRegistry.confirm(intent.client_draft_key, intent.signature)
        this._requestAttachmentUploads.clear()
        this.setData({
          formMode: '',
          form: null,
          detail: presentDetail(reread, this._access),
          writePending: false,
          pendingWriteMessage: '',
          notice: '草稿已创建并完成精确详情回读'
        })
        wx.showToast({ title: '草稿已保存', icon: 'success' })
      } else {
        const requestId = this.data.formRequestId
        const previousVersion = this.data.formRequestVersion
        const intent = this._mutationRegistry.begin({
          requestId,
          action: 'update',
          path: `/v1/material-requests/${requestId}`,
          body: Object.assign({}, draft, { expected_version: previousVersion }),
          expectedVersion: previousVersion
        })
        this.setData({
          writePending: true,
          pendingWriteMessage: `草稿修改结果确认中（${intent.headers['X-Request-ID']}）；重试只复用原请求坐标。`
        })
        const result = contract.validateMaterialRequestMutationResult(
          await transport.mutate(intent),
          { requestId, action: 'update', previousVersion }
        )
        responseValidated = true
        const reread = contract.validateMaterialRequestDetail(
          await transport.detail(requestId),
          requestId
        )
        if (!axesMatch(result, reread)) throw new Error('修改响应与详情回读不一致，仍待人工核验')
        this._mutationRegistry.confirm(intent.request_id, intent.signature)
        this._requestAttachmentUploads.clear()
        this.setData({
          formMode: '',
          form: null,
          detail: presentDetail(reread, this._access),
          writePending: false,
          pendingWriteMessage: '',
          notice: '草稿修改已完成并精确回读'
        })
        wx.showToast({ title: '草稿已更新', icon: 'success' })
      }
    } catch (error) {
      if (this.data.formMode === 'create') {
        const pending = this._createRegistry.get()
        if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
          this._createRegistry.clearDefinitiveRejection(
            pending.client_draft_key,
            pending.signature
          )
          this.setData({ writePending: false, pendingWriteMessage: '' })
        } else if (pending) {
          this.setData({
            writePending: true,
            pendingWriteMessage: `创建结果仍未确认（${pending.client_draft_key}）；禁止修改内容后生成新坐标。`
          })
        }
      } else {
        const pending = this._mutationRegistry.get(this.data.formRequestId)
        if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
          this._mutationRegistry.clearDefinitiveRejection(
            pending.request_id,
            pending.signature
          )
          this.setData({ writePending: false, pendingWriteMessage: '' })
        } else if (pending) {
          this.setData({
            writePending: true,
            pendingWriteMessage: `修改结果仍未确认（${pending.headers['X-Request-ID']}）；禁止同对象其他写入。`
          })
        }
      }
      toast(error, '草稿保存失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  openSubmitConfirm() {
    const detail = this.data.detail
    if (
      !detail ||
      !detail.canSubmit ||
      !['draft', 'returned'].includes(detail.states.request_status)
    ) return
    this.setData({ submitConfirm: true })
  },

  cancelSubmit() {
    this.setData({ submitConfirm: false })
  },

  async confirmSubmit() {
    const before = this.data.detail
    if (
      !before ||
      !before.canSubmit ||
      !['draft', 'returned'].includes(before.states.request_status)
    ) return
    ensureRegistries(this)
    this.setData({ submitConfirm: false, busy: true, notice: '' })
    let responseValidated = false
    try {
      const intent = this._mutationRegistry.begin({
        requestId: before.request_id,
        action: 'submit',
        path: `/v1/material-requests/${before.request_id}/submit`,
        body: { expected_version: before.request_version },
        expectedVersion: before.request_version
      })
      const result = contract.validateMaterialRequestMutationResult(
        await transport.mutate(intent),
        {
          requestId: before.request_id,
          action: 'submit',
          previousVersion: before.request_version
        }
      )
      responseValidated = true
      const reread = contract.validateMaterialRequestDetail(
        await transport.detail(before.request_id),
        before.request_id
      )
      if (!axesMatch(result, reread)) throw new Error('提交响应与详情回读不一致，仍待人工核验')
      this._mutationRegistry.confirm(intent.request_id, intent.signature)
      this.setData({
        detail: presentDetail(reread, this._access),
        notice: '需求已提交并精确回读；后续状态仍分别展示'
      })
      wx.showToast({ title: '需求已提交', icon: 'success' })
    } catch (error) {
      const pending = this._mutationRegistry.get(before.request_id)
      if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
        this._mutationRegistry.clearDefinitiveRejection(
          pending.request_id,
          pending.signature
        )
      }
      toast(error, '需求提交失败')
    } finally {
      this.setData({ busy: false })
    }
  },

  openApprovalProcess(event) {
    const kind = String(event.currentTarget.dataset.kind || '')
    const detail = this.data.detail
    if (!detail || !this._access || ![
      'internal', 'external_registration', 'external_verification'
    ].includes(kind)) return
    if (
      (kind === 'internal' && !detail.canProcessInternal) ||
      (kind === 'external_registration' && !detail.canRegisterExternal) ||
      (kind === 'external_verification' && !detail.canVerifyExternal)
    ) {
      toast(null, '当前访问权限与详情允许动作不一致，已停止处理')
      return
    }
    const step = currentApprovalStep(detail)
    if (!step) {
      toast(null, '当前审批步骤锚点缺失，已停止处理')
      return
    }
    let lines = []
    try {
      if (kind !== 'external_verification') lines = approvalInputLines(detail)
    } catch (error) {
      toast(error, '审批逐行数量读取失败')
      return
    }
    const evidence = ((detail.approval_instance &&
      detail.approval_instance.external_evidence_summaries) || [])
      .filter((item) => item.step_id === step.step_id && item.status === 'pending_verification')
    const action = kind === 'internal'
      ? ['approve', 'return', 'reject'].find((item) => detail.allowed_actions.includes(item)) || 'reject'
      : 'approve'
    ensureFileUploadControllers(this)
    if (kind === 'external_registration') {
      this._externalEvidenceUploads.bind(
        `${this._uploadIdentity}:external:${detail.request_id}:${detail.request_version}:${step.step_id}:${step.version}`
      )
    } else {
      this._externalEvidenceUploads.clear()
    }
    this.setData({
      processing: {
        kind,
        stepId: step.step_id,
        stepVersion: step.version,
        action,
        canApprove: kind !== 'internal' || detail.allowed_actions.includes('approve'),
        canReturn: kind !== 'internal' || detail.allowed_actions.includes('return'),
        canReject: kind !== 'internal' || detail.allowed_actions.includes('reject'),
        lines,
        comment: '',
        externalApproverName: '',
        externalReferenceNo: '',
        externalDecidedAt: '',
        registrationId: evidence.length === 1 ? evidence[0].registration_id : '',
        evidence: evidence.length === 1 ? evidence[0] : null,
        verificationDecision: 'accept',
        error: '',
        pendingMessage: ''
      }
    })
  },

  chooseProcessAction(event) {
    const action = String(event.currentTarget.dataset.action || '')
    if (
      !this.data.processing ||
      this.data.processing.pendingMessage ||
      !['approve', 'return', 'reject'].includes(action)
    ) return
    if (
      this.data.processing.kind === 'internal' &&
      !this.data.detail.allowed_actions.includes(action)
    ) return
    this.setData({
      processing: Object.assign({}, this.data.processing, { action, error: '' })
    })
  },

  processFieldInput(event) {
    if (!this.data.processing || this.data.processing.pendingMessage) return
    const field = String(event.currentTarget.dataset.field || '')
    const allowed = [
      'comment', 'externalApproverName',
      'externalReferenceNo', 'externalDecidedAt'
    ]
    if (!allowed.includes(field)) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        [field]: event.detail.value,
        error: ''
      })
    })
  },

  async chooseExternalEvidence() {
    if (
      !this.data.processing ||
      this.data.processing.kind !== 'external_registration' ||
      this.data.processing.pendingMessage ||
      this.data.busy
    ) return
    ensureFileUploadControllers(this)
    try {
      await this._externalEvidenceUploads.select()
    } catch (error) {
      toast(error, '外部审批证据上传失败')
    }
  },

  async retryExternalEvidence(event) {
    if (!this.data.processing || this.data.processing.pendingMessage || this.data.busy) return
    ensureFileUploadControllers(this)
    try {
      await this._externalEvidenceUploads.retry(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '外部审批证据重试失败')
    }
  },

  removeExternalEvidence(event) {
    if (!this.data.processing || this.data.processing.pendingMessage || this.data.busy) return
    ensureFileUploadControllers(this)
    try {
      this._externalEvidenceUploads.remove(String(event.currentTarget.dataset.key || ''))
    } catch (error) {
      toast(error, '外部审批证据不能移除')
    }
  },

  processLineInput(event) {
    if (!this.data.processing || this.data.processing.pendingMessage) return
    const requestLineId = String(event.currentTarget.dataset.id || '')
    const field = String(event.currentTarget.dataset.field || '')
    if (!['decisionQty', 'reason'].includes(field)) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        error: '',
        lines: this.data.processing.lines.map((line) => line.requestLineId === requestLineId
          ? Object.assign({}, line, { [field]: event.detail.value })
          : line)
      })
    })
  },

  chooseVerificationDecision(event) {
    const decision = String(event.currentTarget.dataset.decision || '')
    if (
      !this.data.processing ||
      this.data.processing.pendingMessage ||
      !['accept', 'reject'].includes(decision)
    ) return
    this.setData({
      processing: Object.assign({}, this.data.processing, {
        verificationDecision: decision,
        error: ''
      })
    })
  },

  cancelApprovalProcess() {
    const detail = this.data.detail
    ensureRegistries(this)
    if (detail && this._mutationRegistry.get(detail.request_id)) {
      this.setData({
        processing: Object.assign({}, this.data.processing, {
          error: '处理结果尚未确认，必须保留原内容与请求坐标'
        })
      })
      return
    }
    if (this._externalEvidenceUploads) this._externalEvidenceUploads.clear()
    this.setData({ processing: null })
  },

  async submitApprovalProcess() {
    const before = this.data.detail
    const process = this.data.processing
    if (!before || !process || !this._access) return
    const step = currentApprovalStep(before)
    if (!step || step.step_id !== process.stepId || step.version !== process.stepVersion) {
      this.setData({
        processing: Object.assign({}, process, {
          error: '当前审批步骤或版本已变化，请重新读取详情'
        })
      })
      return
    }
    const allowedActions = new Set(before.allowed_actions)
    const internalPermission = step.step_no === 1
      ? this._access.can_approve_region
      : step.step_no === 2 && this._access.can_approve_headquarters
    const pendingEvidence = ((before.approval_instance &&
      before.approval_instance.external_evidence_summaries) || []).filter((item) => (
      item.step_id === step.step_id && item.status === 'pending_verification'
    ))
    const authorized = process.kind === 'internal'
      ? step.source_mode === 'internal' && internalPermission && allowedActions.has(process.action)
      : process.kind === 'external_registration'
        ? step.source_mode === 'external_registration' && this._access.can_register_external &&
          allowedActions.has('register_external_approval')
        : step.source_mode === 'external_registration' && this._access.can_verify_external &&
          allowedActions.has('verify_external_approval') && pendingEvidence.length === 1 &&
          pendingEvidence[0].registration_id === process.registrationId
    if (!authorized) {
      this.setData({
        processing: Object.assign({}, process, {
          error: '当前权限、详情允许动作或证据锚点已不满足处理条件，已停止写入'
        })
      })
      return
    }
    let body
    let intentAction
    let path
    try {
      const comment = process.comment.trim()
      if (
        process.kind !== 'external_verification' &&
        ['return', 'reject'].includes(process.action) &&
        !comment
      ) throw new Error('退回或驳回必须填写处理意见')
      if (process.kind === 'external_verification') {
        if (!NONZERO_UUID.test(process.registrationId)) throw new Error('待复核证据锚点无效')
        if (process.verificationDecision === 'reject' && !comment) {
          throw new Error('复核拒绝必须填写原因')
        }
        body = {
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          decision: process.verificationDecision,
          comment
        }
        intentAction = 'verify_external_approval'
        path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence/${process.registrationId}/verification`
      } else {
        const linePayload = approvalLinePayload(process)
        body = Object.assign({
          expected_request_version: before.request_version,
          expected_step_version: step.version,
          action: process.action
        }, linePayload, { comment })
        if (process.kind === 'internal') {
          intentAction = process.action
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/decision`
        } else {
          ensureFileUploadControllers(this)
          const evidence = availableFiles(
            this._externalEvidenceUploads,
            'external_approval_evidence'
          )
          const approver = process.externalApproverName.trim()
          const reference = process.externalReferenceNo.trim()
          const decidedAt = process.externalDecidedAt.trim()
          if (evidence.length !== 1) throw new Error('必须先上传并完成一份外部审批证据的 available 确认')
          if (!approver || approver.length > 160) throw new Error('星星总部审批人无效')
          if (!SAFE_EXTERNAL_REFERENCE.test(reference)) throw new Error('外部审批参考号无效')
          if (!AWARE_TIMESTAMP.test(decidedAt) || !Number.isFinite(Date.parse(decidedAt))) {
            throw new Error('外部决定时间必须是包含时区的 ISO8601 时间')
          }
          body = Object.assign({
            expected_request_version: before.request_version,
            expected_step_version: step.version,
            evidence_file_id: evidence[0].file_id,
            external_approver_name: approver,
            external_reference_no: reference,
            external_decided_at: decidedAt,
            action: process.action
          }, linePayload, { comment })
          intentAction = 'register_external_approval'
          path = `/v1/material-requests/${before.request_id}/approval-steps/${step.step_id}/external-evidence`
        }
      }
    } catch (error) {
      this.setData({
        processing: Object.assign({}, process, { error: error.message, pendingMessage: '' })
      })
      return
    }

    ensureRegistries(this)
    this.setData({ busy: true, notice: '' })
    let responseValidated = false
    try {
      const intent = this._mutationRegistry.begin({
        requestId: before.request_id,
        action: intentAction,
        path,
        body,
        expectedVersion: before.request_version
      })
      this.setData({
        processing: Object.assign({}, process, {
          error: '',
          pendingMessage: `处理结果确认中（${intent.headers['X-Request-ID']}）；重试只复用原坐标。`
        })
      })
      const result = contract.validateMaterialRequestMutationResult(
        await transport.mutate(intent),
        { requestId: before.request_id, action: intentAction, previousVersion: before.request_version }
      )
      responseValidated = true
      const reread = contract.validateMaterialRequestDetail(
        await transport.detail(before.request_id),
        before.request_id
      )
      if (!approvalMutationMatches(result, reread)) {
        throw new Error('处理响应与审批锚点/状态回读不一致，仍待人工核验')
      }
      this._mutationRegistry.confirm(intent.request_id, intent.signature)
      if (process.kind === 'external_registration') this._externalEvidenceUploads.clear()
      this.setData({
        detail: presentDetail(reread, this._access),
        processing: null,
        notice: '审批处理已完成精确回读；分配、占用及履约状态未被合并。'
      })
    } catch (error) {
      const pending = this._mutationRegistry.get(before.request_id)
      if (!responseValidated && pending && adapterModule.isDefinitiveRejection(error)) {
        this._mutationRegistry.clearDefinitiveRejection(pending.request_id, pending.signature)
        this.setData({
          processing: Object.assign({}, process, {
            error: error.message || '审批处理被明确拒绝', pendingMessage: ''
          })
        })
      } else if (pending) {
        this.setData({
          processing: Object.assign({}, process, {
            error: error.message || '审批处理结果未确认',
            pendingMessage: `处理结果仍未确认（${pending.headers['X-Request-ID']}）；禁止生成新坐标或执行其他动作。`
          })
        })
      }
    } finally {
      this.setData({ busy: false })
    }
  }
})
