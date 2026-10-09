import { AudioCaptureAdapter } from '../infrastructure/audio-capture-adapter';
import { backendErrorFromResponse, backendMessage, errorMessage } from '../infrastructure/backend-error';
import { PcmAudioQueue } from '../infrastructure/pcm-audio-queue';
import { accessTokenKey, conversationIdKey, redirectToLogin } from '../../auth/session-guard';
import { completeRetrievalCard, createRetrievalCardMarkup, shouldRenderRetrieval, toolCallBusyMarkup } from './retrieval-card';
import { bindDetailClicks, mountSessionPanel, patchSession, patchSessionFromAgentState, readDetail, refreshOpenDetail, toolDetailFromEvent, writeDetail } from './detail-panel';
import { showToast } from '../infrastructure/toast';
import { applyCallAgentSignals, resetCallAgentSignals } from './agent-signals';
import { mountConversationScroll, type ConversationScrollController } from './conversation-scroll';

type CallMonitorAudio = {
  setLevels: (customer: number, agent: number) => void;
  connectAnalyser: (analyser: AnalyserNode, sampleRate?: number) => void;
  connectPlaybackAnalyser?: (analyser: AnalyserNode, sampleRate?: number) => void;
  disconnectPlaybackAnalyser?: () => void;
  disconnectAnalyser: () => void;
  setPlaying?: (next: boolean) => void;
  resetLiveWave?: () => void;
};

function monitor(): CallMonitorAudio | undefined {
  return (window as Window & { callMonitorAudio?: CallMonitorAudio }).callMonitorAudio;
}

function lucideRefresh(): void {
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

async function waitForMonitor(timeoutMs = 4000): Promise<CallMonitorAudio> {
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    const api = monitor();
    if (api) return api;
    await new Promise((resolve) => window.setTimeout(resolve, 20));
  }
  throw new Error('El espectrograma del monitor no está listo.');
}

function stealButton(id: string): HTMLButtonElement | null {
  const original = document.getElementById(id);
  if (!(original instanceof HTMLButtonElement)) return null;
  const clone = original.cloneNode(true) as HTMLButtonElement;
  original.replaceWith(clone);
  return clone;
}

function setControl(button: HTMLButtonElement, icon: string, caption: string | null, label: string): void {
  button.setAttribute('aria-label', label);
  button.setAttribute('title', label);
  const stack = button.closest('.control-stack');
  const cap = stack?.querySelector('.control-caption');
  if (cap && caption) cap.textContent = caption;
  const existing = button.querySelector('svg, i');
  if (existing) existing.remove();
  const node = document.createElement('i');
  node.setAttribute('data-lucide', icon);
  node.setAttribute('aria-hidden', 'true');
  button.append(node);
}

export function bootLiveMonitor(apiUrl: string, token?: string, conversationId?: string, autoStart = false): () => void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector('#conversationEmpty');
  if (!conversation) {
    return () => undefined;
  }
  if (conversation instanceof HTMLElement && conversation.dataset.liveBooted === '1') return () => undefined;
  if (conversation instanceof HTMLElement) conversation.dataset.liveBooted = '1';
  bindDetailClicks(conversation);
  mountSessionPanel();

  const restartBtn = stealButton('rewindBtn');
  const callBtn = stealButton('startBtn');
  const pauseBtn = stealButton('playBtn');
  if (!callBtn || !restartBtn || !pauseBtn) {
    return () => undefined;
  }
  const scrollController: ConversationScrollController = mountConversationScroll({
    scroller: document.getElementById('appContent'),
    conversation: conversation instanceof HTMLElement ? conversation : null,
  });

  setControl(restartBtn, 'rotate-ccw', 'Reiniciar', 'Reiniciar llamada');
  setControl(callBtn, 'phone', 'Llamar', 'Empezar llamada');
  setControl(pauseBtn, 'pause', 'Pausa', 'Pausar llamada');
  lucideRefresh();

  const capture = new AudioCaptureAdapter(`${apiUrl}/transcribe`);
  const pcm = new PcmAudioQueue();
  const socketUrl = `${apiUrl.replace(/^http/, 'ws')}/ws/call`;
  const initialAuthToken = typeof token === 'string' ? token.trim() : '';
  const currentAuthToken = (): string => sessionStorage.getItem(accessTokenKey)?.trim() || initialAuthToken;
  let attachedConversationId = typeof conversationId === 'string' ? conversationId.trim() : '';
  if (currentAuthToken() && attachedConversationId) {
    void fetch(`${apiUrl}/conversations/${attachedConversationId}/agent-state`, {
      headers: { Authorization: `Bearer ${currentAuthToken()}` },
    }).then(async (response) => {
      if (response.ok) patchSessionFromAgentState(await response.json());
    }).catch(() => undefined);
  }
  const pendingTools = new Map<string, string>();
  let pendingRetrievalId: string | null = null;

  let socket: WebSocket | null = null;
  let connected = false;
  let live = false;
  let paused = false;
  let processing = false;
  let pcmReady = false;
  let agentBubble: HTMLElement | null = null;
  let startedAt = performance.now();
  let closing = false;
  let waveApi: CallMonitorAudio | null = null;
  let customerBubble: HTMLElement | null = null;
  let customerShown = '';
  let ignoreTts = false;
  let waveFromAnalyser = false;
  let ttsRate = 24000;
  let bargeHits = 0;
  let bargeArmedAt = 0;
  let bargePending = false;
  let noiseFloor = 0.08;
  let reconnectTimer = 0;
  let connectionHideTimer = 0;
  let reconnectAttempts = 0;
  let disposed = false;

  function setConnectionStatus(state: 'connecting' | 'reconnecting' | 'connected' | 'error' | 'hidden', message = ''): void {
    const root = document.getElementById('callConnectionStatus');
    const text = document.getElementById('callConnectionStatusText');
    if (!(root instanceof HTMLElement) || !(text instanceof HTMLElement)) return;
    window.clearTimeout(connectionHideTimer);
    root.classList.toggle('is-connected', state === 'connected');
    root.classList.toggle('is-error', state === 'error');
    root.hidden = state === 'hidden';
    if (message) text.textContent = message;
  }

  function scheduleReconnect(): void {
    if (disposed || closing || !live || paused || reconnectTimer) return;
    reconnectAttempts += 1;
    const delay = Math.min(8000, 700 * 2 ** Math.min(reconnectAttempts - 1, 4));
    setConnectionStatus('reconnecting', `Reconectando… ${Math.ceil(delay / 1000)}s`);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = 0;
      if (!disposed && live && !paused) {
        setConnectionStatus('connecting', 'Conectando de nuevo…');
        bindSocket();
      }
    }, delay);
  }

  function stamp(): string {
    return formatTime((performance.now() - startedAt) / 1000);
  }

  function setEmpty(hidden: boolean): void {
    if (conversationEmpty instanceof HTMLElement) conversationEmpty.hidden = hidden;
  }

  function syncControls(): void {
    callBtn.disabled = live && !paused;
    pauseBtn.disabled = !live || paused;
    restartBtn.disabled = false;
    setControl(callBtn, 'phone', paused ? 'Reanudar' : 'Llamar', paused ? 'Reanudar llamada' : live ? 'Llamada en curso' : 'Empezar llamada');
    lucideRefresh();
  }

  function appendRow(kind: string, html: string): HTMLElement {
    setEmpty(true);
    const row = document.createElement('div');
    row.className = `${kind} visible enter`;
    row.innerHTML = html;
    conversation.append(row);
    lucideRefresh();
    scrollController.follow(row);
    window.setTimeout(() => row.classList.remove('enter'), 900);
    return row;
  }

  function note(message: string, icon = 'info'): void {
    appendRow(
      'system-event',
      `<span class="call-ended-label"><i data-lucide="${icon}"></i><span>${escapeHtml(message)}</span></span>`,
    );
  }

  function showTransportError(message: string, icon = 'triangle-alert'): void {
    note(message, icon);
  }

  function hookMicWave(): void {
    const tap = capture.frequencyAnalyser();
    if (!tap) return;
    waveApi?.connectAnalyser(tap.analyser, tap.sampleRate);
    waveFromAnalyser = true;
  }

  function hookTtsWave(): void {
    const tap = pcm.frequencyAnalyser();
    if (!tap) return;
    waveApi?.connectPlaybackAnalyser?.(tap.analyser, tap.sampleRate);
  }

  function unhookTtsWave(): void {
    waveApi?.disconnectPlaybackAnalyser?.();
  }

  function sendSocketCommand(payload: Record<string, unknown>): boolean {
    if (!connected || !socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(payload));
    return true;
  }

  function holdPlayback(): void {
    bargeHits = 0;
    bargePending = true;
    ignoreTts = false;
    processing = false;
    pcm.pause();
    unhookTtsWave();
    hookMicWave();
    showToast('Te escucho…', 'info');
  }

  function handleLocalBarge(): void {
    if (bargePending) return;
    bargeArmedAt = performance.now() + 400;
    holdPlayback();
    sendSocketCommand({ type: 'barge' });
  }

  function appendTokens(target: HTMLElement, text: string): void {
    const parts = text.split(/(\s+)/).filter((part) => part.length);
    for (const part of parts) {
      const token = document.createElement('span');
      token.className = 'token';
      token.textContent = part;
      target.append(token);
    }
  }

  function setCustomerPartial(text: string): void {
    const next = text.trim();
    if (!next) return;
    if (!customerBubble) {
      const row = appendRow(
        'message-row customer',
        `<div class="message-wrap"><div class="message-meta"><span>Cliente</span><i data-lucide="user-round" aria-hidden="true"></i></div><div class="message"></div></div>`,
      );
      customerBubble = row.querySelector('.message');
      customerShown = '';
    }
    if (!customerBubble) return;
    if (customerShown && !next.startsWith(customerShown)) {
      customerBubble.replaceChildren();
      customerShown = '';
    }
    const delta = next.slice(customerShown.length);
    if (!delta) return;
    appendTokens(customerBubble, delta);
    customerShown = next;
    scrollController.follow(customerBubble);
  }

  function finishCustomer(text: string): void {
    const finalText = text.trim();
    if (finalText) setCustomerPartial(finalText);
    if (!customerBubble) {
      if (finalText) addCustomer(finalText);
      return;
    }
    customerBubble.classList.add('complete');
    const time = document.createElement('span');
    time.className = 'message-time';
    time.textContent = stamp();
    customerBubble.append(time);
    customerBubble = null;
    customerShown = '';
  }

  function addCustomer(text: string): void {
    appendRow(
      'message-row customer',
      `<div class="message-wrap"><div class="message-meta"><span>Cliente</span><i data-lucide="user-round" aria-hidden="true"></i></div><div class="message complete">${escapeHtml(text)}<span class="message-time">${stamp()}</span></div></div>`,
    );
  }

  function ensureAgent(): HTMLElement {
    if (agentBubble) return agentBubble;
    const row = appendRow(
      'message-row agent',
      `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Wane</span></div><div class="message"></div></div>`,
    );
    agentBubble = row.querySelector('.message');
    return agentBubble as HTMLElement;
  }

  function appendToken(text: string): void {
    const bubble = ensureAgent();
    appendTokens(bubble, text);
    scrollController.follow(bubble);
  }

  function finishAgent(): void {
    if (!agentBubble) return;
    const time = document.createElement('span');
    time.className = 'message-time';
    time.textContent = stamp();
    agentBubble.append(time);
    agentBubble.classList.add('complete');
    agentBubble = null;
  }

  function addTool(id: string, payload: Record<string, unknown>): void {
    const detail = toolDetailFromEvent(payload);
    const title = detail.name;
    const status = String(payload.status ?? 'Ejecutando');
    appendRow(
      'tool-row',
      `<button type="button" class="tool-call" id="${id}" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-busy="true"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(title)}</div><div class="tool-status loading">${escapeHtml(status)}</div></div>${toolCallBusyMarkup()}</button>`,
    );
  }

  function completeTool(id: string, payload: Record<string, unknown>): void {
    const tool = document.getElementById(id);
    if (!tool) return;
    tool.classList.add('done');
    tool.setAttribute('aria-busy', 'false');
    const previous = readDetail(tool);
    const detail = toolDetailFromEvent(payload, previous?.kind === 'tool' ? previous : undefined);
    writeDetail(tool, detail);
    refreshOpenDetail(id, detail);
    const titleNode = tool.querySelector('.tool-title');
    const statusNode = tool.querySelector('.tool-status');
    if (titleNode) titleNode.textContent = detail.name;
    if (statusNode) {
      statusNode.textContent = String(payload.status ?? 'Completado');
      statusNode.classList.remove('loading');
    }
    const loader = tool.querySelector('.loader') as HTMLElement | null;
    if (loader) window.setTimeout(() => { loader.style.display = 'none'; }, 420);
  }

  function addRetrieval(id: string, payload: unknown): void {
    const markup = createRetrievalCardMarkup(id, payload);
    if (!markup) return;
    const row = appendRow('tool-row', markup);
    const agentRow = agentBubble?.closest('.message-row');
    if (agentRow && agentRow.parentElement === conversation) conversation.insertBefore(row, agentRow);
  }

  function completePendingRetrieval(payload: unknown): void {
    if (!shouldRenderRetrieval(payload)) {
      cancelPendingRetrieval();
      return;
    }
    const id = pendingRetrievalId ?? `rag-${Date.now()}`;
    if (!document.getElementById(id)) addRetrieval(id, payload);
    completeRetrievalCard(id, payload);
    pendingRetrievalId = null;
  }

  function cancelPendingRetrieval(): void {
    if (!pendingRetrievalId) return;
    document.getElementById(pendingRetrievalId)?.closest('.tool-row')?.remove();
    pendingRetrievalId = null;
  }

  function clearTranscript(): void {
    conversation.querySelectorAll('.message-row, .tool-row, .system-event').forEach((node) => node.remove());
    agentBubble = null;
    customerBubble = null;
    customerShown = '';
    pendingTools.clear();
    pendingRetrievalId = null;
    resetCallAgentSignals();
    setEmpty(false);
  }

  async function listen(): Promise<void> {
    if (!live || paused || !connected) return;
    capture.primeContext();
    capture.onLevel((level) => {
      if (!live || paused) return;
      waveApi?.setPlaying?.(true);
      waveApi?.setLevels(level, pcm.voiceLevel());
    });
    const started = await capture.startPcmStream((frame) => {
      if (!live || paused || !connected || !socket || socket.readyState !== WebSocket.OPEN) return;
      const now = performance.now();
      const level = capture.voiceLevel();
      const audioPlaying = pcmReady || pcm.size > 0;
      if (bargePending) {
        bargeHits = 0;
      } else if (audioPlaying) {
        if (now >= bargeArmedAt) {
          const floor = Math.max(0.08, noiseFloor);
          const voiced = capture.isVoiced();
          const speech = (voiced && level >= Math.max(0.18, floor + 0.12))
            || level >= Math.max(0.32, floor + 0.24);
          bargeHits = speech ? bargeHits + 1 : 0;
          if (bargeHits >= 2) handleLocalBarge();
        }
      } else {
        bargeHits = 0;
        if (level < noiseFloor + 0.04) noiseFloor = Math.max(0.06, noiseFloor * 0.94 + level * 0.06);
      }
      waveApi?.setPlaying?.(true);
      waveApi?.setLevels(level, pcm.voiceLevel());
      socket.send(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
    });
    if (!started) {
      note('No se pudo abrir el micrófono', 'mic-off');
      return;
    }
    hookMicWave();
  }

  function handleEvent(data: Record<string, unknown>): void {
    const type = String(data.type ?? '');
    if (type === 'call.connected') {
      if (connected) return;
      connected = true;
      // A fresh socket has no held barge, so make sure playback is not stuck paused.
      bargePending = false;
      pcm.resume();
      reconnectAttempts = 0;
      setConnectionStatus('connected', 'Llamada conectada');
      connectionHideTimer = window.setTimeout(() => setConnectionStatus('hidden'), 1400);
      note('Llamada conectada', 'phone');
      if (live && !paused && sendSocketCommand({ type: 'pcm.start', sample_rate: 16000 })) void listen();
    } else if (type === 'wave.level') {
      waveApi?.setPlaying?.(true);
    } else if (type === 'customer.partial') {
      setCustomerPartial(String(data.text ?? ''));
    } else if (type === 'customer.transcript') {
      processing = true;
      finishCustomer(String(data.text ?? ''));
    } else if (type === 'agent.token') {
      appendToken(String(data.text ?? ''));
    } else if (type === 'agent.signals') {
      applyCallAgentSignals(data);
      patchSessionFromAgentState(data);
    } else if (type === 'tool.started') {
      const toolCallId = String(data.tool_call_id ?? data.id ?? `${String(data.tool ?? 'tool')}-${Date.now()}`);
      const id = `tool-${toolCallId}`;
      addTool(id, data);
      pendingTools.set(toolCallId, id);
    } else if (type === 'tool.completed') {
      const toolCallId = String(data.tool_call_id ?? data.id ?? data.tool ?? 'tool');
      const id = pendingTools.get(toolCallId);
      if (id) completeTool(id, data);
    } else if (type === 'rag.started') {
      const id = `rag-${Date.now()}`;
      pendingRetrievalId = shouldRenderRetrieval(data) ? id : null;
      if (pendingRetrievalId) addRetrieval(id, data);
    } else if (type === 'rag.completed') {
      completePendingRetrieval(data);
    } else if (type === 'turn.started') {
      ignoreTts = false;
      bargeHits = 0;
    } else if (type === 'tts.format') {
      if (ignoreTts) return;
      ttsRate = Number(data.sample_rate) || 24000;
    } else if (type === 'tts.pause') {
      if (!bargePending) holdPlayback();
    } else if (type === 'tts.resume') {
      bargePending = false;
      pcm.resume();
      hookTtsWave();
    } else if (type === 'tts.cancel' || type === 'turn.cancelled') {
      bargePending = false;
      if (type === 'tts.cancel') showToast('Te escucho…', 'info');
      cancelPendingRetrieval();
      ignoreTts = true;
      pcm.cancel();
      pcmReady = false;
      processing = false;
      unhookTtsWave();
      hookMicWave();
      finishAgent();
    } else if (type === 'turn.completed') {
      bargePending = false;
      cancelPendingRetrieval();
      finishAgent();
      processing = false;
      pcmReady = false;
      const endCallAfterPlayback = data.end_call === true;
      pcm.finish(() => {
        unhookTtsWave();
        hookMicWave();
        if (endCallAfterPlayback) void hangup();
      });
    } else if (type === 'transcript.empty') {
      processing = false;
    } else if (type === 'error') {
      processing = false;
      const message = backendMessage(data, 'Error en la llamada.');
      if (!/no detect[eé] una frase/i.test(message)) note(message, 'triangle-alert');
    }
  }

  function bindSocket(): void {
    if (disposed || !live || paused) return;
    const current = new WebSocket(socketUrl);
    socket = current;
    connected = false;
    current.binaryType = 'arraybuffer';
    current.addEventListener('open', () => {
      if (socket !== current || closing || !live) {
        current.close();
        return;
      }
      const authToken = currentAuthToken();
      if (!authToken || !attachedConversationId) {
        showTransportError('No se puede autenticar la llamada: faltan credenciales.');
        current.close();
        return;
      }
      current.send(JSON.stringify({ type: 'auth', token: authToken }));
      current.send(JSON.stringify({ type: 'conversation.attach', conversation_id: attachedConversationId }));
      setConnectionStatus('connecting', 'Autenticando llamada…');
    });
    current.addEventListener('message', (event) => {
      if (socket !== current || !live) return;
      if (typeof event.data !== 'string') {
        if (paused || ignoreTts) return;
        const bytes = new Uint8Array(event.data as ArrayBuffer);
        if (!pcmReady) {
          pcm.start(ttsRate);
          pcmReady = true;
          bargeArmedAt = performance.now() + 250;
          bargeHits = 0;
        }
        pcm.enqueue(bytes, () => undefined, () => undefined);
        hookTtsWave();
        waveApi?.setPlaying?.(true);
        waveApi?.setLevels(capture.voiceLevel(), pcm.voiceLevel());
        return;
      }
      try {
        const data: unknown = JSON.parse(event.data);
        if (!data || typeof data !== 'object' || Array.isArray(data)) return;
        handleEvent(data as Record<string, unknown>);
      } catch {
        showTransportError('La llamada recibió un mensaje inválido.');
      }
    });
    current.addEventListener('error', () => {
      if (socket !== current) return;
      setConnectionStatus('reconnecting', 'Reintentando conexión…');
    });
    current.addEventListener('close', (event) => {
      if (socket !== current) return;
      connected = false;
      if (closing || !live) return;
      socket = null;
      capture.stopPcmStream();
      capture.cancelRecording();
      pcm.cancel();
      pcmReady = false;
      unhookTtsWave();
      if (event.code === 4401) {
        setConnectionStatus('error', 'Sesión inválida. Volviendo al inicio…');
        void hangup(false);
        redirectToLogin();
      } else if (event.code === 4403) {
        void hangup(false);
        showTransportError('Esta cuenta no puede adjuntar la conversación.');
      } else {
        scheduleReconnect();
      }
    });
  }

  async function openCallConversation(): Promise<string> {
    const response = await fetch(`${apiUrl}/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${currentAuthToken()}`,
      },
      body: JSON.stringify({ channel: 'voice-demo' }),
    });
    if (!response.ok) {
      throw await backendErrorFromResponse(response, 'No se pudo abrir una conversación nueva.');
    }
    const payload: unknown = await response.json();
    const record = payload && typeof payload === 'object' && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : {};
    const nextId = typeof record.id === 'string' ? record.id.trim() : '';
    if (!nextId) throw new Error('La API no devolvió una conversación válida.');
    return nextId;
  }

  async function startCall(): Promise<void> {
    if (live && !paused) return;
    if (!currentAuthToken()) {
      showTransportError('No se puede iniciar la llamada: faltan credenciales.');
      return;
    }
    if (live && paused) {
      await resumeCall();
      return;
    }
    const nextConversationId = await openCallConversation();
    attachedConversationId = nextConversationId;
    sessionStorage.setItem(conversationIdKey, nextConversationId);
    capture.primeContext();
    pcm.prime();
    live = true;
    paused = false;
    connected = false;
    processing = false;
    pcmReady = false;
    bargePending = false;
    closing = false;
    reconnectAttempts = 0;
    setConnectionStatus('connecting', 'Conectando llamada…');
    startedAt = performance.now();
    patchSession({ status: 'En vivo' });
    syncControls();
    bindSocket();
    if (!waveApi) {
      try {
        waveApi = await waitForMonitor(1500);
      } catch {
        waveApi = monitor() ?? null;
      }
    }
    waveApi?.resetLiveWave?.();
    waveApi?.setPlaying?.(true);
  }

  async function resumeCall(): Promise<void> {
    if (!live || !paused) return;
    capture.primeContext();
    pcm.prime();
    paused = false;
    processing = false;
    pcmReady = false;
    bargePending = false;
    waveApi?.setPlaying?.(true);
    syncControls();
    if (connected && sendSocketCommand({ type: 'pcm.start', sample_rate: 16000 })) void listen();
  }

  function pauseCall(): void {
    if (!live || paused) return;
    paused = true;
    processing = false;
    pcmReady = false;
    bargePending = false;
    capture.stopPcmStream();
    capture.cancelRecording();
    sendSocketCommand({ type: 'pcm.stop' });
    pcm.cancel();
    unhookTtsWave();
    waveApi?.setPlaying?.(false);
    setConnectionStatus('hidden');
    syncControls();
  }

  async function hangup(notify = true): Promise<void> {
    if (!live && !socket) return;
    closing = true;
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    live = false;
    paused = false;
    processing = false;
    bargePending = false;
    capture.stopPcmStream();
    capture.abort();
    sendSocketCommand({ type: 'pcm.stop' });
    connected = false;
    pcm.shutdown();
    waveApi?.setPlaying?.(false);
    waveFromAnalyser = false;
    waveApi?.disconnectAnalyser();
    const current = socket;
    socket = null;
    if (current && current.readyState === WebSocket.OPEN) current.close();
    finishAgent();
    syncControls();
    if (notify) setConnectionStatus('hidden');
    if (notify) {
      patchSession({ status: 'Finalizada' });
      appendRow(
        'system-event call-ended',
        `<span class="call-ended-label"><i data-lucide="phone-off"></i><span>Llamada finalizada · ${stamp()}</span></span>`,
      );
    }
  }

  async function restartCall(): Promise<void> {
    const wasLive = live;
    await hangup(false);
    clearTranscript();
    waveApi?.resetLiveWave?.();
    if (wasLive) await startCall();
    else syncControls();
  }

  callBtn.addEventListener('click', () => {
    void startCall().catch((error) => {
      const message = errorMessage(error, 'No se pudo iniciar la llamada.');
      note(message, 'phone-off');
      showToast(message, 'error');
    });
  });
  pauseBtn.addEventListener('click', () => {
    pauseCall();
  });
  restartBtn.addEventListener('click', () => {
    void restartCall().catch((error) => {
      const message = errorMessage(error, 'No se pudo reiniciar la llamada.');
      note(message, 'phone-off');
      showToast(message, 'error');
    });
  });
  syncControls();

  void waitForMonitor().then((api) => {
    waveApi = api;
    if (live && !paused) api.setPlaying?.(true);
  }).catch(() => undefined);

  if (autoStart) {
    void startCall().catch((error) => {
      const message = errorMessage(error, 'No se pudo iniciar la llamada.');
      note(message, 'phone-off');
      showToast(message, 'error');
    });
  }

  return () => {
    disposed = true;
    scrollController.dispose();
    window.clearTimeout(reconnectTimer);
    window.clearTimeout(connectionHideTimer);
    void hangup(false);
  };
}
