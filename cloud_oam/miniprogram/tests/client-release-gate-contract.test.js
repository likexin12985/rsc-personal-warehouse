const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const test = require('node:test')

const repositoryRoot = path.resolve(__dirname, '../../..')
const workflowPath = '.github/workflows/client-release-gate.yml'
const workflowSource = fs.readFileSync(path.join(repositoryRoot, workflowPath), 'utf8')

// JSON is a YAML subset. This deliberately JSON-shaped .yml workflow can be
// structurally tested with Node alone, before adding any parser dependency to
// the mini-program. It is not a general YAML parser or a GitHub runner emulator.
// JSON.parse alone accepts duplicate keys; reject those so a later key cannot
// silently replace a reviewed permission or step. Whitespace is not significant.
function parseWorkflow(source) {
  const parsed = JSON.parse(source)
  const tokens = source.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^\s{}\[\]:,"]+/g) || []
  const containers = []
  tokens.forEach((token, index) => {
    if (token === '{') containers.push(new Set())
    else if (token === '[') containers.push(null)
    else if (token === '}' || token === ']') containers.pop()
    else if (token.startsWith('"') && tokens[index + 1] === ':') {
      const keys = containers[containers.length - 1]
      const key = JSON.parse(token)
      assert.ok(keys instanceof Set, 'mapping key must belong to an object')
      assert.ok(!keys.has(key), `duplicate workflow key: ${key}`)
      keys.add(key)
    }
  })
  return parsed
}

function exactKeys(object, expected) {
  assert.ok(object && typeof object === 'object' && !Array.isArray(object))
  assert.deepEqual(Object.keys(object).sort(), [...expected].sort())
}

// The action tags were resolved from their official repositories, then pinned
// to commits. New actions, commands or privileges require explicit review here.
// checkout v7.0.1: https://github.com/actions/checkout/releases/tag/v7.0.1
// setup-node v6.0.0: https://github.com/actions/setup-node/releases/tag/v6.0.0
const permittedSteps = [
  {
    uses: 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
    with: { 'persist-credentials': false }
  },
  {
    uses: 'actions/setup-node@2028fbc5c25fe9cf00d9f06a71cc4710d4507903',
    with: {
      'node-version': '24.19.0',
      'check-latest': false,
      'package-manager-cache': false
    }
  },
  {
    run: 'test "$(node --version)" = "v24.19.0"\n'
      + 'npm install --global --ignore-scripts --no-audit --no-fund pnpm@11.19.0\n'
      + 'test "$(pnpm --version)" = "11.19.0"'
  },
  { run: 'bash cloud_oam/scripts/verify_repository_safety.sh' },
  { 'working-directory': 'cloud_oam/frontend', run: 'pnpm install --frozen-lockfile --ignore-scripts' },
  { 'working-directory': 'cloud_oam/frontend', run: 'pnpm test' },
  { 'working-directory': 'cloud_oam/frontend', run: 'pnpm exec tsc -b' },
  { 'working-directory': 'cloud_oam/frontend', run: 'pnpm exec vite build' },
  { 'working-directory': 'cloud_oam/miniprogram', run: 'node --test tests/*.test.js' }
]

function validateWorkflow(document) {
  exactKeys(document, ['name', 'on', 'permissions', 'concurrency', 'jobs'])
  assert.equal(document.name, 'Client release gate')
  // No paths filters: every main PR receives this check even on docs-only edits.
  // The gate must not inherit pull_request_target privileges or call other jobs.
  assert.deepEqual(document.on, {
    workflow_dispatch: {},
    pull_request: { branches: ['main'] },
    push: { branches: ['main', 'codex/production-readiness-gates'] }
  })
  assert.deepEqual(document.permissions, { contents: 'read' })
  assert.deepEqual(document.concurrency, {
    group: 'client-release-gate-${{ github.ref }}',
    'cancel-in-progress': true
  })
  exactKeys(document.jobs, ['client-release-gate'])
  const job = document.jobs['client-release-gate']
  exactKeys(job, ['name', 'runs-on', 'timeout-minutes', 'defaults', 'steps'])
  assert.equal(job.name, 'Web and WeChat client release gate')
  assert.equal(job['runs-on'], 'ubuntu-24.04')
  assert.equal(job['timeout-minutes'], 15)
  assert.deepEqual(job.defaults, { run: { shell: 'bash' } })
  assert.ok(Array.isArray(job.steps))
  assert.equal(job.steps.length, permittedSteps.length)
  job.steps.forEach((step, index) => {
    const permitted = permittedSteps[index]
    exactKeys(step, ['name', ...Object.keys(permitted)])
    assert.equal(typeof step.name, 'string')
    assert.ok(step.name.trim().length > 0)
    assert.ok(!step.name.includes('${{'), 'step labels must remain literal, not interpolate contexts')
    const { name, ...execution } = step
    assert.deepEqual(execution, permitted, `unreviewed execution in step ${index + 1}`)
  })
}

test('client release gate has structurally pinned read-only execution', () => {
  validateWorkflow(parseWorkflow(workflowSource))
})

test('JSON-shaped YAML accepts harmless layout, label and key-order changes', () => {
  const document = parseWorkflow(workflowSource)
  document.jobs['client-release-gate'].steps[0].name = 'Source checkout'
  const reordered = Object.fromEntries(Object.entries(document).reverse())
  validateWorkflow(parseWorkflow(JSON.stringify(reordered)))
  validateWorkflow(parseWorkflow(JSON.stringify(reordered, null, 4)))
})

test('gate uses the package manager and test script of the locked Web project', () => {
  const manifest = JSON.parse(fs.readFileSync(
    path.join(repositoryRoot, 'cloud_oam/frontend/package.json'), 'utf8'
  ))
  assert.equal(manifest.packageManager, 'pnpm@11.19.0')
  assert.equal(manifest.scripts.test, 'vitest run')
  assert.equal(manifest.scripts.build, 'tsc -b && vite build')
})

const mutations = [
  ['manual trigger removed', (d) => { delete d.on.workflow_dispatch }],
  ['PR trigger removed', (d) => { delete d.on.pull_request }],
  ['PR protected branch changed', (d) => { d.on.pull_request.branches = ['develop'] }],
  ['main push removed', (d) => { d.on.push.branches.shift() }],
  ['continuation branch removed', (d) => { d.on.push.branches.pop() }],
  ['privileged PR trigger added', (d) => { d.on.pull_request_target = {} }],
  ['PR paths filter can leave required check pending', (d) => { d.on.pull_request.paths = ['cloud_oam/frontend/**'] }],
  ['push ignore filter skips validation', (d) => { d.on.push['paths-ignore'] = ['**'] }],
  ['write token requested', (d) => { d.permissions.contents = 'write' }],
  ['extra permission requested', (d) => { d.permissions['id-token'] = 'write' }],
  ['concurrency mixed with database gate', (d) => { d.concurrency.group = 'postgresql16-release-gate-${{ github.ref }}' }],
  ['stale run cancellation disabled', (d) => { d.concurrency['cancel-in-progress'] = false }],
  ['production environment selected', (d, job) => { job.environment = 'production' }],
  ['shared runner selected', (d, job) => { job['runs-on'] = 'self-hosted' }],
  ['unbounded runner timeout', (d, job) => { delete job['timeout-minutes'] }],
  ['job failure ignored', (d, job) => { job['continue-on-error'] = true }],
  ['job skipped', (d, job) => { job.if = 'false' }],
  ['shell changed to hide pipe failures', (d, job) => { job.defaults.run.shell = 'bash {0}' }],
  ['job token permission widened', (d, job) => { job.permissions = 'write-all' }],
  ['job secret exposed', (d, job) => { job.env = { TOKEN: '${{ secrets.PRODUCTION_TOKEN }}' } }],
  ['checkout switched to moving tag', (d, job) => { job.steps[0].uses = 'actions/checkout@v7' }],
  ['checkout persisted credentials', (d, job) => { job.steps[0].with['persist-credentials'] = true }],
  ['checkout overridden to other repository', (d, job) => { job.steps[0].with.repository = 'other/source' }],
  ['setup-node switched to moving tag', (d, job) => { job.steps[1].uses = 'actions/setup-node@v6' }],
  ['Node version floats', (d, job) => { job.steps[1].with['node-version'] = '24.x' }],
  ['automatic cache enabled', (d, job) => { job.steps[1].with['package-manager-cache'] = true }],
  ['explicit cache enabled', (d, job) => { job.steps[1].with.cache = 'pnpm' }],
  ['pnpm version floats', (d, job) => { job.steps[2].run = job.steps[2].run.replace('pnpm@11.19.0', 'pnpm@latest') }],
  ['toolchain install lifecycle enabled', (d, job) => { job.steps[2].run = job.steps[2].run.replace('--ignore-scripts ', '') }],
  ['actual Node version not checked', (d, job) => { job.steps[2].run = job.steps[2].run.split('\n').slice(1).join('\n') }],
  ['actual pnpm version not checked', (d, job) => { job.steps[2].run = job.steps[2].run.split('\n').slice(0, 2).join('\n') }],
  ['repository safety skipped', (d, job) => { job.steps[3].run = 'true' }],
  ['dependency lock not frozen', (d, job) => { job.steps[4].run = 'pnpm install --ignore-scripts' }],
  ['dependency lifecycle enabled', (d, job) => { job.steps[4].run = 'pnpm install --frozen-lockfile' }],
  ['Web suite narrowed', (d, job) => { job.steps[5].run = 'pnpm test -- src/one.test.ts' }],
  ['Web failure suppressed', (d, job) => { job.steps[5].run += ' || true' }],
  ['type check skipped', (d, job) => { job.steps[6].run = 'true' }],
  ['build replaced by deployment', (d, job) => { job.steps[7].run = 'pnpm deploy' }],
  ['mini-program suite narrowed', (d, job) => { job.steps[8].run = 'node --test tests/client-release-gate-contract.test.js' }],
  ['mini-program scope changed', (d, job) => { job.steps[8]['working-directory'] = '.' }],
  ['step secret exposed', (d, job) => { job.steps[8].env = { TOKEN: '${{ secrets.PRODUCTION_TOKEN }}' } }],
  ['step label interpolates secrets', (d, job) => { job.steps[8].name = '${{ secrets.PRODUCTION_TOKEN }}' }],
  ['step failure ignored', (d, job) => { job.steps[8]['continue-on-error'] = true }],
  ['step skipped', (d, job) => { job.steps[8].if = 'false' }],
  ['gate step removed', (d, job) => { job.steps.pop() }],
  ['unreviewed upload step added', (d, job) => { job.steps.push({ name: 'Upload', uses: 'actions/upload-artifact@v4' }) }],
  ['unreviewed external command added', (d, job) => { job.steps.push({ name: 'External write', run: 'curl -X POST https://example.invalid' }) }],
  ['extra deploy job added', (d) => { d.jobs.deploy = { 'runs-on': 'ubuntu-24.04', steps: [] } }]
]

for (const [name, mutate] of mutations) {
  test(`client gate rejects ${name}`, () => {
    const document = parseWorkflow(workflowSource)
    mutate(document, document.jobs['client-release-gate'])
    assert.throws(() => validateWorkflow(document))
  })
}

for (const [name, source] of [
  ['broken JSON', '{'],
  ['general YAML outside the deliberately restricted syntax', 'name: Client release gate'],
  ['duplicate root mapping key', '{"permissions": {}, "permissions": {"contents": "write"}}'],
  ['escaped duplicate mapping key', '{"on": {}, "\\u006fn": {}}'],
  ['duplicate nested step key', '{"steps":[{"run":"true","run":"false"}]}']
]) {
  test(`client gate parser rejects ${name}`, () => {
    assert.throws(() => parseWorkflow(source))
  })
}

test('parser treats punctuation in strings and separate mapping scopes as data', () => {
  const source = '{"steps":[{"run":"say \\\"a:b\\\""},{"run":"{[]}"}]}'
  assert.deepEqual(parseWorkflow(source), JSON.parse(source))
})

test('repository ignore boundary allows only the two reviewed workflow filenames', () => {
  const visible = [workflowPath, '.github/workflows/postgresql16-release-gate.yml']
  const ignored = [
    '.github/workflows/unreviewed.yml',
    '.github/workflows/client-release-gate.yaml',
    '.github/workflows/client-release-gate.yml.backup',
    '.github/actions/unreviewed/action.yml',
    'work/session.json'
  ]
  for (const file of [...visible, ...ignored]) {
    const result = spawnSync('git', ['check-ignore', '--no-index', '--quiet', '--', file], {
      cwd: repositoryRoot,
      encoding: 'utf8'
    })
    assert.equal(result.error, undefined)
    assert.equal(result.status, visible.includes(file) ? 1 : 0, `incorrect ignore scope: ${file}`)
  }
})

function runSafetyOnDisposableRepository(candidate) {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'rsc-client-gate-contract-'))
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')))
  env.GIT_CONFIG_NOSYSTEM = '1'
  env.GIT_CONFIG_GLOBAL = '/dev/null'
  const run = (command, args) => spawnSync(command, args, {
    cwd: fixtureRoot, env, encoding: 'utf8'
  })
  try {
    const baseline = 'docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md'
    // Synthetic file bodies only; never copy production sessions or data.
    for (const file of ['README.md', 'AGENTS.md', baseline, 'cloud_oam/README.md', candidate]) {
      fs.mkdirSync(path.dirname(path.join(fixtureRoot, file)), { recursive: true })
      fs.writeFileSync(path.join(fixtureRoot, file), '# disposable repository contract fixture\n')
    }
    for (const file of ['.gitignore', 'cloud_oam/.gitignore', 'cloud_oam/scripts/verify_repository_safety.sh']) {
      fs.mkdirSync(path.dirname(path.join(fixtureRoot, file)), { recursive: true })
      fs.copyFileSync(path.join(repositoryRoot, file), path.join(fixtureRoot, file))
    }
    const initialized = run('git', ['init', '--quiet', '--template='])
    assert.equal(initialized.status, 0)
    // Force only the explicit synthetic candidate so the scanner, not merely
    // .gitignore, must independently enforce its exact file allowlist.
    const added = run('git', ['add', '--force', '--', candidate])
    assert.equal(added.status, 0)
    return run('bash', ['cloud_oam/scripts/verify_repository_safety.sh'])
  } finally {
    fs.rmSync(fixtureRoot, { recursive: true, force: true })
  }
}

for (const [candidate, allowed] of [
  [workflowPath, true],
  ['.github/workflows/postgresql16-release-gate.yml', true],
  ['.github/workflows/unreviewed.yml', false],
  ['.github/workflows/client-release-gate.yml.backup', false]
]) {
  test(`repository safety independently ${allowed ? 'allows' : 'rejects'} ${candidate}`, () => {
    const result = runSafetyOnDisposableRepository(candidate)
    assert.equal(result.error, undefined)
    assert.equal(result.status, allowed ? 0 : 1)
    if (allowed) assert.match(result.stdout, /repository-safety: PASS/)
    else assert.ok(result.stderr.includes(`path escapes the explicit repository allowlist: ${candidate}`))
  })
}
