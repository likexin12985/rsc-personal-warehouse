const api = require('./api')


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ZERO_UUID = '00000000-0000-0000-0000-000000000000'
const SHA256 = /^[0-9a-f]{64}$/
const SAFE_REQUEST_ID = /^wxreq-[a-f0-9]{36}$/
const SAFE_IDEMPOTENCY_KEY = /^wxidem-[a-f0-9]{36}$/
const AWARE_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const PURPOSES = new Set([
  'request_attachment',
  'external_approval_evidence',
  'stocktake_evidence',
  'receipt_exception_evidence'
])
const MINIPROGRAM_MAXIMUM_FILE_SIZE_BYTES = 10 * 1024 * 1024
const ALLOWED_MIME_BY_EXTENSION = Object.freeze({
  pdf: 'application/pdf',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  webp: 'image/webp',
  heic: 'image/heic',
  heif: 'image/heif',
  mp4: 'video/mp4',
  mov: 'video/quicktime'
})
const ALLOWED_SELECT_EXTENSIONS = Object.freeze(Object.keys(ALLOWED_MIME_BY_EXTENSION))


function uploadError(status, message, responseReceived = false) {
  const error = new Error(message)
  error.status = status
  error.responseReceived = responseReceived
  return error
}

function fail(status, message) {
  throw uploadError(status, message)
}

function exactObject(value, keys, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fail(409, `${label}不是有效对象`)
  }
  const actual = Object.keys(value).sort()
  const expected = Array.from(keys).sort()
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) return fail(409, `${label}必须精确包含正式字段`)
  return value
}

function uuid(value, label) {
  if (
    typeof value !== 'string' ||
    !UUID.test(value) ||
    value.toLowerCase() === ZERO_UUID
  ) return fail(409, `${label}无效`)
  return value.toLowerCase()
}

function purpose(value) {
  if (typeof value !== 'string' || !PURPOSES.has(value)) {
    return fail(409, '文件用途无效')
  }
  return value
}

function timestamp(value, label) {
  if (
    typeof value !== 'string' ||
    !AWARE_TIMESTAMP.test(value) ||
    !Number.isFinite(Date.parse(value))
  ) return fail(409, `${label}无效`)
  return value
}

function boundedText(value, label, maximum) {
  if (
    typeof value !== 'string' ||
    !value ||
    value !== value.trim() ||
    value.length > maximum ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) return fail(400, `${label}无效`)
  return value
}

function bytes(value) {
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  }
  return null
}

function selectedFileMimeType(filename) {
  const checked = boundedText(filename, '附件名称', 200)
  const separator = checked.lastIndexOf('.')
  const extension = separator > 0 && separator < checked.length - 1
    ? checked.slice(separator + 1).toLowerCase()
    : ''
  const mimeType = ALLOWED_MIME_BY_EXTENSION[extension]
  if (!mimeType) return fail(400, '附件扩展名不受支持，禁止猜测未知文件类型')
  return mimeType
}

function normalizeSelectedFormalFile(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fail(400, '小程序文件选择结果无效')
  }
  const path = typeof value.path === 'string' && value.path
    ? value.path
    : value.tempFilePath
  const expectedMimeType = selectedFileMimeType(value.name)
  const providedMimeTypes = [value.mimeType, value.type]
    .filter((item) => typeof item === 'string' && item.includes('/'))
  if (
    providedMimeTypes.some((item) => item !== item.trim().toLowerCase()) ||
    providedMimeTypes.some((item) => item !== expectedMimeType)
  ) return fail(400, '附件 MIME 类型与扩展名不一致')
  return validateLocalFile({
    tempFilePath: path,
    name: value.name,
    size: value.size,
    mimeType: expectedMimeType
  }, 'request_attachment')
}

function chooseFormalFiles(count = 1) {
  if (!Number.isSafeInteger(count) || count < 1 || count > 9) {
    return Promise.reject(uploadError(400, '附件选择数量无效'))
  }
  if (typeof wx === 'undefined' || typeof wx.chooseMessageFile !== 'function') {
    return Promise.reject(uploadError(503, '当前小程序环境不支持正式附件选择'))
  }
  return new Promise((resolve, reject) => {
    wx.chooseMessageFile({
      count,
      type: 'file',
      extension: ALLOWED_SELECT_EXTENSIONS,
      success(result) {
        try {
          if (
            !result ||
            !Array.isArray(result.tempFiles) ||
            !result.tempFiles.length ||
            result.tempFiles.length > count
          ) return fail(400, '小程序文件选择结果无效')
          resolve(result.tempFiles.map(normalizeSelectedFormalFile))
        } catch (error) {
          reject(error)
        }
      },
      fail() {
        reject(uploadError(400, '未选择附件'))
      }
    })
  })
}

function validateLocalFile(localFile, selectedPurpose) {
  const value = exactObject(
    localFile,
    ['tempFilePath', 'name', 'size', 'mimeType'],
    '小程序本地附件'
  )
  boundedText(value.tempFilePath, '附件本地路径', 1024)
  boundedText(value.name, '附件名称', 200)
  boundedText(value.mimeType, '附件 MIME 类型', 160)
  if (selectedFileMimeType(value.name) !== value.mimeType) {
    return fail(400, '附件 MIME 类型与扩展名不一致')
  }
  if (
    !Number.isSafeInteger(value.size) ||
    value.size < 1 ||
    value.size > MINIPROGRAM_MAXIMUM_FILE_SIZE_BYTES
  ) return fail(400, '小程序附件大小无效或超过 10 MB 安全上限')
  purpose(selectedPurpose)
  return value
}

function readLocalFile(tempFilePath) {
  if (
    typeof wx === 'undefined' ||
    typeof wx.getFileSystemManager !== 'function'
  ) return Promise.reject(uploadError(503, '当前小程序环境不支持安全读取附件'))
  const manager = wx.getFileSystemManager()
  return new Promise((resolve, reject) => {
    manager.readFile({
      filePath: tempFilePath,
      success(result) {
        const view = bytes(result && result.data)
        if (!view) {
          reject(uploadError(503, '附件二进制读取结果无效'))
          return
        }
        resolve(view)
      },
      fail() {
        reject(uploadError(503, '附件读取失败'))
      }
    })
  })
}

// Compact SHA-256 implementation over one bounded (<=10 MB) byte view.  The
// mini-program path intentionally caps memory; larger formal files use the PC
// client until a separately reviewed OSS multipart protocol exists.
function sha256Hex(value) {
  const input = bytes(value)
  if (!input) return fail(400, '附件二进制内容无效')
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ]
  const state = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
  ]
  const bitLength = input.length * 8
  const paddedLength = Math.ceil((input.length + 9) / 64) * 64
  const padded = new Uint8Array(paddedLength)
  padded.set(input)
  padded[input.length] = 0x80
  const high = Math.floor(bitLength / 0x100000000)
  const low = bitLength >>> 0
  const view = new DataView(padded.buffer)
  view.setUint32(paddedLength - 8, high, false)
  view.setUint32(paddedLength - 4, low, false)
  const words = new Uint32Array(64)
  const rotate = (word, count) => (word >>> count) | (word << (32 - count))
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) {
      words[index] = view.getUint32(offset + index * 4, false)
    }
    for (let index = 16; index < 64; index += 1) {
      const first = words[index - 15]
      const second = words[index - 2]
      const sigma0 = rotate(first, 7) ^ rotate(first, 18) ^ (first >>> 3)
      const sigma1 = rotate(second, 17) ^ rotate(second, 19) ^ (second >>> 10)
      words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = state
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)
      const choice = (e & f) ^ (~e & g)
      const temporary1 = (h + sum1 + choice + constants[index] + words[index]) >>> 0
      const sum0 = rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temporary2 = (sum0 + majority) >>> 0
      h = g
      g = f
      f = e
      e = (d + temporary1) >>> 0
      d = c
      c = b
      b = a
      a = (temporary1 + temporary2) >>> 0
    }
    state[0] = (state[0] + a) >>> 0
    state[1] = (state[1] + b) >>> 0
    state[2] = (state[2] + c) >>> 0
    state[3] = (state[3] + d) >>> 0
    state[4] = (state[4] + e) >>> 0
    state[5] = (state[5] + f) >>> 0
    state[6] = (state[6] + g) >>> 0
    state[7] = (state[7] + h) >>> 0
  }
  return state.map((word) => word.toString(16).padStart(8, '0')).join('')
}

async function prepareFormalFileUpload(localFile, selectedPurpose, dependencies = {}) {
  const checked = validateLocalFile(localFile, selectedPurpose)
  const read = dependencies.readFile || readLocalFile
  const content = bytes(await read(checked.tempFilePath))
  if (!content || content.byteLength !== checked.size) {
    return fail(409, '附件大小与本地读取结果不一致')
  }
  const sha256 = (dependencies.sha256 || sha256Hex)(content)
  if (typeof sha256 !== 'string' || !SHA256.test(sha256)) {
    return fail(503, '附件 SHA-256 计算失败')
  }
  return Object.freeze({
    content,
    purpose: selectedPurpose,
    original_filename: checked.name,
    size_bytes: checked.size,
    mime_type: checked.mimeType,
    sha256,
    intent_coordinates: Object.freeze({
      requestId: api.createRequestId(),
      idempotencyKey: api.createIdempotencyKey()
    }),
    complete_coordinates: Object.freeze({
      requestId: api.createRequestId(),
      idempotencyKey: api.createIdempotencyKey()
    })
  })
}

function validateCoordinate(coordinate) {
  if (
    !coordinate ||
    !SAFE_REQUEST_ID.test(coordinate.requestId || '') ||
    !SAFE_IDEMPOTENCY_KEY.test(coordinate.idempotencyKey || '')
  ) return fail(409, '附件上传请求坐标无效')
  return coordinate
}

function safeSignedUrl(value) {
  if (
    typeof value !== 'string' ||
    value.length < 1 ||
    value.length > 8192 ||
    !/^https:\/\//i.test(value) ||
    /[\s\u0000-\u001f\u007f\\#]/.test(value)
  ) return fail(409, '对象存储上传地址不符合安全要求')

  // Keep the signed URL byte-for-byte unchanged, but validate its authority
  // without relying on platform URL normalisation. Backslashes, userinfo and
  // non-DNS authorities are rejected before wx.request can interpret them.
  const remainder = value.slice('https://'.length)
  const authorityEnd = remainder.search(/[/?]/)
  const authority = authorityEnd === -1
    ? remainder
    : remainder.slice(0, authorityEnd)
  if (!authority || authority.includes('@')) {
    return fail(409, '对象存储上传地址不符合安全要求')
  }
  const authorityParts = authority.split(':')
  if (authorityParts.length > 2) {
    return fail(409, '对象存储上传地址不符合安全要求')
  }
  const hostname = authorityParts[0]
  const port = authorityParts[1]
  const hostnameLabels = hostname.split('.')
  const canonicalIpv4 = hostnameLabels.length === 4 && hostnameLabels.every((label) => (
    /^(?:0|[1-9][0-9]{0,2})$/.test(label) && Number(label) <= 255
  ))
  if (
    hostname.length > 253 ||
    !/^[A-Za-z0-9.-]+$/.test(hostname) ||
    hostname.startsWith('.') ||
    hostname.endsWith('.') ||
    hostnameLabels.some((label) => (
      !label ||
      label.length > 63 ||
      !/^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/.test(label)
    )) ||
    (/^[0-9]+$/.test(hostnameLabels[hostnameLabels.length - 1]) && !canonicalIpv4) ||
    (port !== undefined && (
      !/^[1-9][0-9]{0,4}$/.test(port) ||
      Number(port) > 65535
    ))
  ) return fail(409, '对象存储上传地址不符合安全要求')
  return value
}

function signedHeaders(value, prepared, fileId) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fail(409, '对象存储签名请求头无效')
  }
  const normalized = new Map()
  Object.entries(value).forEach(([name, raw]) => {
    const key = name.toLowerCase()
    if (normalized.has(key) || typeof raw !== 'string' || !raw) {
      return fail(409, '对象存储签名请求头无效')
    }
    normalized.set(key, raw)
  })
  const expected = new Map([
    ['content-type', prepared.mime_type],
    ['x-oss-meta-sha256', prepared.sha256],
    ['x-oss-meta-file-id', fileId],
    ['x-oss-forbid-overwrite', 'true']
  ])
  if (
    normalized.size !== expected.size ||
    Array.from(expected).some(([name, expectedValue]) => normalized.get(name) !== expectedValue)
  ) return fail(409, '对象存储签名请求头与文件证据不一致')
  return Object.freeze(Object.assign({}, value))
}

function projectUploadIntent(value, prepared, now) {
  const object = exactObject(value, [
    'schema_version', 'file_id', 'purpose', 'status', 'upload', 'idempotency_replayed'
  ], '文件上传意图')
  if (object.schema_version !== '1.0' || typeof object.idempotency_replayed !== 'boolean') {
    return fail(409, '文件上传意图版本无效')
  }
  const fileId = uuid(object.file_id, 'file_id')
  if (purpose(object.purpose) !== prepared.purpose) {
    return fail(409, '文件上传意图用途不一致')
  }
  if (object.status === 'available') {
    if (object.upload !== null) return fail(409, '已可用文件不能返回上传凭证')
    return { fileId, status: 'available', upload: null }
  }
  if (object.status !== 'pending') return fail(409, '文件上传意图状态无效')
  const upload = exactObject(
    object.upload,
    ['method', 'url', 'expires_at', 'headers'],
    '对象存储上传凭证'
  )
  if (upload.method !== 'PUT') return fail(409, '对象存储上传方法无效')
  const expiresAt = timestamp(upload.expires_at, '上传凭证过期时间')
  if (Date.parse(expiresAt) <= now + 5000) return fail(409, '对象存储上传凭证已过期')
  return {
    fileId,
    status: 'pending',
    upload: {
      url: safeSignedUrl(upload.url),
      headers: signedHeaders(upload.headers, prepared, fileId)
    }
  }
}

function objectPut(url, content, headers) {
  if (typeof wx === 'undefined' || typeof wx.request !== 'function') {
    return Promise.reject(uploadError(503, '当前小程序环境不支持对象存储直传'))
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: 'PUT',
      data: content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength),
      header: headers,
      dataType: 'text',
      responseType: 'text',
      timeout: 120000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response)
          return
        }
        reject(uploadError(
          response.statusCode,
          `对象存储拒绝附件上传 (${response.statusCode})`,
          true
        ))
      },
      fail() {
        reject(uploadError(0, '对象存储上传结果未确认'))
      }
    })
  })
}

function projectCompletion(value, expectedFileId, expectedPurpose) {
  const object = exactObject(value, [
    'schema_version', 'file_id', 'purpose', 'status', 'verified_at', 'already_available'
  ], '文件完成确认')
  if (
    object.schema_version !== '1.0' ||
    uuid(object.file_id, 'file_id') !== expectedFileId ||
    purpose(object.purpose) !== expectedPurpose ||
    object.status !== 'available' ||
    typeof object.already_available !== 'boolean'
  ) return fail(409, '文件完成确认与上传意图不一致')
  return timestamp(object.verified_at, '文件核验时间')
}

async function executeFormalFileUpload(prepared, dependencies = {}) {
  if (
    !prepared ||
    !bytes(prepared.content) ||
    prepared.content.byteLength !== prepared.size_bytes ||
    !SHA256.test(prepared.sha256 || '')
  ) return fail(409, '待上传文件与已计算证据不一致')
  const intentCoordinate = validateCoordinate(prepared.intent_coordinates)
  const completeCoordinate = validateCoordinate(prepared.complete_coordinates)
  const requestApi = dependencies.requestApi || api.request
  const put = dependencies.objectPut || objectPut
  const intentPayload = await requestApi('/v1/files/upload-intents', {
    method: 'POST',
    data: {
      purpose: prepared.purpose,
      original_filename: prepared.original_filename,
      size_bytes: prepared.size_bytes,
      mime_type: prepared.mime_type,
      sha256: prepared.sha256
    },
    requestId: intentCoordinate.requestId,
    idempotencyKey: intentCoordinate.idempotencyKey
  })
  const intent = projectUploadIntent(
    intentPayload,
    prepared,
    (dependencies.now || Date.now)()
  )
  let uploadFailure = null
  if (intent.status === 'pending') {
    try {
      await put(intent.upload.url, prepared.content, intent.upload.headers)
    } catch (error) {
      uploadFailure = error
    }
  }
  let completionPayload
  try {
    completionPayload = await requestApi(`/v1/files/${intent.fileId}/complete`, {
      method: 'POST',
      requestId: completeCoordinate.requestId,
      idempotencyKey: completeCoordinate.idempotencyKey
    })
  } catch (completionError) {
    if (uploadFailure) {
      return fail(503, '附件上传结果尚未确认；只能使用原请求坐标重试')
    }
    throw completionError
  }
  const verifiedAt = projectCompletion(
    completionPayload,
    intent.fileId,
    prepared.purpose
  )
  return Object.freeze({
    file_id: intent.fileId,
    purpose: prepared.purpose,
    status: 'available',
    verified_at: verifiedAt,
    sha256: prepared.sha256,
    size_bytes: prepared.size_bytes,
    mime_type: prepared.mime_type
  })
}

function uploadSizeLabel(value) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function availableUploadResult(prepared, result) {
  if (
    !result ||
    uuid(result.file_id, 'file_id') !== result.file_id.toLowerCase() ||
    result.purpose !== prepared.purpose ||
    result.status !== 'available' ||
    result.sha256 !== prepared.sha256 ||
    result.size_bytes !== prepared.size_bytes ||
    result.mime_type !== prepared.mime_type
  ) return fail(409, '文件完成结果与当前上传证据不一致，已停止业务绑定')
  return Object.freeze(Object.assign({}, result, {
    original_filename: prepared.original_filename
  }))
}

function createFormalFileUploadController(options = {}) {
  const selectedPurpose = purpose(options.purpose)
  const multiple = options.multiple === true
  const onChange = typeof options.onChange === 'function' ? options.onChange : () => {}
  const choose = options.chooseFiles || chooseFormalFiles
  const prepare = options.prepare || prepareFormalFileUpload
  const execute = options.execute || executeFormalFileUpload
  let bindingKey = ''
  let generation = 0
  let nextKey = 0
  let entries = []

  function publicSnapshot() {
    const files = entries.map((entry) => Object.freeze({
      key: entry.key,
      filename: entry.localFile.name,
      sizeLabel: uploadSizeLabel(entry.localFile.size),
      sha256: entry.prepared ? entry.prepared.sha256 : '',
      status: entry.status,
      statusLabel: ({
        preparing: '计算摘要',
        uploading: '上传并核验',
        available: 'available（已完成严格确认）',
        retry_required: '失败或结果待确认'
      })[entry.status],
      error: entry.error,
      canRetry: entry.status === 'retry_required' && !!entry.prepared,
      canRemove: entry.status === 'available' || (entry.status === 'retry_required' && !entry.prepared)
    }))
    const blocking = entries.some((entry) => entry.status !== 'available')
    return Object.freeze({
      files: Object.freeze(files),
      blocking,
      canChoose: !blocking && (multiple || entries.length === 0),
      availableFiles: Object.freeze(entries.flatMap((entry) => entry.result ? [entry.result] : []))
    })
  }

  function publish() {
    const snapshot = publicSnapshot()
    onChange(snapshot)
    return snapshot
  }

  function replace(key, update) {
    entries = entries.map((entry) => entry.key === key ? update(entry) : entry)
    return publish()
  }

  function clear() {
    generation += 1
    entries = []
    return publish()
  }

  function bind(nextBindingKey) {
    const checked = boundedText(nextBindingKey, '附件业务绑定坐标', 1024)
    if (checked !== bindingKey) {
      bindingKey = checked
      return clear()
    }
    return publicSnapshot()
  }

  async function executeEntry(key, prepared, selectedGeneration) {
    replace(key, (entry) => Object.assign({}, entry, {
      prepared,
      result: null,
      status: 'uploading',
      error: ''
    }))
    try {
      const result = availableUploadResult(prepared, await execute(prepared))
      if (generation !== selectedGeneration) return publicSnapshot()
      return replace(key, (entry) => Object.assign({}, entry, {
        prepared,
        result,
        status: 'available',
        error: ''
      }))
    } catch (error) {
      if (generation !== selectedGeneration) return publicSnapshot()
      replace(key, (entry) => Object.assign({}, entry, {
        prepared,
        result: null,
        status: 'retry_required',
        error: (error && error.message) || '附件上传结果未确认'
      }))
      throw error
    }
  }

  async function add(localFile, selectedGeneration) {
    nextKey += 1
    const key = `formal-upload-${nextKey}`
    entries = entries.concat([{
      key,
      localFile,
      prepared: null,
      result: null,
      status: 'preparing',
      error: ''
    }])
    publish()
    try {
      const prepared = await prepare(localFile, selectedPurpose)
      if (generation !== selectedGeneration) return publicSnapshot()
      return await executeEntry(key, prepared, selectedGeneration)
    } catch (error) {
      if (generation !== selectedGeneration) return publicSnapshot()
      const current = entries.find((entry) => entry.key === key)
      if (current && current.status === 'preparing') {
        replace(key, (entry) => Object.assign({}, entry, {
          status: 'retry_required',
          error: (error && error.message) || '附件准备失败'
        }))
      }
      throw error
    }
  }

  async function select() {
    if (!bindingKey) return fail(409, '附件业务绑定坐标缺失')
    const before = publicSnapshot()
    if (!before.canChoose) return fail(409, '当前附件仍在处理，禁止生成新上传坐标')
    const selectedGeneration = generation
    const files = await choose(1)
    if (generation !== selectedGeneration) return publicSnapshot()
    if (!Array.isArray(files) || files.length !== 1) return fail(400, '附件选择结果无效')
    return add(files[0], selectedGeneration)
  }

  function retry(key) {
    const entry = entries.find((item) => item.key === key)
    if (!entry || entry.status !== 'retry_required' || !entry.prepared) {
      return Promise.reject(uploadError(409, '没有可按原坐标重试的附件'))
    }
    return executeEntry(key, entry.prepared, generation)
  }

  function remove(key) {
    const entry = entries.find((item) => item.key === key)
    if (!entry) return publicSnapshot()
    if (!(
      entry.status === 'available' ||
      (entry.status === 'retry_required' && !entry.prepared)
    )) return fail(409, '未确认上传只能按原 prepared 坐标重试')
    entries = entries.filter((item) => item.key !== key)
    return publish()
  }

  return Object.freeze({
    bind,
    clear,
    select,
    retry,
    remove,
    snapshot: publicSnapshot
  })
}


module.exports = {
  ALLOWED_MIME_BY_EXTENSION,
  MINIPROGRAM_MAXIMUM_FILE_SIZE_BYTES,
  chooseFormalFiles,
  createFormalFileUploadController,
  executeFormalFileUpload,
  normalizeSelectedFormalFile,
  prepareFormalFileUpload,
  sha256Hex
}
