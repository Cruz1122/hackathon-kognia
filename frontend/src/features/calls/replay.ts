import { accessTokenKey } from '../auth/session-guard';
import { showToast } from '../voice-call/infrastructure/toast';

type TimelineEvent = {
  type: string;
  offset_ms: number;
  payload?: Record<string, unknown>;
};

type Projected = {
  lifecycle: string;
  agent_state: string;
  transcript: { speaker: string; text: string; offset_ms: number }[];
  tools: { tool?: string; status?: string }[];
  rag: { type: string; topic?: string }[];
};

function project(events: TimelineEvent[], timelineMs: number): Projected {
  const state: Projected = {
    lifecycle: 'STARTING',
    agent_state: 'listening',
    transcript: [],
    tools: [],
    rag: [],
  };
  const tools = new Map<string, { tool?: string; status?: string }>();
  for (const event of events) {
    if (event.offset_ms > timelineMs) break;
    const payload = event.payload ?? {};
    if (event.type === 'lifecycle') state.lifecycle = String(payload.state ?? state.lifecycle);
    if (event.type === 'agent.state') state.agent_state = String(payload.state ?? state.agent_state);
    if (event.type === 'transcript.final') {
      state.transcript.push({
        speaker: String(payload.speaker ?? 'customer'),
        text: String(payload.text ?? ''),
        offset_ms: event.offset_ms,
      });
    }
    if (event.type === 'tool.started' || event.type === 'tool.completed') {
      const id = String(payload.tool_call_id ?? payload.tool ?? tools.size);
      const current = tools.get(id) ?? { tool: String(payload.tool ?? '') };
      if (event.type === 'tool.completed') current.status = String(payload.status ?? 'completed');
      tools.set(id, current);
    }
    if (event.type === 'rag.started' || event.type === 'rag.completed') {
      state.rag.push({ type: event.type, topic: String(payload.topic ?? payload.title ?? '') });
    }
  }
  state.tools = [...tools.values()];
  return state;
}

export function bootReplay(root: HTMLElement, apiUrl: string): () => void {
  const params = new URLSearchParams(window.location.search);
  const callId = params.get('id') ?? '';
  const audio = root.querySelector<HTMLAudioElement>('audio');
  const transcript = root.querySelector<HTMLElement>('[data-transcript]');
  const tools = root.querySelector<HTMLElement>('[data-tools]');
  const rag = root.querySelector<HTMLElement>('[data-rag]');
  const lifecycle = root.querySelector<HTMLElement>('[data-lifecycle]');
  const agent = root.querySelector<HTMLElement>('[data-agent]');
  const canvas = root.querySelector<HTMLCanvasElement>('canvas');
  let events: TimelineEvent[] = [];
  let offset = 0;
  let waveform: number[] = [];

  function paint(timelineMs: number): void {
    const state = project(events, timelineMs);
    if (lifecycle) lifecycle.textContent = state.lifecycle;
    if (agent) agent.textContent = state.agent_state;
    if (transcript) {
      transcript.replaceChildren();
      for (const line of state.transcript) {
        const node = document.createElement('p');
        node.textContent = `${line.speaker === 'agent' ? 'Agente' : 'Cliente'}: ${line.text}`;
        transcript.append(node);
      }
    }
    if (tools) tools.textContent = state.tools.map((tool) => `${tool.tool ?? 'tool'} ${tool.status ?? ''}`).join(' · ') || 'Sin tools';
    if (rag) rag.textContent = state.rag.map((item) => item.topic || item.type).join(' · ') || 'Sin RAG';
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    context.fillStyle = '#f7c974';
    waveform.forEach((peak, index) => {
      const barWidth = width / Math.max(waveform.length, 1);
      const barHeight = Math.max(4, peak * height);
      context.fillRect(index * barWidth, (height - barHeight) / 2, Math.max(2, barWidth - 2), barHeight);
    });
  }

  async function load(): Promise<void> {
    const token = sessionStorage.getItem(accessTokenKey)?.trim() ?? '';
    if (!token || !callId || !audio) {
      showToast('Falta la llamada a reproducir', 'warning');
      return;
    }
    const headers = { Authorization: `Bearer ${token}` };
    const [callResponse, timelineResponse, recordingResponse] = await Promise.all([
      fetch(`${apiUrl}/calls/${callId}`, { headers }),
      fetch(`${apiUrl}/calls/${callId}/timeline`, { headers }),
      fetch(`${apiUrl}/calls/${callId}/recording`, { headers }),
    ]);
    if (!callResponse.ok || !timelineResponse.ok || !recordingResponse.ok) {
      showToast('No se pudo abrir la grabación', 'error');
      return;
    }
    const call = (await callResponse.json()) as { recording_offset_ms?: number; recording?: { waveform?: { peaks?: number[] } } };
    const timeline = (await timelineResponse.json()) as { events: TimelineEvent[] };
    offset = call.recording_offset_ms ?? 0;
    waveform = call.recording?.waveform?.peaks ?? [];
    events = timeline.events;
    const blob = await recordingResponse.blob();
    audio.src = URL.createObjectURL(blob);
    audio.addEventListener('timeupdate', () => paint(audio.currentTime * 1000 + offset));
    audio.addEventListener('seeked', () => paint(audio.currentTime * 1000 + offset));
    paint(offset);
    showToast('Replay listo', 'success');
  }

  canvas?.addEventListener('click', (event) => {
    if (!audio || !canvas || !audio.duration) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    audio.currentTime = Math.max(0, ratio * audio.duration);
  });

  void load().catch(() => showToast('No se pudo cargar el replay', 'error'));
  return () => {
    if (audio?.src) URL.revokeObjectURL(audio.src);
  };
}
