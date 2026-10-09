import { strict as assert } from 'node:assert';
import test from 'node:test';
import { createRetrievalCardMarkup, normalizeRetrievalPayload, shouldRenderRetrieval } from './retrieval-card.ts';

test('renders only a compact document topic when retrieval was used', () => {
  assert.deepEqual(normalizeRetrievalPayload({
    used_rag: true,
    message: 'Políticas de reembolso',
    title: 'Políticas de atención',
    content: 'La política permite cambios.',
  }), {
    usedRag: true,
    message: 'Políticas de reembolso',
    title: 'Políticas de atención',
    content: 'La política permite cambios.',
  });
  assert.equal(shouldRenderRetrieval({ used_rag: false }), false);
  assert.equal(createRetrievalCardMarkup('rag-no', { used_rag: false }), '');

  const markup = createRetrievalCardMarkup('rag-yes', {
    used_rag: true,
    message: 'Políticas de reembolso',
    title: 'Políticas de atención',
    content: 'La política permite cambios.',
  });
  assert.match(markup, /class="tool-call"/);
  assert.match(markup, /Fuente/);
  assert.match(markup, /Políticas de reembolso/);
  assert.match(markup, /<button type="button"/);
  assert.doesNotMatch(markup, /¿Usó RAG\?/);
  assert.doesNotMatch(markup, />Sí</);
});
