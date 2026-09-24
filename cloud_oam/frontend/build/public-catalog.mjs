import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

export const SOURCE_URL = 'https://wbenergy.feishu.cn/sheets/ZVnis9SUzhfiU9t9EK4csLvEnNg?sheet=1eOLBU';
const LIMITS = { code: 80, name: 500, category: 100, model: 1000, note: 3000, sourceSheet: 100 };

function keys(value, expected) {
  assert.ok(value && typeof value === 'object' && !Array.isArray(value), 'catalog object required');
  assert.deepEqual(Object.keys(value).sort(), [...expected].sort(), 'unexpected public catalog fields');
}

export function validatePublicCatalog(catalog, { requireReady = false, now = Date.now() } = {}) {
  keys(catalog, ['schemaVersion', 'status', 'sourceUrl', 'verifiedAt', 'sourceSha256', 'items']);
  assert.equal(catalog.schemaVersion, 1, 'unsupported catalog version');
  assert.equal(catalog.sourceUrl, SOURCE_URL, 'unreviewed catalog source');
  assert.ok(Array.isArray(catalog.items), 'catalog items must be an array');
  if (catalog.status === 'pending') {
    assert.equal(catalog.verifiedAt, null);
    assert.equal(catalog.sourceSha256, null);
    assert.equal(catalog.items.length, 0);
    assert.equal(requireReady, false, 'PUBLIC_CATALOG_NOT_READY: import and verify the source before release');
    return { status: 'pending', records: 0 };
  }
  assert.equal(catalog.status, 'ready', 'unknown catalog status');
  assert.match(catalog.sourceSha256, /^[a-f0-9]{64}$/, 'missing export digest');
  assert.equal(typeof catalog.verifiedAt, 'string');
  assert.match(catalog.verifiedAt, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/);
  const [year, month, day, hour, minute, second] = catalog.verifiedAt.slice(0, 19).split(/[-T:]/).map(Number);
  assert.ok(year > 0 && month >= 1 && month <= 12 && day >= 1
    && day <= new Date(Date.UTC(year, month, 0)).getUTCDate()
    && hour < 24 && minute < 60 && second < 60, 'invalid calendar date');
  const verified = Date.parse(catalog.verifiedAt);
  assert.ok(Number.isFinite(verified) && verified <= now, 'invalid or future verification time');
  assert.ok(catalog.items.length > 0 && catalog.items.length <= 20000, 'invalid catalog record count');
  const seen = new Set();
  for (const row of catalog.items) {
    keys(row, [...Object.keys(LIMITS), 'sourceRow']);
    for (const [name, limit] of Object.entries(LIMITS)) {
      assert.equal(typeof row[name], 'string', `invalid ${name}`);
      assert.ok([...row[name]].length <= limit && !/[\x00-\x08\x0b-\x1f]/.test(row[name]), `invalid ${name}`);
      if (name !== 'sourceSheet') assert.equal(row[name], row[name].trim(), `unnormalized ${name}`);
    }
    for (const name of ['code', 'name', 'category', 'sourceSheet']) assert.ok(row[name].trim(), `missing ${name}`);
    assert.match(row.code, /^[A-Za-z0-9][A-Za-z0-9._/\-]{0,79}$/);
    assert.ok(Number.isSafeInteger(row.sourceRow) && row.sourceRow > 0 && row.sourceRow <= 1000000, 'invalid source row');
    const key = JSON.stringify([row.sourceSheet, row.sourceRow, row.code]);
    assert.ok(!seen.has(key), 'duplicate source record');
    seen.add(key);
  }
  return { status: 'ready', records: catalog.items.length };
}

export function publicCatalogPlugin(enabled) {
  const file = new URL('../src/knowledge-catalog.json', import.meta.url);
  const source = enabled ? readFileSync(file, 'utf8') : null;
  return {
    name: 'public-catalog-artifact',
    generateBundle() {
      if (!enabled) return;
      assert.equal(readFileSync(file, 'utf8'), source, 'Catalog changed during build; rebuild from stable source');
      validatePublicCatalog(JSON.parse(source));
      this.emitFile({ type: 'asset', fileName: 'knowledge-catalog.json', source });
    },
  };
}
