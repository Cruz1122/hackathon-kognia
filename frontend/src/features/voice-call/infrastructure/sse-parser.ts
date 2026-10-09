export type SseEvent = { event: string; data: unknown };

export async function* parseSse(response: Response): AsyncGenerator<SseEvent> {
  if (!response.body) throw new Error('La respuesta no contiene un stream');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? '';
      for (const raw of events) {
        const event = raw.match(/^event:\s*(.+)$/m)?.[1].trim();
        const data = raw.match(/^data:\s*(.+)$/m)?.[1].trim();
        if (!event || !data) continue;
        try {
          yield { event, data: JSON.parse(data) as unknown };
        } catch {
          throw new Error('El agente devolvió un evento inválido');
        }
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}
