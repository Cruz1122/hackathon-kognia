import { bindDetailClicks, toolDetailFromEvent } from './detail-panel';
import { completeRetrievalCard, createRetrievalCardMarkup, toolCallBusyMarkup } from './retrieval-card';
import { showToast } from '../infrastructure/toast';
import { applyCallAgentSignals, resetCallAgentSignals } from './agent-signals';

type MonitorEvent = {
  type?: string;
  seq?: number;
  offset_ms?: number;
  payload?: Record<string, unknown>;
  call_id?: string;
  message_id?: string;
  call?: { id: string; conversation_id?: string; lifecycle?: string };
};

const SAMPLE_RATE = 16000;
const FRAME_HEADER = 11;
const CHANNEL_AGENT = 2;

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function lucideRefresh(): void {
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

function socketUrl(apiUrl: string, path: string): string {
  return `${apiUrl.replace(/^http/, 'ws')}${path}`;
}

export function bootLiveCall(apiUrl: string, token: string, callId: string): () => Promise<void> {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector<HTMLElement>('#conversationEmpty');
  const status = document.querySelector<HTMLElement>('#callConnectionStatus');
  const statusText = document.querySelector('#callConnectionStatusText');
  const player = document.querySelector<HTMLElement>('.player');
  const playBtn = document.querySelector<HTMLButtonElement>('#playBtn');
  if (!(conversation instanceof HTMLElement) || !playBtn) {
    return async () => undefined;
  }

  bindDetailClicks(conversation);
  const emptyCopy = conversationEmpty?.innerHTML ?? '';
  if (conversationEmpty) {
    conversationEmpty.hidden = false;
    conversationEmpty.innerHTML = emptyCopy;
  }
  if (status) {
    status.hidden = false;
    status.classList.add('is-connected');
    status.classList.remove('is-error');
  }
  if (statusText) statusText.textContent = 'En vivo';
  player?.classList.add('is-live-listen');

  const context = new AudioContext({ sampleRate: SAMPLE_RATE });
  const gain = context.createGain();
  gain.connect(context.destination);
  void context.resume();

  let disposed = false;
  let ended = false;
  let silenced = false;
  let nextCustomer = 0;
  let nextAgent = 0;
  const customerSources: AudioBufferSourceNode[] = [];
  const agentSources: AudioBufferSourceNode[] = [];
  let customerBubble: HTMLElement | null = null;
  const seen = new Set<string>();
  let activeCallId = callId;
  const tools = new Map<string, { start: number; payload: Record<string, unknown> }>();
  const startedAt = performance.now();

  function stopList(list: AudioBufferSourceNode[]): void {
    list.forEach((source) => {
      try { source.stop(); } catch { /* already finished */ }
    });
    list.length = 0;
  }

  function stopAgentPlayback(): void {
    stopList(agentSources);
    nextAgent = 0;
  }

  function schedule(samples: Int16Array, agent: boolean): void {
    if (silenced || samples.length === 0 || context.state === 'closed') return;
    const buffer = context.createBuffer(1, samples.length, SAMPLE_RATE);
    const data = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) data[index] = (samples[index] ?? 0) / 32768;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(gain);
    const queued = agent ? nextAgent : nextCustomer;
    const when = Math.max(context.currentTime + 0.02, queued);
    source.start(when);
    const playing = agent ? agentSources : customerSources;
    playing.push(source);
    source.onended = () => {
      const index = playing.indexOf(source);
      if (index >= 0) playing.splice(index, 1);
    };
    if (agent) nextAgent = when + buffer.duration;
    else nextCustomer = when + buffer.duration;
  }

  function playFrame(frame: ArrayBuffer): void {
    if (frame.byteLength <= FRAME_HEADER) return;
    const view = new DataView(frame);
    const sampleCount = Math.floor((frame.byteLength - FRAME_HEADER) / 2);
    const samples = new Int16Array(sampleCount);
    for (let index = 0; index < sampleCount; index += 1) samples[index] = view.getInt16(FRAME_HEADER + index * 2, true);
    schedule(samples, view.getUint8(1) === CHANNEL_AGENT);
  }

  function follow(row: HTMLElement): void {
    const scroller = document.getElementById('appContent');
    const nearEnd = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 120;
    if (nearEnd) row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function appendRow(className: string, html: string, atMs: number): HTMLElement {
    if (conversationEmpty) conversationEmpty.hidden = true;
    const row = document.createElement('div');
    row.className = `${className} timeline-item visible enter`;
    row.dataset.at = String(atMs / 1000);
    row.innerHTML = html;
    conversation.append(row);
    lucideRefresh();
    follow(row);
    window.setTimeout(() => row.classList.remove('enter'), 900);
    return row;
  }

  function messageHtml(speaker: 'customer' | 'agent', text: string, atMs: number): string {
    const time = formatTime(atMs / 1000);
    if (speaker === 'agent') {
      return `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Agente</span></div><div class="message complete">${escapeHtml(text)}<span class="message-time">${time}</span></div></div>`;
    }
    return `<div class="message-wrap"><div class="message-meta"><span>Cliente</span><i data-lucide="user-round" aria-hidden="true"></i></div><div class="message complete">${escapeHtml(text)}<span class="message-time">${time}</span></div></div>`;
  }

  function showEnded(atMs: number): void {
    if (ended || disposed) return;
    ended = true;
    const row = appendRow(
      'system-event call-ended',
      `<span class="call-ended-label"><i data-lucide="phone-off" aria-hidden="true"></i><span>Llamada finalizada · ${formatTime(atMs / 1000)}</span></span>`,
      atMs,
    );
    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    if (statusText) statusText.textContent = 'Conversación abierta';
    showToast('Terminó el tramo de voz. La conversación sigue abierta.', 'info');
  }

  function handleEvent(event: MonitorEvent): void {
    if (disposed) return;
    if (event.type === 'call.snapshot' && event.call) {
      const resumed = event.call.id !== activeCallId;
      activeCallId = event.call.id;
      if (resumed) {
        ended = false;
        customerBubble = null;
        stopList(customerSources);
        stopAgentPlayback();
        nextCustomer = 0;
        resetCallAgentSignals();
        connectAudio(activeCallId);
        if (statusText) statusText.textContent = 'En vivo';
        showToast('Llamada retomada en la misma conversación', 'success');
      }
      return;
    }
    const seq = Number(event.seq ?? 0);
    const key = event.message_id ?? (seq ? `${event.call_id ?? activeCallId}:${seq}` : '');
    if (key && seen.has(key)) return;
    if (key) seen.add(key);
    const type = String(event.type ?? '');
    const payload = event.payload ?? {};
    const atMs = Number(event.offset_ms ?? 0);
    if (type === 'conversation.event') {
      const text = String(payload.text ?? '');
      appendRow('system-event', `<span class="call-ended-label">${escapeHtml(text)}</span>`, atMs);
      if (statusText) statusText.textContent = 'Conversación abierta · WhatsApp';
      showToast(text, 'info');
      return;
    }
    if (type === 'agent.signals') {
      applyCallAgentSignals(payload);
      return;
    }
    if (type === 'transcript.partial' && payload.speaker !== 'agent') {
      const text = String(payload.text ?? '').trim();
      if (!text) return;
      if (!customerBubble) {
        const row = appendRow('message-row customer', messageHtml('customer', text, atMs), atMs);
        customerBubble = row.querySelector('.message');
      } else {
        const time = customerBubble.querySelector('.message-time')?.textContent ?? formatTime(atMs / 1000);
        customerBubble.innerHTML = `${escapeHtml(text)}<span class="message-time">${time}</span>`;
      }
      return;
    }
    if (type === 'transcript.final' && payload.speaker !== 'agent') {
      const text = String(payload.text ?? '');
      if (customerBubble) {
        customerBubble.innerHTML = `${escapeHtml(text)}<span class="message-time">${formatTime(atMs / 1000)}</span>`;
        customerBubble = null;
      } else {
        appendRow(`message-row customer${payload.channel === 'whatsapp' ? ' channel-whatsapp' : ''}`, messageHtml('customer', text, atMs), atMs);
      }
      return;
    }
    if (type === 'transcript.final' && payload.speaker === 'agent') {
      customerBubble = null;
      appendRow(`message-row agent${payload.channel === 'whatsapp' ? ' channel-whatsapp' : ''}`, messageHtml('agent', String(payload.text ?? ''), atMs), atMs);
      return;
    }
    if (type === 'tool.started') {
      const id = String(payload.tool_call_id ?? payload.tool ?? seq);
      tools.set(id, { start: atMs, payload });
      return;
    }
    if (type === 'tool.completed') {
      const rawId = String(payload.tool_call_id ?? payload.tool ?? seq);
      const started = tools.get(rawId);
      const detail = toolDetailFromEvent(payload, started ? toolDetailFromEvent(started.payload) : undefined);
      const row = appendRow(
        'tool-row',
        `<button type="button" class="tool-call done" id="tool-${escapeHtml(rawId)}" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-busy="false"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(detail.name)}</div><div class="tool-status">${escapeHtml(String(payload.status ?? 'Completado'))}</div></div>${toolCallBusyMarkup()}<div class="done-mark" aria-hidden="true"><i data-lucide="check"></i></div></button>`,
        started?.start ?? atMs,
      );
      const loader = row.querySelector<HTMLElement>('.loader');
      if (loader) loader.style.display = 'none';
      return;
    }
    if (type === 'rag.completed') {
      const id = `rag-${seq || atMs}`;
      const markup = createRetrievalCardMarkup(id, payload);
      if (markup) {
        appendRow('tool-row', markup, atMs);
        completeRetrievalCard(id, payload);
      }
      return;
    }
    if (type === 'lifecycle' && payload.state === 'ACTIVE') {
      appendRow('system-event', `<span class="call-ended-label"><i data-lucide="phone" aria-hidden="true"></i><span>Llamada conectada</span></span>`, atMs);
      return;
    }
    if (type === 'audio.cancelled') {
      stopAgentPlayback();
      return;
    }
    if (type === 'lifecycle' && payload.state === 'ENDED') {
      showEnded(atMs);
    }
  }

  function setControl(playing: boolean): void {
    const icon = playBtn.querySelector('svg, i');
    icon?.remove();
    const node = document.createElement('i');
    node.dataset.lucide = playing ? 'volume-2' : 'volume-x';
    node.setAttribute('aria-hidden', 'true');
    playBtn.append(node);
    playBtn.setAttribute('aria-label', playing ? 'Mutear' : 'Quitar mute');
    const caption = playBtn.closest('.control-stack')?.querySelector('.control-caption');
    if (caption) caption.textContent = playing ? 'Mutear' : 'Escuchar';
    lucideRefresh();
  }

  const listenerAbort = new AbortController();
  const signal = listenerAbort.signal;
  playBtn.addEventListener('click', () => {
    silenced = !silenced;
    gain.gain.value = silenced ? 0 : 1;
    if (!silenced) {
      nextCustomer = context.currentTime + 0.02;
      nextAgent = context.currentTime + 0.02;
      void context.resume();
    }
    setControl(!silenced);
  }, { signal });

  let audioSocket: WebSocket;
  function connectAudio(id: string): void {
    audioSocket?.close();
    const socket = new WebSocket(socketUrl(apiUrl, `/ws/calls/${id}/audio`));
    audioSocket = socket;
    socket.binaryType = 'arraybuffer';
    socket.addEventListener('open', () => socket.send(JSON.stringify({ type: 'auth', token })));
    socket.addEventListener('message', (event) => {
      if (socket === audioSocket && event.data instanceof ArrayBuffer) playFrame(event.data);
    });
  }
  connectAudio(callId);

  const monitorSocket = new WebSocket(socketUrl(apiUrl, '/ws/calls/monitor'));
  monitorSocket.addEventListener('open', () => {
    monitorSocket.send(JSON.stringify({ type: 'auth', token }));
    monitorSocket.send(JSON.stringify({ type: 'subscribe.call', call_id: callId }));
  });
  monitorSocket.addEventListener('message', (event) => {
    if (typeof event.data !== 'string') return;
    try {
      const message = JSON.parse(event.data) as MonitorEvent;
      if (message.type === 'error') {
        if (status) status.classList.add('is-error');
        if (statusText) statusText.textContent = 'No se pudo escuchar la llamada';
        return;
      }
      handleEvent(message);
    } catch {
      return;
    }
  });
  monitorSocket.addEventListener('close', (event) => {
    if (disposed || ended) return;
    if (event.code === 4401 || event.code === 4403) {
      if (status) status.classList.add('is-error');
      if (statusText) statusText.textContent = 'No se pudo escuchar la llamada';
    }
  });

  const endWatch = window.setInterval(() => {
    if (ended || disposed) return;
    void fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok || ended || disposed) return;
        const body = await response.json() as { calls?: Array<{ id: string }> };
        if (!(body.calls ?? []).some((call) => call.id === activeCallId)) showEnded(performance.now() - startedAt);
      })
      .catch(() => undefined);
  }, 2000);

  setControl(true);

  return async () => {
    disposed = true;
    listenerAbort.abort();
    player?.classList.remove('is-live-listen');
    window.clearInterval(endWatch);
    stopList(customerSources);
    stopAgentPlayback();
    audioSocket.close();
    monitorSocket.close();
    await context.close().catch(() => undefined);
  };
}
