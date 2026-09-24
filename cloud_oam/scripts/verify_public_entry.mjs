import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { validatePublicCatalog } from '../frontend/build/public-catalog.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const publicDir = path.join(root, 'frontend/dist');
const warehouseDir = path.join(root, 'frontend/dist-warehouse');
const read = (file) => readFileSync(file, 'utf8');
assert.ok(process.argv.slice(2).every((argument) => argument === '--release'), 'only --release is supported');
const requireReady = process.argv.includes('--release');
const publicHtml = read(path.join(publicDir, 'index.html'));
const warehouseHtml = read(path.join(warehouseDir, 'index.html'));
assert.match(publicHtml, /<title>交流备件知识大全<\/title>/);
assert.doesNotMatch(publicHtml, /warehouse-main|\/xx\/assets|RSC个人仓/);
assert.match(warehouseHtml, /<title>RSC个人仓<\/title>/);
assert.match(warehouseHtml, /src="\/xx\/assets\//);
assert.match(warehouseHtml, /href="\/xx\/assets\//);

const scripts = readdirSync(path.join(publicDir, 'assets')).filter((name) => name.endsWith('.js'));
const publicCode = scripts.map((name) => read(path.join(publicDir, 'assets', name))).join('\n');
assert.match(publicCode, /资料待更新/);
assert.match(publicCode, /豫ICP备2026043964号-1/);
assert.match(publicCode, /https:\/\/beian\.miit\.gov\.cn\//);
assert.doesNotMatch(publicCode, /验证码登录|\/auth\/me|\/auth\/refresh|\/auth\/sms|cloud-oam-auth-refresh|inventory-transactions/);

const mini = JSON.parse(read(path.join(root, 'miniprogram/app.json')));
assert.deepEqual(mini.pages, ['pages/knowledge/index']);
assert.equal(mini.tabBar, undefined);
const catalog = read(path.join(root, 'frontend/src/knowledge-catalog.json'));
const catalogResult = validatePublicCatalog(JSON.parse(catalog), { requireReady });
assert.equal(catalog, read(path.join(publicDir, 'knowledge-catalog.json')), 'public build has a stale catalog');
assert.equal(catalog, read(path.join(root, 'miniprogram/data/knowledge-catalog.json')));
const manifest = JSON.parse(read(path.join(warehouseDir, 'manifest.webmanifest')));
assert.equal(manifest.scope, '/xx/');
assert.equal(manifest.start_url, '/xx/');
assert.match(read(path.join(publicDir, 'sw.js')), /rsc-personal-warehouse-shell-/);
assert.doesNotMatch(read(path.join(publicDir, 'sw.js')), /addEventListener\("fetch"/);
console.log(JSON.stringify({ check: 'public-entry-artifacts', mode: requireReady ? 'release' : 'development',
  catalog: catalogResult, artifacts: 'public bundle has no auth client; /xx assets, worker scope and mini catalog match',
  scope: 'static artifacts only; source review, real login, device validation and production gates remain separate' }));
