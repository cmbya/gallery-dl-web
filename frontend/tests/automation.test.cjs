/* eslint-disable @typescript-eslint/no-require-imports -- Node built-in runner with a local TypeScript transpilation hook; no test dependency required. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  return originalResolve.call(this, request.startsWith('@/') ? path.join(root, 'src', request.slice(2)) : request, ...args);
};
for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText, filename);
}
// The NAS shell exports NODE_ENV=production; React act requires its test/dev build.
process.env.NODE_ENV = 'test';
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { normalizeUsername, subscriptionInput, statusLabel, isActiveRun } = require('../src/lib/automation.ts');
const api = require('../src/lib/api.ts');
const render = (name, props) => renderToStaticMarkup(React.createElement(require(`../src/components/${name}.tsx`)[name], props));
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
global.window = dom.window; global.document = dom.window.document;
global.IS_REACT_ACT_ENVIRONMENT = true;
global.requestAnimationFrame = (callback) => setTimeout(callback, 0);
const { createRoot } = require('react-dom/client');
const { act } = React;
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function mount(name, props) {
  const container = document.createElement('div'); document.body.append(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(require(`../src/components/${name}.tsx`)[name], props)); await tick(); });
  return { container, close: async () => { await act(async () => root.unmount()); container.remove(); } };
}
function submit(container) { container.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); }
const input = { username: '@Example', enabled: true, refresh_enabled: true, refresh_limit: 20, history_enabled: true, history_limit: 100 };

test('redirect reasons and sanitized diagnostic render in results and cookie checks', () => {
  const { reasonLabel } = require('../src/lib/automation.ts');
  for (const reason of ['redirect', 'redirect_home', 'redirect_login', 'redirect_challenge', 'redirect_external', 'redirect_insecure', 'redirect_invalid', 'redirect_missing_location', 'redirect_unsupported', 'redirect_loop', 'redirect_limit', 'query_error', 'scan_budget']) assert.ok(!reasonLabel(reason).includes('未识别'));
  const diagnostic = { stage: 'graphql', status: 302, target: 'login', classification: 'redirect_login' };
  for (const html of [
    render('AutomationResult', { result: { status: 'stopped', checked: 0, completed: 0, skipped: 0, failed: 0, reason: 'redirect_login', diagnostic } }),
    render('CookieCheckPanel', { check: { login_status: 'unknown', posts_status: 'unknown', reason: 'redirect_login', diagnostic }, onChecked: async () => {} }),
  ]) {
    assert.match(html, /帖子查询接口/); assert.match(html, /HTTP 302/); assert.match(html, /登录页/); assert.match(html, /尚不能确认/);
  }
  assert.ok(!render('AutomationDiagnostic', { diagnostic: { stage: 'SECRET_URL', target: 'SECRET_COOKIE', status: 9999 } }).includes('SECRET'));
});

test('cookie panel explains automatic enabled target and shows actual tested account', () => {
  const html = render('CookieCheckPanel', { check: { login_status: 'unknown', posts_status: 'accessible', tested_username: 'alice' }, onChecked: async () => {} });
  assert.match(html, /测试目标：@alice/);
  assert.match(html, /已启用订阅/);
  assert.match(html, /无已启用订阅时不测试帖子/);
  const untested = render('CookieCheckPanel', { check: { login_status: 'authenticated', posts_status: 'not_tested', tested_username: null }, onChecked: async () => {} });
  assert.match(untested, /未测试/);
});

test('normalizes usernames and profile URLs, rejects post URLs and foreign origins', () => {
  assert.equal(normalizeUsername(' https://www.instagram.com/Example/?hl=en '), 'example');
  assert.equal(normalizeUsername('@Example'), 'example');
  assert.equal(normalizeUsername('WWW.INSTAGRAM.COM/Example/'), 'example');
  assert.throws(() => normalizeUsername('https://instagram.com:8080/example'));
  for (const value of ['https://evil.test/example', 'https://instagram.com/p/abc', 'https://instagram.com/accounts/', 'a/b', '', 'https://instagram.com.evil.test/name']) assert.throws(() => normalizeUsername(value));
});
test('independent simultaneous modes and positive integer limits', () => {
  assert.deepEqual(subscriptionInput(input), { ...input, username: 'example' });
  for (const value of [0, -1, NaN, 1.2]) assert.throws(() => subscriptionInput({ ...input, history_limit: value }));
  assert.equal(subscriptionInput({ ...input, enabled: false }).enabled, false);
});
test('form exposes named independent modes and keyboard-native controls', () => {
  const html = render('SubscriptionForm', { initial: input, busy: false, onSave: async () => true });
  assert.match(html, /<form/); assert.match(html, /Instagram 用户名或主页链接/);
  assert.equal((html.match(/type="checkbox"[^>]*checked=""/g) || []).length, 3);
  assert.match(html, /每次刷新帖子上限/); assert.match(html, /每次历史补全帖子上限/);
  assert.match(html, /type="submit"/);
});
test('result counters distinguish posts from files and escape reason text', () => {
  const html = render('AutomationResult', { result: { status: 'failed', checked: 8, completed: 3, skipped: 4, failed: 1, file_count: 12, reason: '<script>bad</script>' } });
  for (const label of ['已检查帖子', '本轮新增完成', '已跳过帖子', '失败帖子', '下载文件数（非帖子数）']) assert.ok(html.includes(label));
  assert.ok(!html.includes('<script>')); assert.match(html, /&lt;script&gt;/);
});
test('cookie panel separates login validity from restricted posts and is manual', () => {
  const html = render('CookieCheckPanel', { check: { login_status: 'valid', posts_status: 'restricted', checked_at: 1700000000 }, onChecked: async () => {} });
  assert.match(html, /登录状态/); assert.match(html, /有效/); assert.match(html, /帖子访问状态/); assert.match(html, /访问受限/);
  assert.match(html, /手动检查 Cookie/); assert.match(html, /（可选）/);
  assert.notEqual(statusLabel('restricted'), statusLabel('error'));
  assert.equal(isActiveRun('running'), true); assert.equal(isActiveRun('completed'), false);
});
test('automation API uses exact payloads, encoded ids, no cache and handles cooldown/204', async () => {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (url, init) => { calls.push({ url, ...init }); return new Response(JSON.stringify({ accepted: 1, skipped: 0, paused: false }), { status: 200 }); };
  try {
    await api.addSubscription(input); assert.deepEqual(JSON.parse(calls.at(-1).body), input);
    await api.updateSubscription('a/b', { enabled: false }); assert.equal(calls.at(-1).url, '/api/automation/subscriptions/a%2Fb'); assert.equal(calls.at(-1).method, 'PATCH');
    await api.triggerAutomation('refresh', '42'); assert.deepEqual(JSON.parse(calls.at(-1).body), { mode: 'refresh', subscription_id: '42' });
    await api.triggerAutomation('history'); assert.deepEqual(JSON.parse(calls.at(-1).body), { mode: 'history' });
    await api.checkAutomationCookies(); assert.deepEqual(JSON.parse(calls.at(-1).body), {});
    await api.redownloadPost('42', '123'); assert.deepEqual(JSON.parse(calls.at(-1).body), { post_id: '123' });
    await api.repositionHistory('a/b'); assert.equal(calls.at(-1).url, '/api/automation/subscriptions/a%2Fb/reposition'); assert.equal(calls.at(-1).method, 'POST'); assert.deepEqual(JSON.parse(calls.at(-1).body), {});
    await api.getAutomation(); assert.equal(calls.at(-1).cache, 'no-store');
    global.fetch = async () => new Response(null, { status: 204 }); await api.deleteSubscription('42');
    global.fetch = async () => new Response(JSON.stringify({ detail: '稍后重试' }), { status: 429 });
    await assert.rejects(api.checkAutomationCookies(), (e) => e instanceof api.ApiError && e.status === 429);
  } finally { global.fetch = original; }
});

test('edit form strips server fields and synchronously locks duplicate submissions', async () => {
  let release; const pending = new Promise((resolve) => { release = resolve; });
  const calls = [];
  const ui = await mount('SubscriptionForm', { initial: { ...input, id: '42', history_status: 'ready', last_result: null }, busy: false, onCancel: () => {}, onSave: async (body) => { calls.push(body); return pending; } });
  try {
    await act(async () => { submit(ui.container); submit(ui.container); });
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0], { ...input, username: 'example' });
    assert.equal(ui.container.querySelector('button[type=submit]').disabled, true);
    await act(async () => { release(false); await tick(); });
    assert.equal(ui.container.querySelector('button[type=submit]').disabled, false);
    assert.equal(ui.container.querySelector('input').value, '@Example');
  } finally { await ui.close(); }
});

test('cookie check locks repeated Enter, reports refresh failures, and respects disabled prop', async () => {
  const original = global.fetch; let release; let calls = 0;
  global.fetch = async () => { calls++; return new Promise((resolve) => { release = () => resolve(new Response('{}')); }); };
  const ui = await mount('CookieCheckPanel', { check: null, onChecked: async () => false });
  try {
    await act(async () => { submit(ui.container); submit(ui.container); });
    assert.equal(calls, 1); assert.equal(ui.container.querySelector('button').disabled, true);
    await act(async () => { release(); await tick(); });
    assert.match(ui.container.textContent, /检查已完成，但状态刷新失败/);
    assert.doesNotMatch(ui.container.textContent, /检查完成，已更新状态/);
  } finally { await ui.close(); }
  const disabled = await mount('CookieCheckPanel', { check: null, disabled: true, onChecked: async () => {} });
  try { await act(async () => submit(disabled.container)); assert.equal(calls, 1); }
  finally { await disabled.close(); global.fetch = original; }
});

test('cookie cooldown exposes server Retry-After and unlocks after error', async () => {
  const original = global.fetch;
  global.fetch = async () => new Response(JSON.stringify({ detail: 'cooldown' }), { status: 429, headers: { 'Retry-After': '60' } });
  const ui = await mount('CookieCheckPanel', { check: null, onChecked: async () => { assert.fail('must not refresh after failed check'); } });
  try {
    await act(async () => { submit(ui.container); await tick(); });
    assert.match(ui.container.textContent, /至少等待 60 秒/);
    assert.equal(ui.container.querySelector('button').disabled, false);
  } finally { await ui.close(); global.fetch = original; }
});

test('dashboard retries failed initial read and preserves submission/refresh distinction', async () => {
  const original = global.fetch; let reads = 0; let writes = 0; let failRefresh = false; let release;
  const state = { subscriptions: [{ ...input, username: 'example', id: '42' }], runs: [], paused: false, pause_reason: null, cookie_check: null };
  global.fetch = async (url, init) => {
    if (init.method === 'GET') { reads++; if (reads === 1 || failRefresh) throw new Error('offline'); return new Response(JSON.stringify(state)); }
    assert.equal(url, '/api/automation/trigger'); writes++;
    return new Promise((resolve) => { release = () => resolve(new Response(JSON.stringify({ accepted: 1, skipped: 0, paused: false }))); });
  };
  const ui = await mount('SubscriptionDashboard');
  try {
    assert.match(ui.container.querySelector('[role=alert]').textContent, /状态读取失败/);
    await act(async () => { [...ui.container.querySelectorAll('button')].find((b) => b.textContent === '刷新状态').click(); await tick(); });
    assert.equal(ui.container.querySelector('[role=alert]'), null);
    const trigger = [...ui.container.querySelectorAll('button')].find((b) => b.textContent === '立即刷新全部');
    await act(async () => { trigger.click(); trigger.click(); });
    assert.equal(writes, 1); assert.equal(trigger.disabled, true);
    failRefresh = true;
    await act(async () => { release(); await tick(); });
    assert.match(ui.container.textContent, /已接受 1 项/);
    assert.match(ui.container.textContent, /操作已提交，但状态刷新失败/);
    assert.match(ui.container.textContent, /不要重复提交/);
    assert.equal(writes, 1);
  } finally { await ui.close(); global.fetch = original; }
});

test('history reposition requires confirmation, preserves archives, refreshes state and reports conflicts', async () => {
  const original = global.fetch; const confirm = window.confirm;
  let writes = 0; let reads = 0; let approved = false; let conflict = false;
  const state = { subscriptions: [{ ...input, username: 'example', id: '42', history_status: 'needs_reposition' }], runs: [], paused: false, pause_reason: null, cookie_check: null };
  global.fetch = async (url, init) => {
    if (init.method === 'GET') { reads++; return new Response(JSON.stringify(state)); }
    writes++; assert.equal(url, '/api/automation/subscriptions/42/reposition');
    assert.deepEqual(JSON.parse(init.body), {});
    if (conflict) return new Response(JSON.stringify({ detail: 'wait for Instagram jobs' }), { status: 409 });
    state.subscriptions[0].history_status = 'ready'; return new Response(JSON.stringify({ reset: true }));
  };
  window.confirm = (message) => {
    for (const text of ['从头', '扫描预算', '跳过已归档帖子', '不会清除', '文件']) assert.ok(message.includes(text));
    return approved;
  };
  const ui = await mount('SubscriptionDashboard');
  try {
    const button = ui.container.querySelector('[aria-label="重新定位 example 的历史进度"]');
    assert.ok(button);
    await act(async () => { button.click(); await tick(); }); assert.equal(writes, 0);
    approved = true;
    await act(async () => { button.click(); await tick(); });
    assert.equal(writes, 1); assert.equal(reads, 2);
    assert.match(ui.container.textContent, /等待补全/);
    assert.match(ui.container.textContent, /已重新定位/);
    conflict = true;
    await act(async () => { button.click(); await tick(); });
    assert.match(ui.container.querySelector('[role=alert]').textContent, /wait for Instagram jobs/);
    assert.equal(button.disabled, false);
  } finally { await ui.close(); global.fetch = original; window.confirm = confirm; }
});

test('archive progress uses reference totals without percentages or inferred completion', () => {
  const progress = { completed: 12, platform_total: 44, total_updated_at: 1700000000, history_complete: false };
  const html = render('ArchiveProgress', { progress });
  assert.match(html, /累计归档：12 \/ 约44帖/);
  assert.match(html, /平台参考总数/); assert.match(html, /Reels/);
  assert.doesNotMatch(html, /%|历史补全完成/);
  assert.match(render('ArchiveProgress', { progress: { ...progress, platform_total: null } }), /已归档12帖·总数未知/);
  assert.match(render('ArchiveProgress', { progress: { ...progress, platform_total: 0 } }), /12 \/ 约0帖/);
  assert.doesNotMatch(render('ArchiveProgress', { progress: { ...progress, platform_total: 1 } }), /历史补全完成/);
  const complete = render('ArchiveProgress', { progress: { ...progress, history_complete: true } });
  assert.match(complete, /历史补全完成·已归档12帖/);
  assert.match(complete, /当前可访问/); assert.match(complete, /不会自动重新扫描/);
});

test('completed history may be manually disabled without disabling refresh or deleting archives', async () => {
  const original = global.fetch; const writes = [];
  const subscription = { ...input, username: 'example', id: '42', progress: { completed: 12, platform_total: 44, total_updated_at: 1700000000, history_complete: true } };
  const state = { subscriptions: [subscription], runs: [], paused: false, pause_reason: null, cookie_check: null };
  global.fetch = async (url, init) => {
    if (init.method === 'GET') return new Response(JSON.stringify(state));
    writes.push({ url, ...init }); Object.assign(subscription, JSON.parse(init.body));
    return new Response(JSON.stringify(subscription));
  };
  const ui = await mount('SubscriptionDashboard');
  try {
    assert.equal(writes.length, 0);
    const button = [...ui.container.querySelectorAll('button')].find((b) => b.textContent === '关闭历史补全');
    assert.ok(button);
    await act(async () => { button.click(); await tick(); });
    assert.equal(writes.length, 1); assert.equal(writes[0].method, 'PATCH');
    assert.equal(writes[0].url, '/api/automation/subscriptions/42');
    assert.deepEqual(JSON.parse(writes[0].body), { history_enabled: false });
    assert.equal(subscription.refresh_enabled, true);
    assert.match(ui.container.textContent, /最新刷新：已启用/);
    assert.match(ui.container.textContent, /历史补全：已关闭/);
    assert.ok(![...ui.container.querySelectorAll('button')].some((b) => b.textContent === '关闭历史补全'));
  } finally { await ui.close(); global.fetch = original; }
});

test('global budget form saves independent modes, explains deferred posts, and displays batches', async () => {
  const original = global.fetch; const writes = [];
  const state = { subscriptions: [], runs: [], paused: false, pause_reason: null, cookie_check: null,
    global_limits: { refresh: 20, history: 7 }, batches: [{ id: 'batch', mode: 'refresh', post_limit: 20, completed: 19 }] };
  global.fetch = async (url, init) => {
    if (init.method === 'GET') return new Response(JSON.stringify(state));
    writes.push({ url, ...init }); Object.assign(state.global_limits, JSON.parse(init.body));
    return new Response(JSON.stringify(state.global_limits));
  };
  const ui = await mount('SubscriptionDashboard');
  try {
    const form = ui.container.querySelector('[aria-label="跨博主每轮新增总限额"]');
    assert.ok(form);
    assert.equal(form.querySelector('[name=refresh]').value, '20');
    assert.equal(form.querySelector('[name=history]').value, '7');
    assert.match(ui.container.textContent, /归档重复不占额度/);
    assert.match(ui.container.textContent, /不是网络请求上限/);
    assert.match(ui.container.textContent, /延期/);
    assert.match(ui.container.textContent, /19 \/ 20/);
    await act(async () => { form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); await tick(); });
    assert.equal(writes.length, 1);
    assert.equal(writes[0].url, '/api/automation/limits');
    assert.equal(writes[0].method, 'PATCH');
    assert.deepEqual(JSON.parse(writes[0].body), { refresh: 20, history: 7 });
    assert.match(require('../src/lib/automation.ts').reasonLabel('global_post_limit'), /总限额/);
  } finally { await ui.close(); global.fetch = original; }
});

test('settings read errors remain visible rather than implying cookies are absent', async () => {
  const original = global.fetch; global.fetch = async () => { throw new Error('offline'); };
  const ui = await mount('CookieForm');
  try { assert.match(ui.container.querySelector('[role=alert]').textContent, /当前配置状态未知/); }
  finally { await ui.close(); global.fetch = original; }
});
