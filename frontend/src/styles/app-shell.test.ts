import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { test } from 'node:test';

const here = dirname(fileURLToPath(import.meta.url));

function readStyle(name: string): string {
  return readFileSync(join(here, name), 'utf8');
}

function declarationBlock(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  const pattern = new RegExp(`${escaped}\\s*\\{([^}]*)\\}`);
  const match = css.match(pattern);
  assert.ok(match, `missing rule for ${selector}`);
  return match[1];
}

test('call loading does not reserve the detail panel column', () => {
  const css = readStyle('app-shell.css');
  const block = declarationBlock(css, '.app-shell.has-detail-panel.is-call-loading');
  assert.match(
    block,
    /grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    'loading shell must collapse to the main column so the panel column is not pre-reserved',
  );
});

test('call loading overlay is anchored to the main column, not the viewport', () => {
  const css = readStyle('call-demo.css');
  const block = declarationBlock(
    css,
    '.call-demo-page > .call-page-loader, .call-demo-page > .call-page-error',
  );
  assert.match(block, /position:\s*absolute/, 'overlay must anchor to .app-main instead of the viewport');
  assert.match(block, /width:\s*100%/, 'overlay must fill the main column');
  assert.doesNotMatch(block, /100vw/, 'overlay must not stretch beyond the main content column');
});
