import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { validatePublicCatalog } from './public-catalog.mjs';

const source = readFileSync(new URL('../src/knowledge-catalog.json', import.meta.url));
const built = readFileSync(new URL('../dist/knowledge-catalog.json', import.meta.url));
assert.ok(source.equals(built), 'PUBLIC_CATALOG_BUILD_STALE: rebuild after changing the catalog');
const result = validatePublicCatalog(JSON.parse(source), { requireReady: true });
console.log(JSON.stringify({ check: 'public-catalog-release', ...result,
  catalogSha256: createHash('sha256').update(built).digest('hex'),
  scope: 'catalog only; deployment, authentication and source review remain separate' }));
