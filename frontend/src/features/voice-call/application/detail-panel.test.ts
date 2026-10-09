import { strict as assert } from 'node:assert';
import test from 'node:test';
import { renderDetailMarkup, renderMarkdown } from './detail-panel.ts';

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

test('renders a JSON tool result as a highlighted block', () => {
  const html = renderDetailMarkup({
    kind: 'tool',
    name: 'Buscar IPS',
    inputs: [{ label: 'Ciudad', value: 'Manizales' }],
    outputs: [{ label: 'Results', value: '{"count":1,"results":[{"name":"Hospital"}]}' }],
  });
  assert.match(html, /class="detail-field is-long is-json"/);
  assert.match(html, /<pre class="detail-json">/);
  assert.match(html, /class="json-key"/);
  assert.match(html, /class="json-string">&quot;Hospital&quot;/);
  assert.match(html, /class="json-number">1</);
  assert.doesNotMatch(html, /\{"count":1/);
  assert.match(html, /<div class="detail-field"><dt>Ciudad<\/dt><dd>Manizales<\/dd><\/div>/);
});

test('renders a Python tool result list as highlighted JSON', () => {
  const html = renderDetailMarkup({
    kind: 'tool',
    name: 'Search ips',
    inputs: [],
    outputs: [{
      label: 'Results',
      value: "{'site_name': 'A DOS MANOS', 'phone': 'Linea, WhatsApp 322', 'level': None}, {'site_name': 'ARTMEDICA', 'level': None}",
    }],
  });
  assert.match(html, /<pre class="detail-json">/);
  assert.match(html, /class="json-string">&quot;A DOS MANOS&quot;/);
  assert.match(html, /class="json-string">&quot;Linea, WhatsApp 322&quot;/);
  assert.match(html, /class="json-null">null</);
  assert.match(html, /class="json-string">&quot;ARTMEDICA&quot;/);
  assert.doesNotMatch(html, /<script>/);
});

test('keeps scalar tool fields inline', () => {
  const html = renderDetailMarkup({
    kind: 'tool',
    name: 'Sumar',
    inputs: [],
    outputs: [{ label: 'Total', value: '12' }],
  });
  assert.match(html, />12</);
  assert.doesNotMatch(html, /detail-json/);
});
