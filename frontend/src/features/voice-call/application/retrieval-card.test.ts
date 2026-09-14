import { strict as assert } from 'node:assert';
import test from 'node:test';
import { createRetrievalCardMarkup, normalizeRetrievalPayload, shouldRenderRetrieval } from './retrieval-card.ts';

test('renders only a compact document topic when retrieval was used', () => {
  assert.deepEqual(normalizeRetrievalPayload({ used_rag: true, message: 'Políticas de reembolso' }), {
    usedRag: true,
    message: 'Políticas de reembolso',
  });
  assert.equal(shouldRenderRetrieval({ used_rag: false }), false);
  assert.equal(createRetrievalCardMarkup('rag-no', { used_rag: false }), '');

  const markup = createRetrievalCardMarkup('rag-yes', {
    used_rag: true,
    message: 'Políticas de reembolso',
  });
  assert.match(markup, /class="tool-call"/);
  assert.match(markup, /Contexto/);
  assert.match(markup, /Políticas de reembolso/);
  assert.doesNotMatch(markup, /¿Usó RAG\?/);
  assert.doesNotMatch(markup, />Sí</);
});
