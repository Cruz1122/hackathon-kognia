import { strict as assert } from 'node:assert';
import test from 'node:test';
import { renderMarkdown } from './detail-panel.ts';

test('renders markdown headings lists and emphasis in the source panel', () => {
  const html = renderMarkdown('## Políticas\n\n- **24 horas** antes\n- Sin cargo');
  assert.match(html, /<h2>Políticas<\/h2>/);
  assert.match(html, /<ul>/);
  assert.match(html, /<strong>24 horas<\/strong>/);
});

test('drops raw html and unsafe links from markdown', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n[x](javascript:alert(1))');
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /javascript:/i);
});
