import type { ChatMessage } from '../domain/types';
import { backendErrorFromResponse, backendMessage } from './backend-error';
import { parseSse } from './sse-parser';

type TokenEvent = { text?: unknown };
type AgentStreamEvent = {
  type: 'token' | 'done' | 'error' | 'rag.started' | 'rag.completed';
  text?: string;
  message?: string;
  data?: unknown;
};

export class AgentStreamAdapter {
  constructor(
    private readonly apiUrl: string,
    private readonly token?: string,
    private readonly conversationId?: string,
  ) {}

  async *stream(
    prompt: string,
    history: ChatMessage[],
    signal: AbortSignal,
  ): AsyncGenerator<AgentStreamEvent> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const body = this.conversationId
      ? { prompt, conversation_id: this.conversationId }
      : { prompt, messages: history, channel: 'voice-demo' };
    const response = await fetch(`${this.apiUrl}/ask`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw await backendErrorFromResponse(response, 'El agente no está disponible.');
    for await (const event of parseSse(response)) {
      if (event.event === 'token') {
        const payload = event.data as TokenEvent;
        if (typeof payload.text === 'string' && payload.text) yield { type: 'token', text: payload.text };
      } else if (event.event === 'done') {
        yield { type: 'done' };
      } else if (event.event === 'error') {
          const payload = event.data as { message?: unknown };
          yield { type: 'error', message: backendMessage(payload, 'El agente se interrumpió.') };
      } else if (event.event === 'rag.started' || event.event === 'rag.completed') {
        yield { type: event.event, data: event.data };
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
    if (!response.ok) throw await backendErrorFromResponse(response, 'El agente no está disponible.');
    if (!response.body) throw new Error('El agente no devolvió un stream de audio.');
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
