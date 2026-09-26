import { bindDetailClicks, toolDetailFromEvent } from './detail-panel';
import { completeRetrievalCard, createRetrievalCardMarkup, toolCallBusyMarkup } from './retrieval-card';
import { showToast } from '../infrastructure/toast';
import { WAVE_BLEED, drawEventMark, type EventMarkIcon } from './wave-mark';

type MonitorEvent = {
  type?: string;
  seq?: number;
  offset_ms?: number;
  payload?: Record<string, unknown>;
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

export function bootLiveCall(apiUrl: string, token: string, callId: string, onEnded: () => void): () => void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector<HTMLElement>('#conversationEmpty');
  const status = document.querySelector<HTMLElement>('#callConnectionStatus');
  const statusText = document.querySelector('#callConnectionStatusText');
  const waveShell = document.querySelector<HTMLElement>('#waveShell');
  const canvas = document.querySelector<HTMLCanvasElement>('#waveCanvas');
  const currentTimeEl = document.querySelector('#currentTime');
  const durationEl = document.querySelector('#duration');
  const playBtn = document.querySelector<HTMLButtonElement>('#playBtn');
  if (!(conversation instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement) || !waveShell || !playBtn) {
    return () => undefined;
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
  waveShell.classList.add('at-live-edge');
  waveShell.style.setProperty('--progress', '100%');

  const context = new AudioContext({ sampleRate: SAMPLE_RATE });
  const gain = context.createGain();
  gain.connect(context.destination);
  void context.resume();

  let disposed = false;
  let ended = false;
  let silenced = false;
  let nextCustomer = 0;
  let nextAgent = 0;
  const agentSources: AudioBufferSourceNode[] = [];
  let customerBubble: HTMLElement | null = null;
  const seen = new Set<number>();
  const tools = new Map<string, { start: number; payload: Record<string, unknown> }>();
  const waveBins = 180;
  const customerPeaks = Array.from({ length: waveBins }, () => 0.03);
  const agentPeaks = Array.from({ length: waveBins }, () => 0);
  let binAnchor = performance.now();
  let headOffsetMs = 0;
  const marks: { offsetMs: number; icon: EventMarkIcon }[] = [];
  const paint = canvas.getContext('2d');
  const startedAt = performance.now();

  function waveBox(): { width: number; height: number; dpr: number } {
    const rect = (canvas.parentElement ?? canvas).getBoundingClientRect();
    return { width: Math.max(1, rect.width), height: Math.max(1, rect.height), dpr: Math.min(window.devicePixelRatio || 1, 2) };
  }

  function resize(): void {
    if (!paint) return;
    const { width, height, dpr } = waveBox();
    canvas.width = Math.round((width + WAVE_BLEED * 2) * dpr);
    canvas.height = Math.round(height * dpr);
    paint.setTransform(dpr, 0, 0, dpr, WAVE_BLEED * dpr, 0);
  }

  function drawSeries(series: number[], fillStyle: string, width: number, height: number, bursts: boolean): void {
    if (!paint) return;
    const baseline = height - 1;
    const span = Math.max(1, series.length - 1);
    const levelAt = (index: number) => {
      const prev = series[Math.max(0, index - 1)] ?? 0;
      const value = series[index] ?? 0;
      const next = series[Math.min(series.length - 1, index + 1)] ?? value;
      return prev * 0.22 + value * 0.56 + next * 0.22;
    };
    const pointAt = (index: number) => ({
      x: (index / span) * width,
      y: baseline - (5 + Math.pow(Math.min(1, levelAt(index)), 1.25) * height * 0.5),
    });
    const curveThrough = (points: { x: number; y: number }[]) => {
      if (points.length === 0) return;
      paint.lineTo(points[0].x, points[0].y);
      for (let index = 1; index < points.length - 1; index += 1) {
        const current = points[index];
        const next = points[index + 1];
        if (!current || !next) continue;
        paint.quadraticCurveTo(current.x, current.y, (current.x + next.x) / 2, (current.y + next.y) / 2);
      }
      const last = points[points.length - 1];
      if (last) paint.lineTo(last.x, last.y);
    };
    paint.beginPath();
    if (!bursts) {
      const points = [];
      for (let index = 0; index < series.length; index += 1) points.push(pointAt(index));
      paint.moveTo(0, baseline);
      curveThrough(points);
      paint.lineTo(width, baseline);
      paint.closePath();
      paint.fillStyle = fillStyle;
      paint.fill();
      return;
    }
    let open: { x: number; y: number }[] = [];
    const closeBurst = (x: number) => {
      curveThrough(open);
      paint.lineTo(x, baseline);
      paint.closePath();
      open = [];
    };
    for (let index = 0; index < series.length; index += 1) {
      const value = levelAt(index);
      const x = (index / span) * width;
      if (value < 0.05) {
        if (open.length) closeBurst(x);
        continue;
      }
      if (!open.length) paint.moveTo(x, baseline);
      open.push(pointAt(index));
    }
    if (open.length) closeBurst(width);
    paint.fillStyle = fillStyle;
    paint.fill();
  }

  function drawWave(): void {
    if (!paint) return;
    const { width, height, dpr } = waveBox();
    paint.setTransform(dpr, 0, 0, dpr, WAVE_BLEED * dpr, 0);
    paint.clearRect(-WAVE_BLEED, 0, width + WAVE_BLEED * 2, height);
    drawSeries(customerPeaks, '#f7c974', width, height, false);
    drawSeries(agentPeaks, '#faeccf', width, height, true);
    if (headOffsetMs <= 0) return;
    const baseline = height - 1;
    const windowMs = waveBins * 70;
    for (const mark of marks) {
      const ratio = 1 - (headOffsetMs - mark.offsetMs) / windowMs;
      if (ratio < 0 || ratio > 1) continue;
      drawEventMark(paint, ratio * width, baseline, width, mark.icon);
    }
  }

  function pushPeak(samples: Int16Array, agent: boolean): void {
    const now = performance.now();
    const steps = Math.min(waveBins, Math.floor((now - binAnchor) / 70));
    for (let step = 0; step < steps; step += 1) {
      customerPeaks.shift();
      customerPeaks.push(0.02);
      agentPeaks.shift();
      agentPeaks.push(0);
    }
    if (steps > 0) binAnchor += steps * 70;
    let energy = 0;
    for (let index = 0; index < samples.length; index += 1) {
      const value = (samples[index] ?? 0) / 32768;
      energy += value * value;
    }
    const level = Math.min(1, Math.sqrt(energy / Math.max(1, samples.length)) * 4);
    const series = agent ? agentPeaks : customerPeaks;
    const last = series.length - 1;
    series[last] = Math.max(series[last] ?? 0, level);
    drawWave();
  }

  function stopAgentPlayback(): void {
    agentSources.forEach((source) => {
      try { source.stop(); } catch { /* already finished */ }
    });
    agentSources.length = 0;
    nextAgent = 0;
  }

  function schedule(samples: Int16Array, agent: boolean): void {
    pushPeak(samples, agent);
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
    if (agent) {
      nextAgent = when + buffer.duration;
      agentSources.push(source);
      source.onended = () => {
        const index = agentSources.indexOf(source);
        if (index >= 0) agentSources.splice(index, 1);
      };
    } else {
      nextCustomer = when + buffer.duration;
    }
  }

  function playFrame(frame: ArrayBuffer): void {
    if (frame.byteLength <= FRAME_HEADER) return;
    const view = new DataView(frame);
    const frameOffset = view.getUint32(7, false);
    if (frameOffset > headOffsetMs) headOffsetMs = frameOffset;
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

  function pinMark(offsetMs: number, icon: EventMarkIcon): void {
    marks.push({ offsetMs, icon });
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
    showToast('La llamada terminó', 'info');
    onEnded();
  }

  function handleEvent(event: MonitorEvent): void {
    if (disposed) return;
    const seq = Number(event.seq ?? 0);
    if (seq && seen.has(seq)) return;
    if (seq) seen.add(seq);
    const type = String(event.type ?? '');
    const payload = event.payload ?? {};
    const atMs = Number(event.offset_ms ?? 0);
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
        appendRow('message-row customer', messageHtml('customer', text, atMs), atMs);
      }
      return;
    }
    if (type === 'transcript.final' && payload.speaker === 'agent') {
      customerBubble = null;
      appendRow('message-row agent', messageHtml('agent', String(payload.text ?? ''), atMs), atMs);
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
      pinMark(started?.start ?? atMs, 'bot');
      return;
    }
    if (type === 'rag.completed') {
      const id = `rag-${seq || atMs}`;
      const markup = createRetrievalCardMarkup(id, payload);
      if (markup) {
        appendRow('tool-row', markup, atMs);
        completeRetrievalCard(id, payload);
        pinMark(atMs, 'book-search');
      }
      return;
    }
    if (type === 'lifecycle' && payload.state === 'ACTIVE') {
      appendRow('system-event', `<span class="call-ended-label"><i data-lucide="phone" aria-hidden="true"></i><span>Llamada conectada</span></span>`, atMs);
      pinMark(atMs, 'phone');
      return;
    }
    if (type === 'audio.cancelled') {
      stopAgentPlayback();
      return;
    }
    if (type === 'lifecycle' && payload.state === 'ENDED') {
      pinMark(atMs, 'phone-off');
      showEnded(atMs);
    }
  }

  function setControl(playing: boolean): void {
    const icon = playBtn.querySelector('svg, i');
    icon?.remove();
    const node = document.createElement('i');
    node.dataset.lucide = playing ? 'pause' : 'play';
    node.setAttribute('aria-hidden', 'true');
    playBtn.append(node);
    playBtn.setAttribute('aria-label', playing ? 'Silenciar escucha' : 'Escuchar llamada');
    const caption = playBtn.closest('.control-stack')?.querySelector('.control-caption');
    if (caption) caption.textContent = playing ? 'Pausa' : 'Reanudar';
    lucideRefresh();
  }

  playBtn.addEventListener('click', () => {
    silenced = !silenced;
    gain.gain.value = silenced ? 0 : 1;
    if (!silenced) {
      nextCustomer = context.currentTime + 0.02;
      nextAgent = context.currentTime + 0.02;
      void context.resume();
    }
    setControl(!silenced);
  });

  const clock = window.setInterval(() => {
    const seconds = (performance.now() - startedAt) / 1000;
    if (currentTimeEl) currentTimeEl.textContent = formatTime(seconds);
    if (durationEl) durationEl.textContent = formatTime(seconds);
    waveShell.setAttribute('aria-valuemax', String(Math.round(seconds)));
    waveShell.setAttribute('aria-valuenow', String(Math.round(seconds)));
  }, 250);

  const audioSocket = new WebSocket(socketUrl(apiUrl, `/ws/calls/${callId}/audio`));
  audioSocket.binaryType = 'arraybuffer';
  audioSocket.addEventListener('open', () => {
    audioSocket.send(JSON.stringify({ type: 'auth', token }));
  });
  audioSocket.addEventListener('message', (event) => {
    if (event.data instanceof ArrayBuffer) playFrame(event.data);
  });

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
        if (!(body.calls ?? []).some((call) => call.id === callId)) showEnded(performance.now() - startedAt);
      })
      .catch(() => undefined);
  }, 2000);

  resize();
  drawWave();
  setControl(true);
  const resizeObserver = new ResizeObserver(() => {
    resize();
    drawWave();
  });
  resizeObserver.observe(canvas);

  return () => {
    disposed = true;
    window.clearInterval(clock);
    window.clearInterval(endWatch);
    stopAgentPlayback();
    resizeObserver.disconnect();
    audioSocket.close();
    monitorSocket.close();
    void context.close();
  };
}
