import { describe, it, expect } from 'vitest';
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { SOURCE_URL, validatePublicCatalog } from './public-catalog.mjs';

const pending = () => ({ schemaVersion: 1, status: 'pending', sourceUrl: SOURCE_URL,
  verifiedAt: null, sourceSha256: null, items: [] });
const ready = () => ({ ...pending(), status: 'ready', verifiedAt: '2000-01-01T00:00:00+08:00',
  sourceSha256: 'a'.repeat(64), items: [{ code: 'TEST001', name: '测试夹具', category: '测试分类',
    model: '', note: '仅测试数据', sourceSheet: '测试页', sourceRow: 2 }] });

describe('public catalog release boundary', () => {
  it('allows an honest pending development preview and blocks its release', () => {
    expect(validatePublicCatalog(pending())).toEqual({ status: 'pending', records: 0 });
    expect(() => validatePublicCatalog(pending(), { requireReady: true })).toThrow(/PUBLIC_CATALOG_NOT_READY/);
  });
  it('accepts reviewed public fields with exact provenance', () => {
    expect(validatePublicCatalog(ready(), { requireReady: true })).toEqual({ status: 'ready', records: 1 });
  });
  it('preserves distinct exact worksheet titles including meaningful edge spaces', () => {
    const catalog = ready();
    catalog.items.push({ ...catalog.items[0], sourceSheet: ' 测试页 ' });
    expect(validatePublicCatalog(catalog, { requireReady: true }).records).toBe(2);
    expect(catalog.items[1].sourceSheet).toBe(' 测试页 ');
  });
  it.each([
    ['unknown fields', c => { c.accessToken = 'test-only'; }],
    ['wrong source', c => { c.sourceUrl = 'https://example.com'; }],
    ['missing export digest', c => { c.sourceSha256 = null; }],
    ['empty ready data', c => { c.items = []; }],
    ['undated data', c => { c.verifiedAt = '2000-01-01'; }],
    ['invalid calendar date', c => { c.verifiedAt = '2001-02-29T00:00:00Z'; }],
    ['future verification', c => { c.verifiedAt = '2999-01-01T00:00:00Z'; }],
    ['invalid offset', c => { c.verifiedAt = '2000-01-01T00:00:00+24:00'; }],
    ['unknown row fields', c => { c.items[0].phone = 'test-only'; }],
    ['missing source sheet', c => { c.items[0].sourceSheet = ''; }],
    ['whitespace source sheet', c => { c.items[0].sourceSheet = '  '; }],
    ['boolean row', c => { c.items[0].sourceRow = true; }],
    ['noninteger row', c => { c.items[0].sourceRow = 1.5; }],
    ['unsafe code', c => { c.items[0].code = '=1+1'; }],
    ['unnormalized fields', c => { c.items[0].code = ' TEST001'; }],
    ['control characters', c => { c.items[0].note = 'test\rhidden'; }],
    ['oversized field', c => { c.items[0].note = '😀'.repeat(3001); }],
    ['duplicate source rows', c => { c.items.push({ ...c.items[0] }); }],
  ])('rejects %s before packaging', (_, change) => {
    const catalog = ready();
    change(catalog);
    expect(() => validatePublicCatalog(catalog, { requireReady: true })).toThrow();
  });
  it('retains the same code in different source locations and counts Unicode characters consistently', () => {
    const catalog = ready();
    catalog.items[0].note = '😀'.repeat(3000);
    catalog.items.push({ ...catalog.items[0], sourceRow: 3 });
    expect(validatePublicCatalog(catalog, { requireReady: true }).records).toBe(2);
  });
});

function runRelease(source, built) {
  const root = mkdtempSync(path.join(tmpdir(), 'rsc-catalog-release-test-'));
  try {
    for (const dir of ['src', 'dist', 'build']) mkdirSync(path.join(root, dir));
    for (const name of ['public-catalog.mjs', 'verify-release.mjs']) {
      copyFileSync(new URL(name, import.meta.url), path.join(root, 'build', name));
    }
    writeFileSync(path.join(root, 'src/knowledge-catalog.json'), JSON.stringify(source));
    writeFileSync(path.join(root, 'dist/knowledge-catalog.json'), JSON.stringify(built));
    return spawnSync(process.execPath, [path.join(root, 'build/verify-release.mjs')], { encoding: 'utf8', timeout: 10000 });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

it('the packaging command refuses a pending catalogue even when its build matches', () => {
  const result = runRelease(pending(), pending());
  expect(result.status).toBe(1);
  expect(result.stderr).toContain('PUBLIC_CATALOG_NOT_READY');
});
it('the packaging command refuses an old build after a new catalogue import', () => {
  const source = ready();
  source.items[0].name = '新测试夹具';
  const result = runRelease(source, ready());
  expect(result.status).toBe(1);
  expect(result.stderr).toContain('PUBLIC_CATALOG_BUILD_STALE');
});
it('the packaging command reports the digest of matching ready artifacts', () => {
  const result = runRelease(ready(), ready());
  expect(result.status).toBe(0);
  const evidence = JSON.parse(result.stdout);
  expect(evidence.records).toBe(1);
  expect(evidence.catalogSha256).toMatch(/^[a-f0-9]{64}$/);
});
