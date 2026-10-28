/* eslint-disable @typescript-eslint/no-require-imports -- Node test runner with TypeScript transpilation. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) { return originalResolve.call(this, request.startsWith('@/') ? path.join(__dirname, '../src', request.slice(2)) : request, ...args); };
const originalLoad = Module._load;
Module._load = function (request, ...args) { if (request === 'next/navigation') return { useRouter: () => ({ push() {} }) }; return originalLoad.call(this, request, ...args); };
for (const ext of ['.ts', '.tsx']) require.extensions[ext] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText, filename);
process.env.NODE_ENV = 'test';
const React = require('react');
const { act } = React;
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><button id="outside">outside</button></body></html>', { url: 'http://localhost' });
global.window = dom.window; global.document = dom.window.document; global.HTMLElement = dom.window.HTMLElement;
global.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom has no native modal top layer. Exercise our focus handling around the platform API.
window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
window.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new window.Event('close')); };
const { createRoot } = require('react-dom/client');
const api = require('../src/lib/api.ts');
const file = (name, kind) => ({ filename: name, path: `facebook/Jane Public/${name}`, kind, thumb_url: `/thumb/${name}`, file_url: `/api/profiles/facebook/Jane%20Public/file/${name}`, bytes: 10, mtime: 0, media_id: null });
const metadata = { name: 'Jane Public', platform: 'facebook', images: [file('nested/a%20pic.jpg', 'image')], videos: [file('clip.mp4', 'video')], image_count: 1, video_count: 1, total_bytes: 20 };
async function mount(meta = metadata) {
  api.getProfile = async () => meta;
  const container = document.createElement('div'); document.body.append(container); const root = createRoot(container);
  await act(async () => { root.render(React.createElement(require('../src/components/ProfileGallery.tsx').ProfileGallery, { platform: 'facebook', name: 'Jane Public' })); });
  return { container, close: async () => { await act(async () => root.unmount()); container.remove(); } };
}
async function click(node) { assert.ok(node, 'expected clickable element'); await act(async () => { node.focus(); node.click(); }); }
async function key(node, value, options = {}) { await act(async () => node.dispatchEvent(new window.KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...options }))); }
function button(ui, label) { return [...ui.container.querySelectorAll('button')].find((node) => node.getAttribute('aria-label') === label || node.textContent === label); }

test('gallery opens full image inline, navigates to playable video and keeps separate downloads', async () => {
  const ui = await mount();
  try {
    assert.ok(ui.container.querySelector('a[href$="/zip"]'));
    const trigger = button(ui, '预览 nested/a%20pic.jpg');
    await click(trigger);
    const dialog = ui.container.querySelector('dialog[open]'); assert.ok(dialog);
    assert.equal(dialog.getAttribute('aria-modal'), 'true');
    assert.ok(document.getElementById(dialog.getAttribute('aria-labelledby')));
    const image = dialog.querySelector('img');
    assert.equal(image.getAttribute('src'), '/api/profiles/facebook/Jane%20Public/preview/nested/a%20pic.jpg');
    assert.equal(dialog.querySelector('a').getAttribute('href'), metadata.images[0].file_url);
    assert.ok(ui.container.querySelector('a[aria-label="下载 clip.mp4"]'));
    assert.equal(document.activeElement, button(ui, '关闭预览'));
    await click(button(ui, '下一项'));
    const video = dialog.querySelector('video'); assert.ok(video);
    assert.equal(video.getAttribute('src'), '/api/profiles/facebook/Jane%20Public/preview/clip.mp4');
    assert.equal(video.controls, true); assert.equal(video.playsInline, true);
    assert.equal(video.autoplay, false); assert.equal(video.preload, 'metadata');
    assert.equal(dialog.querySelector('img'), null);
    await key(button(ui, '下一项'), 'ArrowLeft'); assert.ok(dialog.querySelector('img'));
    await key(dialog, 'Escape'); assert.equal(ui.container.querySelector('dialog'), null);
    assert.ok(document.activeElement === trigger, 'focus restored to opener');
  } finally { await ui.close(); }
});

test('preview replaces only the operation segment for a profile named file, preserving encoded nested paths', async () => {
  const entries = ['image', 'video'].map((kind) => {
    const filename = `file/nested%20folder/a%23b%25.${kind === 'image' ? 'jpg' : 'mp4'}`;
    return { ...file(filename, kind), path: `facebook/file/${filename}`, file_url: `/api/profiles/facebook/file/file/${filename}` };
  });
  const ui = await mount({ ...metadata, name: 'file', images: [entries[0]], videos: [entries[1]] });
  try {
    await click(button(ui, `预览 ${entries[0].filename}`));
    for (const entry of entries) {
      const dialog = ui.container.querySelector('dialog');
      assert.equal(dialog.querySelector(entry.kind === 'image' ? 'img' : 'video').getAttribute('src'), `/api/profiles/facebook/file/preview/${entry.filename}`);
      assert.equal(dialog.querySelector('a[download]').getAttribute('href'), entry.file_url);
      if (entry.kind === 'image') await click(button(ui, '下一项'));
    }
  } finally { await ui.close(); }
});

test('preview traps keyboard focus, handles cancel/backdrop, stops video on close and displays errors', async () => {
  const ui = await mount();
  try {
    const trigger = button(ui, '预览 clip.mp4'); await click(trigger);
    let dialog = ui.container.querySelector('dialog');
    const controls = [...dialog.querySelectorAll('button:not(:disabled), a[href], video[controls]')];
    controls[0].focus(); await key(controls[0], 'Tab', { shiftKey: true }); assert.ok(document.activeElement === controls.at(-1), 'Shift-Tab wraps to last control');
    await key(controls.at(-1), 'Tab'); assert.ok(document.activeElement === controls[0], 'Tab wraps to first control');
    const video = dialog.querySelector('video');
    await act(async () => video.dispatchEvent(new window.Event('error')));
    assert.match(dialog.textContent, /无法预览/);
    assert.ok(dialog.querySelector('a[href$="/file/clip.mp4"]'));
    await act(async () => dialog.dispatchEvent(new window.Event('cancel', { cancelable: true })));
    assert.equal(ui.container.querySelector('video'), null); assert.ok(document.activeElement === trigger, 'focus restored to opener');
    await click(trigger); dialog = ui.container.querySelector('dialog');
    await click(dialog); assert.equal(ui.container.querySelector('dialog'), null);
    await click(trigger); await click(button(ui, '关闭预览')); assert.equal(ui.container.querySelector('dialog'), null);
  } finally { await ui.close(); }
});

test('empty gallery has no preview controls; single media disables previous and next', async () => {
  let ui = await mount({ ...metadata, images: [], videos: [] });
  try { assert.match(ui.container.textContent, /暂无媒体/); assert.equal(ui.container.querySelector('dialog'), null); } finally { await ui.close(); }
  ui = await mount({ ...metadata, videos: [] });
  try { await click(button(ui, '预览 nested/a%20pic.jpg')); assert.equal(button(ui, '上一项').disabled, true); assert.equal(button(ui, '下一项').disabled, true); } finally { await ui.close(); }
});
