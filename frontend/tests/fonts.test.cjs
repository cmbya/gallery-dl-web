/* eslint-disable @typescript-eslint/no-require-imports -- Node's CommonJS test entrypoint. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('layout fonts do not require build-time Google requests', () => {
  const layout = fs.readFileSync(path.join(__dirname, '../src/app/layout.tsx'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../src/app/globals.css'), 'utf8');
  assert.doesNotMatch(layout, /next\/font\/google/);
  assert.doesNotMatch(css, /fonts\.(googleapis|gstatic)\.com/);
  assert.match(css, /--font-sans:.*system-ui/);
  assert.match(css, /--font-mono:.*ui-monospace/);
});
