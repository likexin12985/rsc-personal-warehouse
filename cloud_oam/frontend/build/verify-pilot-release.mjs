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
const scopeSource = readFileSync(new URL('../src/trialMvpScope.ts', import.meta.url), 'utf8');
const requestPageSource = readFileSync(new URL('../src/pages/FormalMaterialRequests.tsx', import.meta.url), 'utf8');
assert.ok(requestPageSource.includes('import { TRIAL_MVP_UI_SCOPE } from "../trialMvpScope";'), 'PILOT_UI_SCOPE_NOT_BOUND_TO_REQUEST_PAGE');
assert.match(
  requestPageSource,
  /const canOperateFulfillment = access\?\.can_read === true\s*\n?\s*&& access\.can_read_allocation_options === true;/,
  'PILOT_FULFILLMENT_CAPABILITY_NOT_EXPLICIT',
);
// Every operator-only panel must stay behind the same capability boundary.
// The personal receipt/inbound panel is intentionally excluded: it is the
// technician-facing part of the frozen pilot MVP.
for (const panel of [
  'FormalMaterialRequestSupplyPanel',
  'FormalMaterialRequestReservationPanel',
  'FormalMaterialRequestOutboundPanel',
  'FormalMaterialRequestShipmentPanel',
  'FormalMaterialRequestReceiptPanel',
  'FormalMaterialRequestInboundPanel',
  'FormalMaterialRequestPickPanel',
  'FormalMaterialRequestFulfillmentPreparationPanel',
]) {
  const renderLines = requestPageSource
    .split('\n')
    .filter((line) => line.includes(`<${panel}`));
  assert.ok(renderLines.length > 0, `PILOT_PANEL_MISSING: ${panel}`);
  assert.ok(
    renderLines.every((line) => line.includes('canOperateFulfillment')),
    `PILOT_PANEL_NOT_CAPABILITY_GATED: ${panel}`,
  );
}
for (const flag of [
  'allowSupplyPlanning: false',
  'allowLogisticsEvents: false',
  'showOamReceipt: false',
  'showReturnOperations: false',
  'showReleaseOperations: false',
  'showComplexLifecycle: false',
]) {
  assert.match(scopeSource, new RegExp(flag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), `PILOT_UI_SCOPE_OPEN: ${flag}`);
  assert.ok(requestPageSource.includes(`TRIAL_MVP_UI_SCOPE.${flag.split(':')[0]}`), `PILOT_UI_SCOPE_UNUSED: ${flag}`);
}
assert.match(scopeSource, /Object\.freeze\(/, 'PILOT_UI_SCOPE_NOT_FROZEN');
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
