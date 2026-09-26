import { bindDetailClicks, toolDetailFromEvent } from './detail-panel';
import { completeRetrievalCard, createRetrievalCardMarkup, toolCallBusyMarkup } from './retrieval-card';

type TimelineEvent = {
  type: string;
  offset_ms: number;
  payload?: Record<string, unknown>;
};

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

function peaksFromBuffer(buffer: AudioBuffer, count = 180): number[] {
  const channel = buffer.getChannelData(0);
  const size = Math.max(1, Math.floor(channel.length / count));
  const peaks: number[] = [];
  for (let index = 0; index < count; index += 1) {
    const start = index * size;
    let energy = 0;
    let used = 0;
    for (let sample = 0; sample < size && start + sample < channel.length; sample += 1) {
      const value = channel[start + sample] ?? 0;
      energy += value * value;
      used += 1;
    }
    peaks.push(Math.min(1, Math.sqrt(energy / Math.max(1, used)) * 4));
  }
  return peaks;
}

function speechOnsetMs(buffer: AudioBuffer, markedMs: number, earliestMs: number): number {
  const channel = buffer.getChannelData(0);
  const rate = buffer.sampleRate;
  if (!channel.length || rate <= 0) return Math.max(0, markedMs);
  const windowSamples = Math.max(1, Math.floor(rate * 0.03));
  const end = Math.max(0, Math.min(channel.length - 1, Math.floor((markedMs / 1000) * rate)));
  const earliest = Math.max(0, Math.min(end, Math.floor((Math.max(0, earliestMs) / 1000) * rate)));
  const rmsAt = (index: number): number => {
    const from = Math.max(0, index);
    const to = Math.min(channel.length, from + windowSamples);
    if (to <= from) return 0;
    let energy = 0;
    for (let sample = from; sample < to; sample += 1) {
      const value = channel[sample] ?? 0;
      energy += value * value;
    }
    return Math.sqrt(energy / (to - from));
  };
  const loud = 0.015;
  let cursor = Math.max(earliest, end - windowSamples);
  let sawSpeech = rmsAt(cursor) >= loud;
  if (!sawSpeech) {
    for (let index = cursor; index >= earliest; index -= windowSamples) {
      if (rmsAt(index) >= loud) {
        cursor = index;
        sawSpeech = true;
        break;
      }
      if (end - index > rate * 4) break;
    }
  }
  if (!sawSpeech) return Math.max(0, markedMs);
  let onset = cursor;
  let quietSamples = 0;
  for (let index = cursor; index >= earliest; index -= windowSamples) {
    if (rmsAt(index) >= loud) {
      onset = index;
      quietSamples = 0;
      continue;
    }
    quietSamples += windowSamples;
    if (quietSamples >= rate * 0.16) break;
  }
  return Math.min(markedMs, (onset / rate) * 1000);
}

function alignCustomerMessages(buffer: AudioBuffer, originMs: number, rows: HTMLElement[]): void {
  let earliest = 0;
  for (const item of rows) {
    const sessionMs = Number(item.dataset.at ?? 0) * 1000;
    const audioMs = Math.max(0, sessionMs - originMs);
    if (!item.classList.contains('customer')) {
      earliest = Math.max(earliest, audioMs);
      continue;
    }
    const onset = speechOnsetMs(buffer, audioMs, earliest);
    item.dataset.at = String((onset + originMs) / 1000);
    const time = item.querySelector('.message-time');
    if (time) time.textContent = formatTime(onset / 1000);
    earliest = Math.max(earliest, onset);
  }
}

export function bootCallReplay(apiUrl: string, token: string, callId: string): () => void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector<HTMLElement>('#conversationEmpty');
  const status = document.querySelector<HTMLElement>('#callConnectionStatus');
  const statusText = document.querySelector('#callConnectionStatusText');
  const waveShell = document.querySelector<HTMLElement>('#waveShell');
  const canvas = document.querySelector<HTMLCanvasElement>('#waveCanvas');
  const currentTimeEl = document.querySelector('#currentTime');
  const durationEl = document.querySelector('#duration');
  const playBtn = document.querySelector<HTMLButtonElement>('#playBtn');
  const restartBtn = document.querySelector<HTMLButtonElement>('#rewindBtn');
  if (!(conversation instanceof HTMLElement) || !(canvas instanceof HTMLCanvasElement) || !waveShell || !playBtn || !restartBtn) {
    return () => undefined;
  }
  bindDetailClicks(conversation);
    const scroller = document.getElementById('appContent');
    let scrollRest = 0;
    scroller?.addEventListener('scroll', () => {
      scroller.classList.add('is-scrolling');
      window.clearTimeout(scrollRest);
      scrollRest = window.setTimeout(() => scroller.classList.remove('is-scrolling'), 780);
    }, { passive: true });
  const emptyCopy = conversationEmpty?.innerHTML ?? '';
  if (conversationEmpty) conversationEmpty.innerHTML = toolCallBusyMarkup();
  if (status) status.hidden = false;
  if (statusText) statusText.textContent = 'Conectando…';

  const audio = new Audio();
  audio.preload = 'auto';
  let peaks: number[] = [0.04];
  let offsetMs = 0;
  let disposed = false;
  let previousMs = 0;
  const items: HTMLElement[] = [];
  const context = canvas.getContext('2d');

  function resize(): void {
    if (!context) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(Math.max(1, rect.width) * dpr);
    canvas.height = Math.round(Math.max(1, rect.height) * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawWave(progress: number): void {
    if (!context) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    context.clearRect(0, 0, width, height);
    const paint = (from: number, to: number, fillStyle: string) => {
      if (to <= from) return;
      context.beginPath();
      const baseline = height - 1;
      const span = Math.max(1, peaks.length - 1);
      const start = Math.floor(from * span);
      const end = Math.min(span, Math.ceil(to * span));
      context.moveTo((start / span) * width, baseline);
      for (let index = start; index <= end; index += 1) {
        const amplitude = 5 + Math.pow(peaks[index] ?? 0.04, 1.25) * height * 0.5;
        context.lineTo((index / span) * width, baseline - amplitude);
      }
      context.lineTo((end / span) * width, baseline);
      context.closePath();
      context.fillStyle = fillStyle;
      context.fill();
    };
    paint(0, 1, 'rgba(65,65,65,.14)');
    paint(0, progress, '#f7c974');
  }

  function paint(timelineMs: number, animate: boolean): void {
    const movingForward = timelineMs >= previousMs;
    let visible = false;
    for (const item of items) {
      const at = Number(item.dataset.at ?? 0) * 1000;
      const show = timelineMs >= at;
      if (show) {
        const crossed = animate && movingForward && previousMs < at && timelineMs >= at;
        item.classList.add('visible');
        if (crossed) {
          item.classList.remove('enter');
          void item.offsetWidth;
          item.classList.add('enter');
        }
        visible = true;
        if (crossed) {
          const scroller = document.getElementById('appContent');
          const follow = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 120;
          if (follow) window.setTimeout(() => item.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 180);
        }
        if (item.dataset.doneAt && timelineMs >= Number(item.dataset.doneAt)) {
          const tool = item.querySelector<HTMLElement>('.tool-call');
          if (tool && !tool.classList.contains('done')) {
            tool.classList.add('done');
            tool.setAttribute('aria-busy', 'false');
            const statusNode = tool.querySelector('.tool-status');
            if (statusNode) {
              statusNode.textContent = tool.dataset.finalStatus ?? 'Completado';
              statusNode.classList.remove('loading');
            }
            const loader = tool.querySelector<HTMLElement>('.loader');
            if (loader) window.setTimeout(() => { loader.style.display = 'none'; }, 420);
          }
        } else if (item.dataset.doneAt) {
          const tool = item.querySelector<HTMLElement>('.tool-call');
          tool?.classList.remove('done');
          tool?.setAttribute('aria-busy', 'true');
          const statusNode = tool?.querySelector('.tool-status');
          if (statusNode) {
            statusNode.textContent = tool?.dataset.busyStatus ?? 'Ejecutando';
            statusNode.classList.add('loading');
          }
          const loader = tool?.querySelector<HTMLElement>('.loader');
          if (loader) loader.style.display = '';
        }
      } else {
        item.classList.remove('visible', 'enter');
        const tool = item.querySelector('.tool-call');
        tool?.classList.remove('done');
        tool?.setAttribute('aria-busy', 'true');
        item.querySelector('.tool-status')?.classList.add('loading');
      }
    }
    if (conversationEmpty) conversationEmpty.hidden = visible;
    previousMs = timelineMs;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    const progress = duration > 0 ? audio.currentTime / duration : 0;
    waveShell.style.setProperty('--progress', `${progress * 100}%`);
    waveShell.classList.toggle('at-live-edge', duration > 0 && audio.currentTime >= duration - 0.15);
    waveShell.setAttribute('aria-valuemax', String(Math.round(duration)));
    waveShell.setAttribute('aria-valuenow', String(Math.round(audio.currentTime)));
    if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
    if (durationEl) durationEl.textContent = formatTime(duration);
    drawWave(progress);
  }

  function seek(seconds: number): void {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    audio.currentTime = Math.max(0, Math.min(duration, seconds));
    previousMs = Math.min(previousMs, audio.currentTime * 1000 + offsetMs);
    paint(audio.currentTime * 1000 + offsetMs, false);
  }

  function setPlaying(next: boolean): void {
    const icon = playBtn.querySelector('svg, i');
    icon?.remove();
    const node = document.createElement('i');
    node.dataset.lucide = next ? 'pause' : 'play';
    node.setAttribute('aria-hidden', 'true');
    playBtn.append(node);
    playBtn.setAttribute('aria-label', next ? 'Pausar llamada' : 'Reanudar llamada');
    const caption = playBtn.closest('.control-stack')?.querySelector('.control-caption');
    if (caption) caption.textContent = next ? 'Pausa' : 'Reanudar';
    lucideRefresh();
    if (next) void audio.play();
    else audio.pause();
  }

  function addItem(atMs: number, className: string, html: string, doneAtMs?: number): void {
    const row = document.createElement('div');
    row.className = `${className} timeline-item`;
    row.dataset.at = String(atMs / 1000);
    if (doneAtMs !== undefined) row.dataset.doneAt = String(doneAtMs);
    row.innerHTML = html;
    conversation.append(row);
    items.push(row);
  }

  function renderTimeline(events: TimelineEvent[], originMs: number): void {
    const tools = new Map<string, { start: number; payload: Record<string, unknown> }>();
    const clock = (offsetMs: number) => formatTime(Math.max(0, offsetMs - originMs) / 1000);
    if (!events.some((event) => event.type === 'lifecycle' && event.payload?.state === 'ACTIVE')) {
      addItem(0, 'system-event', `<span class="call-ended-label"><i data-lucide="phone" aria-hidden="true"></i><span>Llamada conectada</span></span>`);
    }
    events.forEach((event, index) => {
      const payload = event.payload ?? {};
      if (event.type === 'lifecycle' && payload.state === 'ACTIVE') {
        addItem(event.offset_ms, 'system-event', `<span class="call-ended-label"><i data-lucide="phone" aria-hidden="true"></i><span>Llamada conectada</span></span>`);
      }
      if (event.type === 'transcript.final' && payload.speaker !== 'agent') {
        const time = clock(event.offset_ms);
        addItem(
          event.offset_ms,
          'message-row customer',
          `<div class="message-wrap"><div class="message-meta"><span>Cliente</span><i data-lucide="user-round" aria-hidden="true"></i></div><div class="message complete">${escapeHtml(String(payload.text ?? ''))}<span class="message-time">${time}</span></div></div>`,
        );
      }
      if (event.type === 'transcript.final' && payload.speaker === 'agent') {
        const time = clock(event.offset_ms);
        addItem(
          event.offset_ms,
          'message-row agent',
          `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Agente</span></div><div class="message complete">${escapeHtml(String(payload.text ?? ''))}<span class="message-time">${time}</span></div></div>`,
        );
      }
      if (event.type === 'tool.started') {
        const id = String(payload.tool_call_id ?? payload.tool ?? index);
        tools.set(id, { start: event.offset_ms, payload });
      }
      if (event.type === 'tool.completed') {
        const rawId = String(payload.tool_call_id ?? payload.tool ?? index);
        const id = `tool-${rawId}`;
        const started = tools.get(rawId);
        const detail = toolDetailFromEvent(payload, started ? toolDetailFromEvent(started.payload) : undefined);
        addItem(
          started?.start ?? event.offset_ms,
          'tool-row',
          `<button type="button" class="tool-call" id="${escapeHtml(id)}" data-busy-status="${escapeHtml(String(started?.payload.status ?? 'Ejecutando'))}" data-final-status="${escapeHtml(String(payload.status ?? 'Completado'))}" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-busy="true"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(detail.name)}</div><div class="tool-status loading">${escapeHtml(String(started?.payload.status ?? 'Ejecutando'))}</div></div>${toolCallBusyMarkup()}<div class="done-mark" aria-hidden="true"><i data-lucide="check"></i></div></button>`,
          event.offset_ms,
        );
      }
      if (event.type === 'rag.completed') {
        const id = `rag-${index}`;
        const markup = createRetrievalCardMarkup(id, payload);
        if (markup) {
          addItem(event.offset_ms, 'tool-row', markup, event.offset_ms);
          completeRetrievalCard(id, payload);
        }
      }
      if (event.type === 'lifecycle' && payload.state === 'ENDED') {
        addItem(
          event.offset_ms,
          'system-event call-ended',
          `<span class="call-ended-label"><i data-lucide="phone-off" aria-hidden="true"></i><span>Llamada finalizada · ${clock(event.offset_ms)}</span></span>`,
        );
      }
    });
    lucideRefresh();
  }

  function onTime(): void {
    if (!disposed) paint(audio.currentTime * 1000 + offsetMs, true);
  }

  function seekFromPointer(clientX: number): void {
    const rect = waveShell.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    seek(ratio * (Number.isFinite(audio.duration) ? audio.duration : 0));
  }

  playBtn.addEventListener('click', () => setPlaying(audio.paused));
  restartBtn.addEventListener('click', () => seek(0));
  waveShell.addEventListener('pointerdown', (event) => {
    waveShell.classList.add('seeking');
    waveShell.setPointerCapture(event.pointerId);
    seekFromPointer(event.clientX);
  });
  waveShell.addEventListener('pointermove', (event) => {
    if (waveShell.classList.contains('seeking')) seekFromPointer(event.clientX);
  });
  waveShell.addEventListener('pointerup', () => waveShell.classList.remove('seeking'));
  waveShell.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft') seek(audio.currentTime - 5);
    if (event.key === 'ArrowRight') seek(audio.currentTime + 5);
    if (event.key === ' ') {
      event.preventDefault();
      setPlaying(audio.paused);
    }
  });
  audio.addEventListener('timeupdate', onTime);
  audio.addEventListener('seeked', onTime);
  audio.addEventListener('ended', () => setPlaying(false));
  const resizeObserver = new ResizeObserver(() => {
    resize();
    drawWave(Number.isFinite(audio.duration) && audio.duration > 0 ? audio.currentTime / audio.duration : 0);
  });
  resizeObserver.observe(canvas);
  resize();

  void (async () => {
    const headers = { Authorization: `Bearer ${token}` };
    const [callResponse, timelineResponse, recordingResponse] = await Promise.all([
      fetch(`${apiUrl}/calls/${callId}`, { headers }),
      fetch(`${apiUrl}/calls/${callId}/timeline`, { headers }),
      fetch(`${apiUrl}/calls/${callId}/recording`, { headers }),
    ]);
    if (disposed) return;
    if (!callResponse.ok || !timelineResponse.ok || !recordingResponse.ok) {
      if (statusText) statusText.textContent = 'No se pudo cargar la llamada';
      if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
      return;
    }
    const call = await callResponse.json() as { recording_offset_ms?: number };
    const timeline = await timelineResponse.json() as { events: TimelineEvent[] };
    const events = timeline.events ?? [];
    const active = events.find((event) => event.type === 'lifecycle' && event.payload?.state === 'ACTIVE');
    offsetMs = call.recording_offset_ms || active?.offset_ms || 0;
    renderTimeline(events, offsetMs);
    const blob = await recordingResponse.blob();
    if (disposed) return;
    audio.src = URL.createObjectURL(blob);
    const decoded = await new AudioContext().decodeAudioData(await blob.arrayBuffer());
    peaks = peaksFromBuffer(decoded);
    alignCustomerMessages(decoded, offsetMs, items);
    if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
    if (status) status.hidden = true;
    paint(offsetMs, false);
    setPlaying(true);
  })().catch(() => {
    if (statusText) statusText.textContent = 'No se pudo cargar la llamada';
    if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
  });

  return () => {
    disposed = true;
    audio.pause();
    if (audio.src) URL.revokeObjectURL(audio.src);
    resizeObserver.disconnect();
  };
}
