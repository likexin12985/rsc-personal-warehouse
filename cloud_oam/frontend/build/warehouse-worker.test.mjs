import { it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../warehouse-public/sw.js', import.meta.url), 'utf8');
function worker({ online, cached = {}, cacheNames = [] } = {}) {
  const handlers = {};
  const removed = [];
  const opened = [];
  const cache = { match: async request => cached[typeof request === 'string' ? request : request.url] };
  vm.runInNewContext(source, {
    URL,
    self: { location: { origin: 'https://rscwz.cn' },
      addEventListener: (name, fn) => { handlers[name] = fn; },
      clients: { claim() {} } },
    caches: { open: async name => { opened.push(name); return cache; },
      keys: async () => cacheNames, delete: async key => { removed.push(key); } },
    fetch: async () => { if (online) return online; throw new Error('offline'); },
  });
  return {
    request(url, mode = 'cors', method = 'GET') {
      let result;
      handlers.fetch({ request: { url, mode, method }, respondWith: promise => { result = promise; } });
      return result;
    },
    activate() { let result; handlers.activate({ waitUntil: p => { result = p; } }); return result; },
    removed, opened,
  };
}

it.each([
  ['https://rscwz.cn/api/auth/me', 'cors', 'GET'],
  ['https://rscwz.cn/', 'navigate', 'GET'],
  ['https://rscwz.cn/xx/api/auth/me', 'cors', 'GET'],
  ['https://example.com/xx/page', 'cors', 'GET'],
  ['https://rscwz.cn/xx/login', 'cors', 'POST'],
])('leaves requests outside its private shell untouched: %s', (url, mode, method) => {
  expect(worker().request(url, mode, method)).toBeUndefined();
});
it('returns server errors without silently replacing them with cached HTML', async () => {
  const response = { status: 404 };
  expect(await worker({ online: response, cached: { '/xx/': 'html' } })
    .request('https://rscwz.cn/xx/assets/missing.js')).toBe(response);
});
it('only uses the private shell fallback for an offline document navigation', async () => {
  const instance = worker({ cached: { '/xx/': 'private html' } });
  expect(await instance.request('https://rscwz.cn/xx/my-stock', 'navigate')).toBe('private html');
  expect(instance.opened).toEqual(['rsc-personal-warehouse-private-shell-v4']);
  await expect(instance.request('https://rscwz.cn/xx/assets/missing.js')).rejects.toThrow('offline');
});
it('serves only an exact resource match from the current private cache', async () => {
  const url = 'https://rscwz.cn/xx/assets/app.js';
  expect(await worker({ cached: { [url]: 'javascript', '/xx/': 'html' } }).request(url)).toBe('javascript');
});
it('removes prior private shells without deleting caches owned by another app', async () => {
  const instance = worker({ cacheNames: ['rsc-personal-warehouse-private-shell-v3',
    'rsc-personal-warehouse-private-shell-v4', 'another-app'] });
  await instance.activate();
  expect(instance.removed).toEqual(['rsc-personal-warehouse-private-shell-v3']);
});
