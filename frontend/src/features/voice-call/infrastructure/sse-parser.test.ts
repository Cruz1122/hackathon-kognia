import { strict as assert } from 'node:assert';
import test from 'node:test';
import { parseSse } from './sse-parser.ts';

test('parses SSE events delimited with CRLF', async () => {
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: token\r\ndata: {"text":"hola"}\r\n\r\n'));
      controller.close();
    },
  });
  const events = [];
  for await (const event of parseSse(new Response(body))) events.push(event);
  assert.deepEqual(events, [{ event: 'token', data: { text: 'hola' } }]);
});
