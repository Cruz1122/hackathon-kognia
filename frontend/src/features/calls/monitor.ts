import { accessTokenKey } from '../auth/session-guard';
import { showToast } from '../voice-call/infrastructure/toast';

export type LiveCall = {
  id: string;
  caller: string;
  callee: string;
  lifecycle: string;
  agent_state: string;
  started_at: string;
  duration_ms: number;
  recording_offset_ms: number;
};

type MonitorEvent = {
  type: string;
  offset_ms?: number;
  payload?: Record<string, unknown>;
  call?: LiveCall;
};

const workletSource = `
class CallMixer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queues = [[], []];
    this.port.onmessage = (event) => {
      const index = event.data.channel === 2 ? 1 : 0;
      this.queues[index].push(event.data.samples);
    };
  }
  process(_inputs, outputs) {
    const output = outputs[0][0];
    if (!output) return true;
    output.fill(0);
    for (const queue of this.queues) {
      let pending = queue[0];
      if (!pending || pending.length < output.length) continue;
      for (let index = 0; index < output.length; index += 1) output[index] += pending[index] * 0.6;
      const rest = pending.subarray(output.length);
      if (rest.length) queue[0] = rest;
      else queue.shift();
    }
    return true;
  }
}
registerProcessor('call-mixer', CallMixer);
`;

function pcmToFloat(pcm: ArrayBuffer): Float32Array {
  const view = new DataView(pcm);
  const samples = new Float32Array(Math.floor(pcm.byteLength / 2));
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 32768;
  }
  return samples;
}

export function bootCallMonitor(root: HTMLElement, apiUrl: string): () => void {
  const list = root.querySelector<HTMLElement>('[data-call-list]');
  const detail = root.querySelector<HTMLElement>('[data-call-detail]');
  const transcript = root.querySelector<HTMLElement>('[data-transcript]');
  const tools = root.querySelector<HTMLElement>('[data-tools]');
  const rag = root.querySelector<HTMLElement>('[data-rag]');
  const lifecycle = root.querySelector<HTMLElement>('[data-lifecycle]');
  const agent = root.querySelector<HTMLElement>('[data-agent]');
  const duration = root.querySelector<HTMLElement>('[data-duration]');
  const caller = root.querySelector<HTMLElement>('[data-caller]');
  let stopped = false;
  let monitor: WebSocket | null = null;
  let audioSocket: WebSocket | null = null;
  let selected = '';
  let startedAt = 0;
  const jitter: Record<number, Float32Array[]> = { 1: [], 2: [] };
  let worklet: AudioWorkletNode | null = null;

  async function ensureWorklet(): Promise<AudioWorkletNode | null> {
    if (worklet) return worklet;
    const context = new AudioContext({ sampleRate: 16000 });
    const blob = new Blob([workletSource], { type: 'application/javascript' });
    await context.audioWorklet.addModule(URL.createObjectURL(blob));
    worklet = new AudioWorkletNode(context, 'call-mixer');
    worklet.connect(context.destination);
    await context.resume();
    return worklet;
  }

  function pushAudio(channel: number, samples: Float32Array): void {
    const queue = jitter[channel] ?? jitter[1];
    queue.push(samples);
    const queued = queue.reduce((total, chunk) => total + chunk.length, 0);
    if (queued < 16000 * 0.08 || !worklet) return;
    const merged = new Float32Array(queued);
    let offset = 0;
    for (const chunk of queue) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    queue.length = 0;
    worklet.port.postMessage({ channel, samples: merged });
  }

  function renderEvent(event: MonitorEvent): void {
    const payload = event.payload ?? {};
    if (event.type === 'call.snapshot' && event.call) {
      if (lifecycle) lifecycle.textContent = event.call.lifecycle;
      if (agent) agent.textContent = event.call.agent_state;
      if (caller) caller.textContent = event.call.caller || 'Cliente';
      startedAt = Date.parse(event.call.started_at);
      return;
    }
    if (event.type === 'lifecycle' && lifecycle) lifecycle.textContent = String(payload.state ?? '');
    if (event.type === 'agent.state' && agent) agent.textContent = String(payload.state ?? '');
    if ((event.type === 'transcript.final' || event.type === 'transcript.partial') && transcript) {
      const line = document.createElement('p');
      line.textContent = `${payload.speaker === 'agent' ? 'Agente' : 'Cliente'}: ${String(payload.text ?? '')}`;
      if (event.type === 'transcript.partial') line.dataset.partial = 'true';
      const previous = transcript.querySelector('[data-partial="true"]');
      if (event.type === 'transcript.partial' && previous) previous.replaceWith(line);
      else transcript.append(line);
    }
    if (event.type.startsWith('tool.') && tools) {
      const line = document.createElement('p');
      line.textContent = `${String(payload.tool ?? 'tool')} · ${String(payload.status ?? event.type)}`;
      tools.append(line);
    }
    if (event.type.startsWith('rag.') && rag) {
      const line = document.createElement('p');
      line.textContent = `${event.type} · ${String(payload.topic ?? payload.title ?? '')}`;
      rag.append(line);
    }
  }

  function connect(callId: string): void {
    const token = sessionStorage.getItem(accessTokenKey)?.trim() ?? '';
    if (!token) {
      showToast('Inicia sesión para ver la llamada', 'warning');
      return;
    }
    monitor?.close();
    audioSocket?.close();
    if (transcript) transcript.replaceChildren();
    if (tools) tools.replaceChildren();
    if (rag) rag.replaceChildren();
    const monitorSocket = new WebSocket(`${apiUrl.replace(/^http/, 'ws')}/ws/calls/monitor`);
    monitor = monitorSocket;
    monitorSocket.addEventListener('open', () => {
      monitorSocket.send(JSON.stringify({ type: 'auth', token }));
      monitorSocket.send(JSON.stringify({ type: 'subscribe.call', call_id: callId }));
      showToast('Monitor conectado', 'success');
    });
    monitorSocket.addEventListener('message', (message) => {
      renderEvent(JSON.parse(String(message.data)) as MonitorEvent);
    });
    monitorSocket.addEventListener('close', () => {
      if (!stopped && selected === callId) {
        showToast('El monitor se cerró. La llamada sigue.', 'info');
        window.setTimeout(() => {
          if (!stopped && selected === callId) connect(callId);
        }, 1200);
      }
    });
    const audio = new WebSocket(`${apiUrl.replace(/^http/, 'ws')}/ws/calls/${callId}/audio`);
    audio.binaryType = 'arraybuffer';
    audioSocket = audio;
    audio.addEventListener('open', () => {
      audio.send(JSON.stringify({ type: 'auth', token }));
      void ensureWorklet();
    });
    audio.addEventListener('message', (message) => {
      if (!(message.data instanceof ArrayBuffer) || message.data.byteLength < 11) return;
      const view = new DataView(message.data);
      const channel = view.getUint8(1);
      pushAudio(channel, pcmToFloat(message.data.slice(11)));
    });
  }

  async function refresh(): Promise<void> {
    const token = sessionStorage.getItem(accessTokenKey)?.trim() ?? '';
    if (!list || !token) return;
    const response = await fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const body = (await response.json()) as { calls: LiveCall[] };
    list.replaceChildren();
    if (!body.calls.length) {
      const empty = document.createElement('p');
      empty.textContent = 'No hay llamadas activas.';
      list.append(empty);
      return;
    }
    for (const call of body.calls) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'call-row';
      button.textContent = `${call.caller || 'Cliente'} · ${call.lifecycle}`;
      button.addEventListener('click', () => {
        selected = call.id;
        if (detail) detail.hidden = false;
        const replay = root.querySelector<HTMLAnchorElement>('[data-replay]');
        if (replay) replay.href = `/calls/replay?id=${call.id}`;
        connect(call.id);
      });
      list.append(button);
    }
  }

  const timer = window.setInterval(() => {
    if (duration && startedAt) {
      const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
      duration.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    }
    void refresh().catch(() => showToast('No se pudo listar llamadas', 'error'));
  }, 2000);
  void refresh();

  return () => {
    stopped = true;
    window.clearInterval(timer);
    monitor?.close();
    audioSocket?.close();
  };
}
