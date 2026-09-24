import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { validatePublicCatalog } from './public-catalog.mjs';

// The pilot profile is deliberately narrower than the public production gate:
// the public knowledge catalog may remain pending, while the private /xx build
// must still be a complete, self-contained artifact. The full release gate
// remains the default Docker profile and still requires a reviewed catalog.
const publicDir = new URL('../dist/', import.meta.url);
const warehouseDir = new URL('../dist-warehouse/', import.meta.url);
const sourceCatalog = readFileSync(new URL('../src/knowledge-catalog.json', import.meta.url));
const builtCatalog = readFileSync(new URL('../dist/knowledge-catalog.json', import.meta.url));
assert.ok(sourceCatalog.equals(builtCatalog), 'PUBLIC_CATALOG_BUILD_STALE: rebuild after changing the catalog');
const catalog = validatePublicCatalog(JSON.parse(sourceCatalog), { requireReady: false });

const warehouseIndex = readFileSync(new URL('index.html', warehouseDir), 'utf8');
assert.match(warehouseIndex, /<title>RSC个人仓<\/title>/, 'pilot warehouse title missing');
assert.match(warehouseIndex, /\/xx\/manifest\.webmanifest/, 'pilot manifest must stay under /xx/');
assert.ok(existsSync(new URL('assets', warehouseDir)), 'pilot warehouse assets missing');
assert.ok(readdirSync(new URL('assets', warehouseDir)).length > 0, 'pilot warehouse assets empty');
assert.ok(existsSync(new URL('index.html', publicDir)), 'public shell missing');
console.log(JSON.stringify({
  check: 'pilot-release',
  catalog,
  catalogSha256: createHash('sha256').update(builtCatalog).digest('hex'),
  privatePath: '/xx/',
  publicCatalogIsReady: catalog.status === 'ready',
  scope: 'private pilot artifact only; production catalog, authentication and external acceptance remain separate',
}));
