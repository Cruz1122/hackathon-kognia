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

  async *streamAudio(
    audio: Blob,
    mimeType: string,
    history: ChatMessage[],
    signal: AbortSignal,
  ): AsyncGenerator<{ type: 'audio' | 'done'; chunk?: Uint8Array; sampleRate?: number }> {
    const response = await fetch(`${this.apiUrl}/voice`, {
      method: 'POST',
      headers: {
        'Content-Type': mimeType,
        'X-Chat-History': JSON.stringify(history),
        Accept: 'audio/L16',
      },
      body: audio,
      signal,
    });
    if (!response.ok || !response.body) throw new Error(`El agente no está disponible (HTTP ${response.status})`);
    const sampleRate = Number(response.headers.get('X-Audio-Sample-Rate') ?? 24000);
    const reader = response.body.getReader();
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (value?.byteLength) yield { type: 'audio', chunk: value, sampleRate };
    }
    yield { type: 'done' };
  }
}
