const test = require('node:test')
const assert = require('node:assert/strict')

const {
  MINIPROGRAM_MAXIMUM_FILE_SIZE_BYTES,
  createFormalFileUploadController,
  executeFormalFileUpload,
  normalizeSelectedFormalFile,
  prepareFormalFileUpload,
  sha256Hex
} = require('../utils/formal-file-upload')


const FILE_ID = '90000000-0000-4000-8000-000000000001'
const NOW = Date.parse('2026-09-01T08:00:00Z')
const CONTENT = new TextEncoder().encode('abc')
const SHA = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'


function localFile(overrides = {}) {
  return Object.assign({
    tempFilePath: '/tmp/formal-photo.jpg',
    name: '现场照片.jpg',
    size: CONTENT.byteLength,
    mimeType: 'image/jpeg'
  }, overrides)
}

function intent(overrides = {}) {
  return Object.assign({
    schema_version: '1.0',
    file_id: FILE_ID,
    purpose: 'request_attachment',
    status: 'pending',
    upload: {
      method: 'PUT',
      url: 'https://private-bucket.oss-cn-test.aliyuncs.com/key?signature=opaque',
      expires_at: '2026-09-01T08:10:00Z',
      headers: {
        'Content-Type': 'image/jpeg',
        'x-oss-meta-sha256': SHA,
        'x-oss-meta-file-id': FILE_ID,
        'x-oss-forbid-overwrite': 'true'
      }
    },
    idempotency_replayed: false
  }, overrides)
}

function completion(overrides = {}) {
  return Object.assign({
    schema_version: '1.0',
    file_id: FILE_ID,
    purpose: 'request_attachment',
    status: 'available',
    verified_at: '2026-09-01T08:01:00Z',
    already_available: false
  }, overrides)
}

async function prepared() {
  return prepareFormalFileUpload(localFile(), 'request_attachment', {
    readFile: async () => CONTENT
  })
}


test('SHA-256 implementation matches a standard known vector', () => {
  assert.equal(sha256Hex(CONTENT), SHA)
})

test('prepare enforces exact local metadata, byte size and stable safe coordinates', async () => {
  const value = await prepared()
  assert.equal(value.sha256, SHA)
  assert.equal(value.size_bytes, 3)
  assert.match(value.intent_coordinates.requestId, /^wxreq-[a-f0-9]{36}$/)
  assert.match(value.intent_coordinates.idempotencyKey, /^wxidem-[a-f0-9]{36}$/)

  await assert.rejects(
    prepareFormalFileUpload(
      localFile({ size: 4 }),
      'request_attachment',
      { readFile: async () => CONTENT }
    ),
    /大小与本地读取结果不一致/
  )
  await assert.rejects(
    prepareFormalFileUpload(
      localFile({ size: MINIPROGRAM_MAXIMUM_FILE_SIZE_BYTES + 1 }),
      'request_attachment',
      { readFile: async () => CONTENT }
    ),
    /10 MB/
  )
})

test('mini selection uses the reviewed extension to MIME map and rejects unknown or mismatched types', () => {
  assert.deepEqual(normalizeSelectedFormalFile({
    path: '/tmp/现场照片.JPG',
    name: '现场照片.JPG',
    size: 3,
    type: 'image/jpeg'
  }), {
    tempFilePath: '/tmp/现场照片.JPG',
    name: '现场照片.JPG',
    size: 3,
    mimeType: 'image/jpeg'
  })
  assert.throws(
    () => normalizeSelectedFormalFile({ path: '/tmp/a.txt', name: 'a.txt', size: 3 }),
    /禁止猜测未知文件类型/
  )
  assert.throws(
    () => normalizeSelectedFormalFile({
      path: '/tmp/a.jpg', name: 'a.jpg', size: 3, mimeType: 'image/png'
    }),
    /MIME 类型与扩展名不一致/
  )
})

test('upload controller exposes available only after completion, retries exact coordinates and clears on object, authorization or identity change', async () => {
  const selected = localFile()
  const preparedValue = Object.freeze({
    content: CONTENT,
    purpose: 'stocktake_evidence',
    original_filename: selected.name,
    size_bytes: selected.size,
    mime_type: selected.mimeType,
    sha256: SHA,
    intent_coordinates: Object.freeze({ requestId: `wxreq-${'a'.repeat(36)}`, idempotencyKey: `wxidem-${'b'.repeat(36)}` }),
    complete_coordinates: Object.freeze({ requestId: `wxreq-${'c'.repeat(36)}`, idempotencyKey: `wxidem-${'d'.repeat(36)}` })
  })
  const executeCalls = []
  const snapshots = []
  let attempt = 0
  const controller = createFormalFileUploadController({
    purpose: 'stocktake_evidence',
    multiple: true,
    chooseFiles: async () => [selected],
    prepare: async () => preparedValue,
    execute: async (value) => {
      executeCalls.push(value)
      attempt += 1
      if (attempt === 1) throw new Error('上传结果待确认')
      return {
        file_id: FILE_ID,
        purpose: value.purpose,
        status: 'available',
        verified_at: '2026-09-01T08:01:00Z',
        sha256: value.sha256,
        size_bytes: value.size_bytes,
        mime_type: value.mime_type
      }
    },
    onChange: (snapshot) => snapshots.push(snapshot)
  })
  controller.bind('person-1:v1:task-1:scope-1')
  await assert.rejects(controller.select(), /上传结果待确认/)
  assert.equal(controller.snapshot().availableFiles.length, 0)
  assert.equal(controller.snapshot().files[0].canRetry, true)
  await controller.retry(controller.snapshot().files[0].key)
  assert.equal(controller.snapshot().availableFiles[0].file_id, FILE_ID)
  assert.equal(controller.snapshot().files[0].statusLabel, 'available（已完成严格确认）')
  assert.equal(executeCalls[0], preparedValue)
  assert.equal(executeCalls[1], preparedValue)
  for (const binding of [
    'person-1:v1:task-2:scope-1',
    'person-1:v2:task-2:scope-1',
    'person-2:v2:task-2:scope-1'
  ]) {
    controller.bind(binding)
    assert.equal(controller.snapshot().files.length, 0)
    if (!binding.startsWith('person-2:')) {
      await controller.select()
      assert.equal(controller.snapshot().availableFiles.length, 1)
    }
  }
  assert.equal(snapshots.at(-1).blocking, false)
})

test('execute sends exact API evidence, signed raw PUT and completion readback', async () => {
  const value = await prepared()
  const calls = []
  const putCalls = []
  const requestApi = async (path, options) => {
    calls.push([path, options])
    return path.endsWith('/complete') ? completion() : intent()
  }
  const objectPut = async (url, content, headers) => {
    putCalls.push([url, content, headers])
    return { statusCode: 200 }
  }

  const result = await executeFormalFileUpload(value, {
    requestApi,
    objectPut,
    now: () => NOW
  })

  assert.deepEqual(result, {
    file_id: FILE_ID,
    purpose: 'request_attachment',
    status: 'available',
    verified_at: '2026-09-01T08:01:00Z',
    sha256: SHA,
    size_bytes: 3,
    mime_type: 'image/jpeg'
  })
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[0][1].data, {
    purpose: 'request_attachment',
    original_filename: '现场照片.jpg',
    size_bytes: 3,
    mime_type: 'image/jpeg',
    sha256: SHA
  })
  assert.equal(calls[0][1].requestId, value.intent_coordinates.requestId)
  assert.equal(calls[0][1].idempotencyKey, value.intent_coordinates.idempotencyKey)
  assert.equal(putCalls.length, 1)
  assert.equal(putCalls[0][0], intent().upload.url)
  assert.equal(putCalls[0][1], value.content)
  assert.deepEqual(putCalls[0][2], intent().upload.headers)
})

test('default mini-program transport uses raw ArrayBuffer PUT without API authorization', async (context) => {
  const value = await prepared()
  const originalWx = global.wx
  const requests = []
  global.wx = {
    request(options) {
      requests.push(options)
      options.success({ statusCode: 200, data: '' })
    }
  }
  context.after(() => { global.wx = originalWx })
  const requestApi = async (path) => (
    path.endsWith('/complete') ? completion() : intent()
  )

  await executeFormalFileUpload(value, { requestApi, now: () => NOW })

  assert.equal(requests.length, 1)
  assert.equal(requests[0].method, 'PUT')
  assert.ok(requests[0].data instanceof ArrayBuffer)
  assert.deepEqual(requests[0].header, intent().upload.headers)
  assert.equal(requests[0].header.Authorization, undefined)
})

test('uncertain PUT is resolved only by completion and both failures keep the same coordinate', async () => {
  const value = await prepared()
  const calls = []
  const successRequest = async (path, options) => {
    calls.push([path, options])
    return path.endsWith('/complete') ? completion() : intent()
  }
  const uncertainPut = async () => { throw new Error('network unavailable') }
  const result = await executeFormalFileUpload(value, {
    requestApi: successRequest,
    objectPut: uncertainPut,
    now: () => NOW
  })
  assert.equal(result.status, 'available')

  const failedRequest = async (path, options) => {
    calls.push([path, options])
    if (path.endsWith('/complete')) throw new Error('HEAD unavailable')
    return intent()
  }
  await assert.rejects(
    executeFormalFileUpload(value, {
      requestApi: failedRequest,
      objectPut: uncertainPut,
      now: () => NOW
    }),
    /只能使用原请求坐标重试/
  )
  assert.equal(calls[2][1].idempotencyKey, value.intent_coordinates.idempotencyKey)
})

test('insecure URLs and any signed-header mismatch fail before bytes are sent', async () => {
  const value = await prepared()
  for (const badIntent of [
    intent({ upload: Object.assign({}, intent().upload, { url: 'http://bucket/key' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://trusted.example\\@evil.example/key?signature=opaque' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://user@trusted.example/key?signature=opaque' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://trusted.example/key?signature=opaque#fragment' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https:///key?signature=opaque' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://trusted.example:99999/key?signature=opaque' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://-invalid.example/key?signature=opaque' }) }),
    intent({ upload: Object.assign({}, intent().upload, { url: 'https://0177.0.0.1/key?signature=opaque' }) }),
    intent({
      upload: Object.assign({}, intent().upload, {
        headers: Object.assign({}, intent().upload.headers, {
          'x-oss-meta-sha256': 'cd'.repeat(32)
        })
      })
    })
  ]) {
    let putCalled = false
    await assert.rejects(
      executeFormalFileUpload(value, {
        requestApi: async () => badIntent,
        objectPut: async () => { putCalled = true },
        now: () => NOW
      })
    )
    assert.equal(putCalled, false)
  }
})
