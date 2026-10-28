/* eslint-disable @typescript-eslint/no-require-imports -- Node built-in test with TypeScript transpilation. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, filename);
process.env.BACKEND_URL = 'http://backend:8000';
const route = require('../src/app/api/[...path]/route.ts');

test('media proxy preserves ranges, validators, inline metadata and streaming status', async () => {
  const original = global.fetch;
  let sent;
  global.fetch = async (url, options) => {
    sent = { url, options };
    return new Response('2345', { status: 206, headers: { 'content-type': 'video/mp4', 'content-range': 'bytes 2-5/10', 'accept-ranges': 'bytes', 'content-length': '4', 'content-disposition': 'inline; filename="clip.mp4"', etag: '"test"', 'last-modified': 'Wed, 28 Oct 2026 00:00:00 GMT', 'x-content-type-options': 'nosniff' } });
  };
  try {
    const request = new Request('http://frontend/api/profiles/facebook/demo/preview/nested/clip.mp4', { headers: { Range: 'bytes=2-5', 'If-Range': '"test"' } });
    const result = await route.GET(request, { params: Promise.resolve({ path: ['profiles', 'facebook', 'demo', 'preview', 'nested', 'clip.mp4'] }) });
    assert.equal(sent.options.headers.range, 'bytes=2-5');
    assert.equal(sent.options.headers['if-range'], '"test"');
    assert.equal(sent.options.signal, request.signal);
    assert.equal(result.status, 206);
    for (const [name, expected] of Object.entries({ 'content-range': 'bytes 2-5/10', 'accept-ranges': 'bytes', 'content-length': '4', etag: '"test"', 'last-modified': 'Wed, 28 Oct 2026 00:00:00 GMT', 'x-content-type-options': 'nosniff' })) assert.equal(result.headers.get(name), expected);
    assert.match(result.headers.get('content-disposition'), /^inline;/);
    assert.equal(await result.text(), '2345');
    global.fetch = async () => new Response(null, { status: 416, headers: { 'content-range': 'bytes */10' } });
    const invalid = await route.GET(request, { params: Promise.resolve({ path: ['profiles', 'facebook', 'demo', 'preview', 'clip.mp4'] }) });
    assert.equal(invalid.status, 416); assert.equal(invalid.headers.get('content-range'), 'bytes */10');
    assert.equal(typeof route.HEAD, 'function');
    global.fetch = async (_url, options) => { assert.equal(options.method, 'HEAD'); return new Response(null, { headers: { 'content-length': '10' } }); };
    const head = await route.HEAD(new Request(request.url, { method: 'HEAD' }), { params: Promise.resolve({ path: ['profiles', 'facebook', 'demo', 'preview', 'clip.mp4'] }) });
    assert.equal(head.headers.get('content-length'), '10'); assert.equal(await head.text(), '');
  } finally { global.fetch = original; }
});

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
