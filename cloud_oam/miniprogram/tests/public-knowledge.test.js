const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '..')
const catalogPath = require.resolve('../data/knowledge-catalog.json')
const pagePath = require.resolve('../pages/knowledge/index.js')

function loadPage(context, catalog) {
  const previous = require.cache[catalogPath]
  require.cache[catalogPath] = { id: catalogPath, filename: catalogPath, loaded: true, exports: catalog }
  delete require.cache[pagePath]
  let page
  global.Page = (definition) => { page = definition; page.setData = (values) => Object.assign(page.data, values) }
  global.wx = { request() { throw new Error('unexpected request') }, login() { throw new Error('unexpected login') }, reLaunch() { throw new Error('unexpected redirect') } }
  require(pagePath)
  context.after(() => {
    if (previous) require.cache[catalogPath] = previous
    else delete require.cache[catalogPath]
    delete require.cache[pagePath]
    delete global.Page
    delete global.wx
  })
  page.onLoad()
  return page
}

test('pending public source stays empty without business transport or login', (context) => {
  const page = loadPage(context, { status: 'pending', items: [], verifiedAt: null })
  assert.equal(page.data.ready, false)
  assert.equal(page.data.total, 0)
  page.onQuery({ detail: { value: '主板' } })
  assert.deepEqual(page.data.results, [])
})

test('mini supports code/name/model/category search and bounded pagination', (context) => {
  const item = { code: 'TEST0', name: '测试主板', category: '板卡', model: 'AC TEST', note: '', sourceSheet: '测试页', sourceRow: 2 }
  const page = loadPage(context, { status: 'ready', verifiedAt: '2026-01-01T00:00:00Z', items: Array.from({ length: 23 }, (_, i) => ({ ...item, code: `TEST${i}`, sourceRow: i + 2 })) })
  assert.equal(page.data.results.length, 20)
  page.onNext()
  assert.equal(page.data.results.length, 3)
  page.onNext()
  assert.equal(page.data.page, 2)
  page.onQuery({ detail: { value: 'test1 主板' } })
  assert.equal(page.data.page, 1)
  assert.equal(page.data.total, 11)
  page.onCategory({ detail: { value: '1' } })
  assert.equal(page.data.total, 11)
  page.onQuery({ detail: { value: 'no-match' } })
  assert.equal(page.data.total, 0)
  page.onClear()
  assert.equal(page.data.total, 23)
})

test('public mini package excludes preserved private and prototype page sources', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  const config = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
  assert.deepEqual(app.pages, ['pages/knowledge/index'])
  for (const name of fs.readdirSync(path.join(root, 'pages'))) {
    if (name === 'knowledge') continue
    assert.ok(config.packOptions.ignore.some((entry) => entry.type === 'folder' && entry.value === `pages/${name}`), name)
  }
  for (const name of fs.readdirSync(path.join(root, 'utils'))) {
    const ignored = config.packOptions.ignore.some((entry) => entry.type === 'file' && entry.value === `utils/${name}`)
    assert.equal(ignored, !['session.js', 'production-guard.js'].includes(name), name)
  }
  assert.ok(config.packOptions.ignore.some((entry) => entry.type === 'folder' && entry.value === 'assets'))
  const source = fs.readFileSync(pagePath, 'utf8')
  assert.doesNotMatch(source, /utils\/(?:api|session)|wx\.(?:login|request)|web-view|pages\/login/)
})
