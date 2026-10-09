import {
  bindDetailClicks,
  mountSessionPanel,
  openDetail,
  patchSession,
  patchSessionFromAgentState,
  type TechnicalDetail,
  toolDetailFromEvent,
} from './detail-panel';
import { completeRetrievalCard, createRetrievalCardMarkup } from './retrieval-card';
import { eventMarkIcon, paintCallWave, resizeWave, type WaveMark } from './wave-mark';
import { applyCallAgentSignals, resetCallAgentSignals } from './agent-signals';
import { mountConversationScroll } from './conversation-scroll';

type TimelineEvent = {
  type?: string;
  offset_ms: number;
  payload?: Record<string, unknown>;
  id?: string;
  kind?: string;
  name?: string;
  span_name?: string;
  playback_ms?: number;
  duration_ms?: number;
  occurred_at?: string;
  turn_id?: string | null;
  status?: string | null;
  usage?: Record<string, number> | null;
  response?: Record<string, unknown> | null;
};

type DevReplayPayload = {
  call?: {
    recording_offset_ms?: number;
    conversation_id?: string;
    caller?: string;
    customer_name?: string;
    status?: string;
  };
  summary?: Record<string, unknown>;
  turns?: Array<Record<string, unknown>>;
  events?: TimelineEvent[];
  has_trace?: boolean;
};

type ReplayOptions = {
  dev?: boolean;
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

function levelSeries(channel: Float32Array, count: number, gain: number): number[] {
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
    peaks.push(Math.min(1, Math.sqrt(energy / Math.max(1, used)) * gain));
  }
  return peaks;
}

function peaksFromBuffer(buffer: AudioBuffer, count = 180): { customer: number[]; agent: number[] } {
  const customer = levelSeries(buffer.getChannelData(0), count, 4);
  const agent = buffer.numberOfChannels > 1
    ? levelSeries(buffer.getChannelData(1), count, 4)
    : Array.from({ length: count }, () => 0);
  return { customer, agent };
}

function monoPlaybackUrl(buffer: AudioBuffer): string {
  const customer = buffer.getChannelData(0);
  const agent = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : null;
  const header = 44;
  const bytes = new ArrayBuffer(header + customer.length * 2);
  const view = new DataView(bytes);
  const write = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  write(0, 'RIFF');
  view.setUint32(4, bytes.byteLength - 8, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, customer.length * 2, true);
  for (let index = 0; index < customer.length; index += 1) {
    const mixed = Math.max(-1, Math.min(1, (customer[index] ?? 0) + (agent?.[index] ?? 0)));
    view.setInt16(header + index * 2, mixed * 32767, true);
  }
  return URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
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
    if (item.classList.contains('channel-whatsapp')) continue;
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

export function bootCallReplay(
  apiUrl: string,
  token: string,
  callId: string,
  onReady?: () => void,
  onError?: (reason: string) => void,
  options: ReplayOptions = {},
): () => void {
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
    onError?.('La vista de la llamada no está disponible en esta página.');
    return () => undefined;
  }
  bindDetailClicks(conversation);
  mountSessionPanel();
  const scrollController = mountConversationScroll({
    scroller: document.getElementById('appContent'),
    conversation,
  });
  const emptyCopy = conversationEmpty?.innerHTML ?? '';
  if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
  if (status) status.hidden = false;
  if (statusText) statusText.textContent = 'Conectando…';

  const audio = new Audio();
  audio.preload = 'auto';
  let customerPeaks: number[] = [0.04];
  let agentPeaks: number[] = [0];
  let offsetMs = 0;
  let disposed = false;
  let readyNotified = false;
  let errorNotified = false;
  let previousMs = 0;
  const items: HTMLElement[] = [];
  let signalEvents: TimelineEvent[] = [];
  let finalSignalsFallback: Record<string, unknown> | null = null;
  const devMode = options.dev === true;
  const devDetails = new Map<string, TechnicalDetail>();
  let devDetailSequence = 0;

  const registerDevDetail = (detail: Omit<TechnicalDetail, 'kind'>): string => {
    const id = `dev-detail-${devDetailSequence += 1}`;
    devDetails.set(id, { kind: 'technical', ...detail });
    return id;
  };

  const devReaction = (id: string, icon: string, label: string, tone: 'error' | undefined = undefined, text = ''): string => (
    `<button class="dev-bubble-reaction${tone ? ` is-${tone}` : ''}" type="button" data-dev-reaction="${escapeHtml(id)}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><i data-lucide="${escapeHtml(icon)}" aria-hidden="true"></i>${text ? `<span>${escapeHtml(text)}</span>` : ''}</button>`
  );

  const onDevReaction = (event: Event): void => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest<HTMLElement>('[data-dev-reaction]');
    if (!button) return;
    const detail = devDetails.get(button.dataset.devReaction ?? '');
    if (detail) openDetail(detail, button.dataset.devReaction);
  };
  conversation.addEventListener('click', onDevReaction);

  const notifyReady = (): void => {
    if (disposed || readyNotified || errorNotified) return;
    readyNotified = true;
    onReady?.();
  };

  const notifyError = (reason: string): void => {
    if (disposed || readyNotified || errorNotified) return;
    errorNotified = true;
    onError?.(reason);
  };
  let visibleSignalCount = -1;
  const context = canvas.getContext('2d');

  function resize(): void {
    if (!context) return;
    resizeWave(canvas, context);
  }

  let playAnchor = 0;
  let playAnchorAt = performance.now();

  function shownProgress(): number {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    if (duration <= 0) return 0;
    if (audio.paused || waveShell.classList.contains('seeking')) return Math.min(1, Math.max(0, audio.currentTime / duration));
    const elapsed = (performance.now() - playAnchorAt) / 1000;
    return Math.min(1, Math.max(0, playAnchor + (elapsed * (audio.playbackRate || 1)) / duration));
  }

  function trackProgress(): void {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    const actual = duration > 0 ? Math.min(1, Math.max(0, audio.currentTime / duration)) : 0;
    const shown = shownProgress();
    const ahead = duration > 0 ? (shown - actual) * duration : 0;
    playAnchor = ahead > 0 && ahead < 0.35 && !waveShell.classList.contains('seeking') ? shown : actual;
    playAnchorAt = performance.now();
  }

  function drawWave(progress: number): void {
    if (!context) return;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    const marks: WaveMark[] = [];
    if (duration > 0) {
      for (const item of items) {
        const icon = eventMarkIcon(item);
        if (!icon) continue;
        const ratio = (Number(item.dataset.at ?? 0) * 1000 - offsetMs) / (duration * 1000);
        if (ratio < 0 || ratio > 1) continue;
        marks.push({ ratio, icon });
      }
    }
    paintCallWave(context, canvas, customerPeaks, agentPeaks, progress, marks);
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
          window.setTimeout(() => scrollController.follow(item), 180);
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
          }
        } else if (item.dataset.doneAt) {
          const tool = item.querySelector<HTMLElement>('.tool-call');
          tool?.classList.remove('done');
          tool?.setAttribute('aria-busy', 'true');
          const statusNode = tool?.querySelector('.tool-status');
          if (statusNode) {
            statusNode.textContent = tool?.dataset.busyStatus ?? 'Cargando…';
            statusNode.classList.add('loading');
          }
        }
      } else {
        item.classList.remove('visible', 'enter');
        const tool = item.querySelector('.tool-call');
        tool?.classList.remove('done');
        tool?.setAttribute('aria-busy', 'true');
        item.querySelector('.tool-status')?.classList.add('loading');
      }
    }
    const nextSignalCount = signalEvents.filter((event) => event.offset_ms <= timelineMs).length;
    if (nextSignalCount !== visibleSignalCount) {
      visibleSignalCount = nextSignalCount;
      resetCallAgentSignals();
      signalEvents.slice(0, nextSignalCount).forEach((event) => applyCallAgentSignals(event.payload));
    }
    if (conversationEmpty) conversationEmpty.hidden = visible;
    previousMs = timelineMs;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    trackProgress();
    waveShell.classList.toggle('at-live-edge', duration > 0 && audio.currentTime >= duration - 0.15);
    waveShell.setAttribute('aria-valuemax', String(Math.round(duration)));
    waveShell.setAttribute('aria-valuenow', String(Math.round(audio.currentTime)));
    if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
    if (durationEl) durationEl.textContent = formatTime(duration);
  }

  function seek(seconds: number): void {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    audio.currentTime = Math.max(0, Math.min(duration, seconds));
    previousMs = Math.min(previousMs, audio.currentTime * 1000 + offsetMs);
    paint(audio.currentTime * 1000 + offsetMs, false);
    scrollController.follow();
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
    if (next) void audio.play().catch(() => { if (!disposed) setPlaying(false); });
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

  function renderDevMetrics(payload: DevReplayPayload): void {
    const root = document.getElementById('devReplayMetrics');
    if (!(root instanceof HTMLElement)) return;
    const summaryRoot = document.getElementById('devReplaySummary');
    const summary = payload.summary ?? {};
    const formatCount = (value: unknown): string => {
      const count = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
      return new Intl.NumberFormat('es').format(count);
    };
    const formatCost = (value: unknown): string => {
      if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '';
      return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
    };
    const formatDuration = (value: unknown): string => {
      const ms = typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0;
      return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
    };
    const firstTurn = payload.turns?.[0] ?? {};
    const model = [firstTurn.provider, firstTurn.model]
      .filter((value): value is string => typeof value === 'string' && value.length > 0)
      .join(' / ');
    const facts: Array<[string, string]> = [];
    const addCount = (label: string, value: unknown): void => {
      if (typeof value === 'number' && Number.isFinite(value) && value > 0) facts.push([label, formatCount(value)]);
    };
    addCount('Tokens', summary.total_tokens);
    addCount('Entrada', summary.prompt_tokens);
    addCount('Salida', summary.completion_tokens);
    addCount('LLM', summary.llm_calls);
    addCount('Tools', summary.tools ?? (payload.events ?? []).filter((event) => event.kind === 'tool' || event.name === 'tool.completed').length);
    const formattedCost = formatCost(summary.cost_usd);
    if (formattedCost) facts.push(['Costo', formattedCost]);
    if (typeof summary.duration_ms === 'number' && Number.isFinite(summary.duration_ms) && summary.duration_ms > 0) {
      facts.push(['Cómputo', formatDuration(summary.duration_ms)]);
    }
    if (model) facts.push(['Modelo', model]);
    root.replaceChildren();
    facts.forEach(([label, value]) => {
      const item = document.createElement('div');
      item.className = 'dev-replay-metric';
      const valueNode = document.createElement('strong');
      valueNode.textContent = value;
      const labelNode = document.createElement('span');
      labelNode.textContent = label;
      item.append(valueNode, labelNode);
      root.append(item);
    });
    root.hidden = facts.length === 0;
    if (summaryRoot) {
      summaryRoot.hidden = facts.length === 0;
      const hint = summaryRoot.querySelector<HTMLElement>('.dev-replay-summary__hint');
      if (hint) hint.textContent = 'Los eventos siguen el playhead';
    }
  }

  function renderDevTimeline(payload: DevReplayPayload, originMs: number): void {
    const events = payload.events ?? [];
    const turns = payload.turns ?? [];
    signalEvents = events.filter((event) => event.name === 'agent.signals' || event.type === 'agent.signals');
    visibleSignalCount = -1;
    let fallbackTurnIndex = 0;
    let whatsappSeparatorAdded = false;
    const clock = (playbackMs: number): string => formatTime(Math.max(0, playbackMs) / 1000);
    const record = (value: unknown): Record<string, unknown> => (
      value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
    );
    const turnsById = new Map(turns.map((turn) => [String(turn.id ?? ''), turn]).filter(([id]) => id));
    const turnForTranscript = (event: TimelineEvent): Record<string, unknown> | undefined => {
      const explicit = event.turn_id ? turnsById.get(String(event.turn_id)) : undefined;
      if (explicit) return explicit;
      const occurredAt = Date.parse(String(event.occurred_at ?? ''));
      if (Number.isFinite(occurredAt)) {
        const prior = turns
          .map((turn) => ({ turn, startedAt: Date.parse(String(turn.started_at ?? '')) }))
          .filter((item) => Number.isFinite(item.startedAt) && item.startedAt <= occurredAt)
          .sort((left, right) => left.startedAt - right.startedAt);
        return prior.at(-1)?.turn;
      }
      // Last-resort support for very old traces without timestamps. This path
      // is intentionally unreachable for current replay events.
      const fallback = turns[fallbackTurnIndex];
      fallbackTurnIndex += 1;
      return fallback;
    };
    const turnResponse = (turn: Record<string, unknown> | undefined, transcriptText: string): string => {
      const answer = turn?.answer;
      if (typeof answer === 'string' && answer.trim()) return answer;
      const data = record(turn?.data);
      const spans = Array.isArray(data.spans) ? data.spans : [];
      for (const span of spans.slice().reverse()) {
        const spanRecord = record(span);
        if (spanRecord.name !== 'llm.request') continue;
        const attributes = record(spanRecord.attributes);
        const text = attributes.text;
        if (typeof text === 'string' && text.trim()) return text;
      }
      return transcriptText;
    };
    const responseFromAttributes = (attributes: Record<string, unknown>, fallbackText = ''): Record<string, unknown> => {
      const explicit = record(attributes.response);
      if (Object.keys(explicit).length) return explicit;
      const response: Record<string, unknown> = {};
      if (typeof attributes.text === 'string' && attributes.text.trim()) response.text = attributes.text;
      if (Array.isArray(attributes.tool_calls) && attributes.tool_calls.length) response.tool_calls = attributes.tool_calls;
      const usage = record(attributes.usage);
      if (Object.keys(usage).length) response.usage = usage;
      ['prompt_tokens', 'completion_tokens', 'total_tokens'].forEach((key) => {
        if (typeof attributes[key] === 'number') {
          const current = record(response.usage);
          response.usage = { ...current, [key]: attributes[key] };
        }
      });
      if (!Object.keys(response).length && fallbackText.trim()) response.text = fallbackText;
      return response;
    };
    const withResponseFallback = (response: Record<string, unknown>, fallbackText: string): Record<string, unknown> => {
      const text = response.text;
      const hasToolCalls = Array.isArray(response.tool_calls) && response.tool_calls.length > 0;
      if (fallbackText.trim() && (!((typeof text === 'string' && text.trim()) || hasToolCalls))) {
        return { ...response, text: fallbackText };
      }
      return response;
    };
    const turnResponseJson = (turn: Record<string, unknown> | undefined, fallbackText: string): Record<string, unknown> => {
      const data = record(turn?.data);
      const spans = Array.isArray(data.spans) ? data.spans : [];
      for (const span of spans.slice().reverse()) {
        const spanRecord = record(span);
        if (spanRecord.name !== 'llm.request') continue;
        const response = responseFromAttributes(record(spanRecord.attributes));
        if (Object.keys(response).length) return withResponseFallback(response, fallbackText);
      }
      const response = record(data.response);
      return Object.keys(response).length ? response : { text: fallbackText };
    };
    const eventResponse = (event: TimelineEvent, fallbackText: string): Record<string, unknown> => {
      const direct = record(event.response);
      if (Object.keys(direct).length) return withResponseFallback(direct, fallbackText);
      return responseFromAttributes(record(event.payload?.attributes), fallbackText);
    };
    const technicalKind = (event: TimelineEvent): 'llm' | 'tool' | 'rag' | 'error' | null => {
      if (event.kind === 'llm' || event.name === 'llm.request') return 'llm';
      if (event.kind === 'tool' || event.name === 'tool.completed') return 'tool';
      if (event.kind === 'rag' || event.name === 'rag.completed') return 'rag';
      if (event.kind === 'error' || event.name === 'agent.error' || event.name === 'provider.error') return 'error';
      return null;
    };
    const agentTranscriptEvents = events.filter((event) => {
      const isTranscript = event.kind === 'transcript' || (event.type ?? '').startsWith('transcript.');
      return isTranscript && String(event.payload?.speaker ?? '') === 'agent';
    });
    const transcriptTargets = agentTranscriptEvents.length
      ? agentTranscriptEvents
      : events.filter((event) => event.kind === 'transcript' || (event.type ?? '').startsWith('transcript.'));
    const transcriptByTurn = new Map<string, TimelineEvent>();
    transcriptTargets.forEach((event) => {
      if (event.turn_id) transcriptByTurn.set(String(event.turn_id), event);
    });
    const technicalByTranscript = new Map<TimelineEvent, TimelineEvent[]>();
    events.forEach((event) => {
      const kind = technicalKind(event);
      if (!kind) return;
      const explicitTarget = event.turn_id ? transcriptByTurn.get(String(event.turn_id)) : undefined;
      const target = explicitTarget ?? transcriptTargets.find((candidate) => Number(candidate.playback_ms ?? candidate.offset_ms) >= Number(event.playback_ms ?? event.offset_ms))
        ?? transcriptTargets.at(-1);
      if (!target) return;
      const related = technicalByTranscript.get(target) ?? [];
      related.push(event);
      technicalByTranscript.set(target, related);
    });
    events.forEach((event) => {
      const playbackMs = Math.max(0, Number(event.playback_ms ?? Math.max(0, event.offset_ms - originMs)));
      const atMs = originMs + playbackMs;
      const eventPayload = event.payload ?? {};
      const channel = eventPayload.channel === 'whatsapp' ? ' channel-whatsapp' : '';
      const speaker = String(eventPayload.speaker ?? '');
      const isTranscript = event.kind === 'transcript' || (event.type ?? '').startsWith('transcript.');
      if (isTranscript) {
        const text = String(eventPayload.text ?? '');
        const customer = speaker !== 'agent';
        const turn = !customer ? turnForTranscript(event) : undefined;
        const turnAnswer = !customer ? turnResponse(turn, text) : text;
        const displayText = text || (!customer ? turnAnswer : '');
        if (!displayText) return;
        const turnUsage = record(turn?.usage);
        const rawTurnCost = turn?.cost_usd;
        const turnTechnicalFacts: string[] = [];
        if (typeof turn?.provider === 'string' && turn.provider) turnTechnicalFacts.push(turn.provider);
        if (typeof turn?.model === 'string' && turn.model) turnTechnicalFacts.push(turn.model);
        if (turnUsage && typeof turnUsage.total_tokens === 'number' && turnUsage.total_tokens > 0) {
          turnTechnicalFacts.push(`${turnUsage.total_tokens} tokens`);
        }
        if (typeof rawTurnCost === 'number' && Number.isFinite(rawTurnCost) && rawTurnCost > 0) {
          turnTechnicalFacts.push(rawTurnCost < 0.01 ? `$${rawTurnCost.toFixed(4)}` : `$${rawTurnCost.toFixed(2)}`);
        }
        if (typeof turn?.duration_ms === 'number' && turn.duration_ms > 0) turnTechnicalFacts.push(`${turn.duration_ms} ms`);
        const turnReactions: string[] = [];
        const rawTurnData = record(turn?.data);
        const storedAnswer = rawTurnData.answer;
        const turnData = turnAnswer && (typeof storedAnswer !== 'string' || !storedAnswer.trim())
          ? { ...rawTurnData, answer: turnAnswer }
          : rawTurnData;
        const responseJson = !customer ? turnResponseJson(turn, turnAnswer || displayText) : {};
        const transcriptTechnicalEvents = technicalByTranscript.get(event) ?? [];
        const hasTechnicalData = Boolean(turn || transcriptTechnicalEvents.length);
        const responseSections = [
          ...(turnTechnicalFacts.length ? [{ title: 'Resumen', value: turnTechnicalFacts.join(' · ') }] : []),
          ...(Object.keys(responseJson).length ? [{ title: 'Respuesta JSON', value: responseJson }] : []),
          ...(Object.keys(turnData).length ? [{ title: 'Traza del turno', value: turnData }] : []),
        ];
        const responseDetailId = !customer && hasTechnicalData
          ? registerDevDetail({
              title: 'Respuesta del agente',
              sections: responseSections,
            })
          : '';
        const relatedEvents = [
          ...(turn && transcriptByTurn.get(String(turn.id)) === event
            ? events.filter((candidate) => candidate.turn_id === turn.id && technicalKind(candidate))
            : []),
          ...transcriptTechnicalEvents,
        ];
        const uniqueRelated = Array.from(new Map(relatedEvents.map((related, index) => [related.id ?? `${related.name ?? 'event'}-${index}-${related.offset_ms}`, related])).values());
        const relatedGroups: TimelineEvent[][] = [];
        uniqueRelated.forEach((related) => {
          const kind = technicalKind(related);
          if (kind !== 'error') {
            relatedGroups.push([related]);
            return;
          }
          const attempt = record(related.payload?.attributes).attempt;
          const matchingGroup = typeof attempt === 'number' || typeof attempt === 'string'
            ? relatedGroups.find((group) => group.some((candidate) => {
                if (technicalKind(candidate) !== 'llm') return false;
                const candidateAttempt = record(candidate.payload?.attributes).attempt;
                return String(candidateAttempt ?? '') === String(attempt);
              }))
            : undefined;
          if (matchingGroup) matchingGroup.push(related);
          else relatedGroups.push([related]);
        });
        let hasLlmReaction = false;
        relatedGroups.forEach((group) => {
          const related = group[0];
          const kind = technicalKind(related);
          if (!kind) return;
          const modelEvent = group.find((candidate) => technicalKind(candidate) === 'llm');
          const errorEvent = group.find((candidate) => technicalKind(candidate) === 'error');
          const failedModel = Boolean(modelEvent && errorEvent);
          if (modelEvent) hasLlmReaction = true;
          const detailKind = modelEvent ? 'llm' : kind;
          const toolName = String(related.name ?? record(related.payload?.attributes).tool ?? related.payload?.tool ?? '').trim();
          const detailSections = [
            ...(modelEvent?.usage ? [{ title: 'Uso', value: modelEvent.usage }] : []),
            ...(modelEvent?.status ? [{ title: 'Estado', value: modelEvent.status }] : []),
            ...(modelEvent?.duration_ms ? [{ title: 'Duración', value: `${modelEvent.duration_ms} ms` }] : []),
            ...(detailKind === 'llm' ? [{ title: 'Respuesta JSON', value: eventResponse(modelEvent ?? related, failedModel ? '' : turnAnswer || displayText) }] : []),
            ...(detailKind === 'llm' && Object.keys(turnData).length ? [{ title: 'Traza del turno', value: turnData }] : []),
            ...(errorEvent ? [{ title: 'Error', value: errorEvent.payload ?? {} }] : []),
            { title: 'Payload', value: modelEvent?.payload ?? related.payload ?? {} },
          ];
          const relatedId = registerDevDetail({
            title: failedModel ? 'Modelo · error' : detailKind === 'tool' ? `Tool ${toolName || 'usada'}` : detailKind === 'rag' ? 'RAG' : detailKind === 'error' ? 'Error del provider' : 'Ejecución del modelo',
            sections: detailSections,
          });
          const isError = failedModel || detailKind === 'error';
          const pillText = detailKind === 'tool' ? `Tool${toolName ? ` · ${toolName}` : ''}` : detailKind === 'rag' ? 'RAG' : '';
          turnReactions.push(devReaction(
            relatedId,
            detailKind === 'tool' ? 'wrench' : detailKind === 'rag' ? 'library' : isError ? 'triangle-alert' : 'cpu',
            failedModel ? 'Abrir llamada al modelo y error' : detailKind === 'tool' ? `Abrir tool${toolName ? ` ${toolName}` : ''}` : detailKind === 'rag' ? 'Abrir RAG' : isError ? 'Abrir error' : 'Abrir modelo',
            isError ? 'error' : undefined,
            pillText,
          ));
        });
        if (!customer && responseDetailId && !hasLlmReaction) {
          turnReactions.unshift(devReaction(responseDetailId, 'cpu', 'Abrir ejecución del modelo'));
        }
        const technical = !customer && turnTechnicalFacts.length
          ? `<div class="dev-replay-message-tech">${escapeHtml(turnTechnicalFacts.join(' · '))}</div>`
          : '';
        const reactions = turnReactions.length
          ? `<span class="dev-bubble-reactions">${turnReactions.map((reaction, reactionIndex) => `${reactionIndex ? '<span class="dev-reaction-arrow" aria-hidden="true"><i data-lucide="arrow-right"></i></span>' : ''}${reaction}`).join('')}</span>`
          : '';
        addItem(
          atMs,
          `message-row ${customer ? 'customer' : 'agent'}${channel}`,
          `<div class="message-wrap"><div class="message-meta">${customer ? '<span>Cliente</span><i data-lucide="user-round" aria-hidden="true"></i>' : '<i data-lucide="headset" aria-hidden="true"></i><span>Wane</span>'}</div><div class="message complete">${escapeHtml(displayText)}<span class="message-time">${clock(playbackMs)}</span>${reactions}</div>${technical}</div>`,
        );
        return;
      }
      if (technicalKind(event)) return;
      if (event.kind === 'whatsapp') {
        const customer = String(eventPayload.role ?? '') !== 'assistant';
        const text = String(eventPayload.content ?? '');
        if (!text) return;
        if (!whatsappSeparatorAdded) {
          whatsappSeparatorAdded = true;
          addItem(atMs, 'system-event dev-replay-whatsapp-divider', '<span class="call-ended-label"><i data-lucide="message-circle" aria-hidden="true"></i><span>Continuación por WhatsApp</span></span>');
        }
        let reactions = '';
        if (!customer) {
          const detailId = registerDevDetail({
            title: 'Respuesta por WhatsApp',
            sections: [
              { title: 'Respuesta completa', value: text },
              { title: 'Payload', value: eventPayload },
            ],
          });
          reactions = `<span class="dev-bubble-reactions">${devReaction(detailId, 'cpu', 'Abrir respuesta de WhatsApp')}</span>`;
        }
        addItem(
          atMs,
          `message-row ${customer ? 'customer' : 'agent'} channel-whatsapp`,
          `<div class="message-wrap"><div class="message-meta">${customer ? '<span>Cliente · WhatsApp</span><i data-lucide="message-circle" aria-hidden="true"></i>' : '<i data-lucide="message-circle" aria-hidden="true"></i><span>Wane · WhatsApp</span>'}</div><div class="message complete">${escapeHtml(text)}<span class="message-time">${escapeHtml(new Date(String(event.occurred_at ?? '')).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }))}</span>${reactions}</div></div>`,
        );
        return;
      }
      if (event.kind === 'system') {
        if (!whatsappSeparatorAdded) {
          whatsappSeparatorAdded = true;
          addItem(atMs, 'system-event dev-replay-whatsapp-divider', '<span class="call-ended-label"><i data-lucide="message-circle" aria-hidden="true"></i><span>Continuación por WhatsApp</span></span>');
        }
        addItem(atMs, 'system-event', `<span class="call-ended-label"><i data-lucide="message-circle" aria-hidden="true"></i><span>${escapeHtml(String(eventPayload.content ?? 'Continuación de conversación'))}</span></span>`);
        return;
      }
      if (technicalKind(event)) return;
      if (event.name === 'lifecycle' || event.type === 'lifecycle') {
        const state = String(eventPayload.state ?? '').toLowerCase();
        addItem(atMs, 'system-event', `<span class="call-ended-label"><i data-lucide="${state === 'ended' ? 'phone-off' : 'phone'}" aria-hidden="true"></i><span>${state === 'ended' ? 'Llamada finalizada' : 'Llamada conectada'} · ${clock(playbackMs)}</span></span>`);
      }
    });
    lucideRefresh();
  }

  function renderTimeline(events: TimelineEvent[], originMs: number): void {
    if (devMode) return;
    signalEvents = events.filter((event) => event.type === 'agent.signals');
    visibleSignalCount = -1;
    const tools = new Map<string, { start: number; payload: Record<string, unknown> }>();
    const clock = (offsetMs: number) => formatTime(Math.max(0, offsetMs - originMs) / 1000);
    if (!events.some((event) => event.type === 'lifecycle' && event.payload?.state === 'ACTIVE')) {
      addItem(originMs, 'system-event', `<span class="call-ended-label"><i data-lucide="phone" aria-hidden="true"></i><span>Llamada conectada</span></span>`);
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
          `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Wane</span></div><div class="message complete">${escapeHtml(String(payload.text ?? ''))}<span class="message-time">${time}</span></div></div>`,
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
          `<button type="button" class="tool-call" id="${escapeHtml(id)}" data-busy-status="Cargando…" data-final-status="${escapeHtml(String(payload.status ?? 'Completado'))}" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-busy="true"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(detail.name)}</div><div class="tool-status loading">Cargando…</div></div><div class="done-mark" aria-hidden="true"><i data-lucide="check"></i></div></button>`,
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
  audio.addEventListener('ended', () => {
    setPlaying(false);
    if (Number.isFinite(audio.duration)) paint(offsetMs + audio.duration * 1000, true);
  });
  const resizeObserver = new ResizeObserver(() => resize());
  resizeObserver.observe(canvas);
  resize();
  const tickWave = () => {
    if (disposed) return;
    const progress = shownProgress();
    drawWave(progress);
    waveShell.style.setProperty('--progress', `${progress * 100}%`);
    waveFrame = window.requestAnimationFrame(tickWave);
  };
  let waveFrame = window.requestAnimationFrame(tickWave);

  void (async () => {
    const headers = { Authorization: `Bearer ${token}` };
    let loadStage = 'los datos de la llamada';
    const [callResponse, timelineResponse, recordingResponse] = await Promise.all([
      fetch(devMode ? `${apiUrl}/dev/calls/${callId}/replay` : `${apiUrl}/calls/${callId}`, { headers }),
      devMode ? Promise.resolve(null) : fetch(`${apiUrl}/calls/${callId}/timeline`, { headers }),
      fetch(`${apiUrl}/calls/${callId}/recording`, { headers }),
    ]);
    if (disposed) return;
    if (!callResponse.ok || (timelineResponse && !timelineResponse.ok) || !recordingResponse.ok) {
      const failed = !callResponse.ok
        ? callResponse
        : timelineResponse && !timelineResponse.ok ? timelineResponse : recordingResponse;
      const label = failed === callResponse
        ? devMode ? 'La repetición técnica solicitada' : 'La llamada solicitada'
        : failed === timelineResponse ? 'La línea de tiempo de la llamada' : 'La grabación de la llamada';
      const reason = failed.status === 404
        ? `${label} no existe o ya no está disponible.`
        : failed.status === 401 || failed.status === 403
          ? `No tienes permiso para consultar ${label.toLowerCase()}.`
          : `${label} respondió con un error del servidor (${failed.status}).`;
      if (statusText) statusText.textContent = reason;
      if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
      notifyError(reason);
      return;
    }
    loadStage = 'los datos de la llamada';
    const devPayload = devMode ? await callResponse.json() as DevReplayPayload : null;
    const call = (devMode ? devPayload?.call ?? {} : await callResponse.json()) as {
      recording_offset_ms?: number;
      conversation_id?: string;
      caller?: string;
      customer_name?: string;
      status?: string;
    };
    patchSession({
      name: call.customer_name ?? '',
      phone: call.caller ?? '',
      status: devMode ? '' : call.status === 'active' ? 'En vivo' : 'Llamada finalizada',
    });
    loadStage = 'la línea de tiempo de la llamada';
    const timeline = timelineResponse ? await timelineResponse.json() as { events: TimelineEvent[] } : null;
    const events = devPayload?.events ?? timeline?.events ?? [];
    if (call.conversation_id) {
      const stateResponse = await fetch(`${apiUrl}/conversations/${call.conversation_id}/agent-state`, { headers });
      if (stateResponse.ok) {
        const state = await stateResponse.json() as { signals?: Record<string, unknown> };
        patchSessionFromAgentState(state);
        if (state.signals && Object.keys(state.signals).length) {
          finalSignalsFallback = { signals: state.signals };
        }
      }
    }
    const active = events.find((event) => event.type === 'lifecycle' && event.payload?.state === 'ACTIVE');
    offsetMs = call.recording_offset_ms || active?.offset_ms || 0;
    if (devMode) {
      renderDevMetrics(devPayload ?? {});
      renderDevTimeline(devPayload ?? {}, offsetMs);
    } else {
      renderTimeline(events, offsetMs);
    }
    loadStage = 'la grabación de la llamada';
    const blob = await recordingResponse.blob();
    if (disposed) return;
    const bytes = await blob.arrayBuffer();
    if (disposed) return;
    let decoded: AudioBuffer | null = null;
    for (let attempt = 0; attempt < 3 && !decoded; attempt += 1) {
      const decodeContext = new AudioContext();
      try {
        await decodeContext.resume().catch(() => undefined);
        decoded = await decodeContext.decodeAudioData(bytes.slice(0));
      } catch (error) {
        if (attempt === 2) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, 150));
      } finally {
        await decodeContext.close().catch(() => undefined);
      }
    }
    if (!decoded || disposed) return;
    if (!signalEvents.length && finalSignalsFallback) {
      // Historical calls only have the final snapshot, so reveal it at the
      // end instead of presenting it as if it were known from the beginning.
      signalEvents = [{
        type: 'agent.signals',
        offset_ms: offsetMs + decoded.duration * 1000,
        payload: finalSignalsFallback,
      }];
      visibleSignalCount = -1;
    }
    const wave = peaksFromBuffer(decoded);
    customerPeaks = wave.customer;
    agentPeaks = wave.agent;
    audio.src = decoded.numberOfChannels > 1 ? monoPlaybackUrl(decoded) : URL.createObjectURL(blob);
    alignCustomerMessages(decoded, offsetMs, items);
    const endedItem = items.find((item) => item.classList.contains('call-ended'));
    if (endedItem) endedItem.dataset.at = String(offsetMs / 1000 + decoded.duration);
    if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
    if (status) status.hidden = true;
    await new Promise<void>((resolve) => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        resolve();
        return;
      }
      const done = () => resolve();
      audio.addEventListener('loadedmetadata', done, { once: true });
      audio.addEventListener('error', done, { once: true });
    });
    if (disposed) return;
    notifyReady();
    resize();
    paint(offsetMs, false);
    drawWave(shownProgress());
    setPlaying(true);
  })().catch((error: unknown) => {
    const reason = error instanceof DOMException && error.name === 'EncodingError'
      ? 'La grabación llegó en un formato que el navegador no puede reproducir.'
      : `No se pudo cargar ${loadStage}. Revisa la conexión con el servidor e inténtalo de nuevo.`;
    if (statusText) statusText.textContent = reason;
    if (conversationEmpty) conversationEmpty.innerHTML = emptyCopy;
    notifyError(reason);
  });

  return () => {
    disposed = true;
    window.cancelAnimationFrame(waveFrame);
    audio.pause();
    if (audio.src) URL.revokeObjectURL(audio.src);
    resizeObserver.disconnect();
    scrollController.dispose();
    conversation.removeEventListener('click', onDevReaction);
    devDetails.clear();
  };
}
