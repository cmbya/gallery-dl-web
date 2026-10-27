/* eslint-disable @typescript-eslint/no-require-imports -- Node built-in test with TypeScript transpilation. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, filename);
process.env.BACKEND_URL = 'http://backend:8000';
const route = require('../src/app/api/[...path]/route.ts');

test('subscription edits traverse the actual PATCH proxy and preserve payload and upstream errors', async () => {
  assert.equal(typeof route.PATCH, 'function');
  const original = global.fetch;
  let sent;
  global.fetch = async (url, options) => { sent = { url, options }; return Response.json({ refresh_limit: 7, history_limit: 3 }); };
  try {
    const payload = { username: 'example', enabled: true, refresh_enabled: true, refresh_limit: 7, history_enabled: true, history_limit: 3 };
    const request = new Request('http://frontend/api/automation/subscriptions/test-id', { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await route.PATCH(request, { params: Promise.resolve({ path: ['automation', 'subscriptions', 'test-id'] }) });
    assert.equal(sent.url, 'http://backend:8000/api/automation/subscriptions/test-id');
    assert.equal(sent.options.method, 'PATCH');
    assert.deepEqual(JSON.parse(Buffer.from(sent.options.body).toString()), payload);
    assert.deepEqual(await result.json(), { refresh_limit: 7, history_limit: 3 });
    global.fetch = async () => Response.json({ detail: 'limit invalid' }, { status: 422 });
    const invalid = new Request('http://frontend/api/automation/subscriptions/test-id', { method: 'PATCH', body: '{}' });
    const failed = await route.PATCH(invalid, { params: Promise.resolve({ path: ['automation', 'subscriptions', 'test-id'] }) });
    assert.equal(failed.status, 422);
    assert.equal((await failed.json()).detail, 'limit invalid');
  } finally { global.fetch = original; }
});
