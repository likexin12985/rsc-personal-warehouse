const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')
const { validate } = require('../utils/personal-qr-contract')

const PERSON = '10000000-0000-4000-8000-000000000001'
const ACCOUNT = '30000000-0000-4000-8000-000000000001'
const LOCATION = '20000000-0000-4000-8000-000000000001'
const SERIAL = '50000000-0000-4000-8000-000000000001'

function response(patch = {}) {
  return {
    schema_version: '1.0', object_type: 'serial', object_id: SERIAL,
    display_name: 'SKU-SN · SN SN-001', sku_code: 'SKU-SN', material_name: '测试物料',
    lot_no: null, serial_no: 'SN-001', stock_account_id: ACCOUNT, location_id: LOCATION,
    ledger_cursor: 12, actions: ['view_personal_serials'], ...patch
  }
}

test('QR contract accepts scoped serial result and rejects credential leakage', () => {
  const result = validate(response())
  assert.equal(result.objectType, 'serial')
  assert.equal(result.serialNo, 'SN-001')
  assert.equal(JSON.stringify(result).includes('qr_code'), false)
  assert.throws(() => validate({ ...response(), qr_code: 'SECRET' }), /字段不完整/)
  assert.throws(() => validate({ ...response(), stock_account_id: null }), /范围证明/)
})

function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

function harness(options = {}) {
  const state = { token: 'session-1', user: { person_id: PERSON, authorization_version: 7 }, calls: [], scans: 0 }
  let definition
  const context = {
    Page(value) { definition = value },
    wx: {
      scanCode(settings) {
        state.scans += 1
        if (options.scanFailure) settings.fail(new Error('cancelled'))
        else settings.success({ result: options.scanResult || 'QR-SECRET' })
      },
      switchTab(url) { state.switchTab = url },
      navigateTo(value) { state.navigateTo = value }
    },
    require(module) {
      if (module === '../../utils/session') return { ensureLogin: () => true, getToken: () => state.token, getUser: () => state.user }
      if (module === '../../utils/material-request-adapter') return { formalMaterialRequestAdapter: {
        loadIdentityNoReplay: async () => ({ person_id: PERSON, authorization_version: 7 }),
        loadAccessNoReplay: async () => ({ can_read: true, can_read_material_catalog: true })
      } }
      if (module === '../../utils/api') return { request: async (endpoint) => {
        state.calls.push(endpoint)
        return options.response ? options.response(endpoint) : response()
      } }
      return require(path.resolve(__dirname, '../pages/formal-scan', module))
    }
  }
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../pages/formal-scan/index.js'), 'utf8'), context)
  const instance = Object.assign({}, definition, { data: JSON.parse(JSON.stringify(definition.data)), setData(value) { Object.assign(this.data, JSON.parse(JSON.stringify(value))) } })
  return { instance, state }
}

test('page performs exact no-store read without persisting scanned QR value', async () => {
  const { instance, state } = harness()
  await instance.onShow()
  await instance.scan()
  assert.equal(state.scans, 1)
  assert.equal(instance.data.result.serialNo, 'SN-001')
  assert.equal(Object.prototype.hasOwnProperty.call(instance.data, 'code'), false)
  assert.equal(Object.prototype.hasOwnProperty.call(instance.data, 'scanValue'), false)
  assert.equal(JSON.stringify(instance.data).includes('QR-SECRET'), false)
  assert.match(state.calls[0], /\/v1\/scan\/qr\?code=QR-SECRET$/)
  instance.openSerials()
  assert.equal(state.navigateTo.url, `/pages/formal-personal-warehouse-serials/index?accountId=${ACCOUNT}&serialNo=SN-001`)
})

test('late QR response after page hide cannot restore the result', async () => {
  const pending = deferred()
  const { instance } = harness({ response: () => pending.promise })
  await instance.onShow()
  const scanning = instance.scan()
  instance.onHide()
  pending.resolve(response())
  await scanning
  assert.equal(instance.data.result, null)
  assert.equal(instance.data.state, 'idle')
})
