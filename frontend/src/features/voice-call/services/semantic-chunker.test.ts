import { strict as assert } from 'node:assert';
import test from 'node:test';
import { SemanticChunker } from './semantic-chunker.ts';

test('waits for a useful sentence instead of speaking every token', () => {
  const chunker = new SemanticChunker();
  assert.deepEqual(chunker.push('Sí, por supuesto. '), ['Sí, por supuesto.']);
});

test('flushes an incomplete response at the end of a stream', () => {
  const chunker = new SemanticChunker();
  assert.deepEqual(chunker.push('Esta respuesta todavía no termina'), []);
  assert.deepEqual(chunker.flush(), ['Esta respuesta todavía no termina']);
});

test('cuts long responses at a bounded word count', () => {
  const chunker = new SemanticChunker(8, 10);
  const chunks = chunker.push('uno dos tres cuatro cinco seis siete ocho nueve diez once doce');
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].split(/\s+/).length, 10);
});
