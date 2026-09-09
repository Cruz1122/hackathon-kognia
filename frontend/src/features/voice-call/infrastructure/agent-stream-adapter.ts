import type { ChatMessage } from '../domain/types';
import { parseSse } from './sse-parser';

type TokenEvent = { text?: unknown };

export class AgentStreamAdapter {
  constructor(private readonly apiUrl: string) {}

  async *stream(
    prompt: string,
    history: ChatMessage[],
    signal: AbortSignal,
  ): AsyncGenerator<{ type: 'token' | 'done' | 'error'; text?: string; message?: string }> {
    const response = await fetch(`${this.apiUrl}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ prompt, messages: history, channel: 'voice-demo' }),
      signal,
    });
    if (!response.ok) throw new Error(`El agente no está disponible (HTTP ${response.status})`);
    for await (const event of parseSse(response)) {
      if (event.event === 'token') {
        const payload = event.data as TokenEvent;
        if (typeof payload.text === 'string' && payload.text) yield { type: 'token', text: payload.text };
      } else if (event.event === 'done') {
        yield { type: 'done' };
      } else if (event.event === 'error') {
        const payload = event.data as { message?: unknown };
        yield { type: 'error', message: typeof payload.message === 'string' ? payload.message : 'El agente se interrumpió' };
      }
    }
  }
}
