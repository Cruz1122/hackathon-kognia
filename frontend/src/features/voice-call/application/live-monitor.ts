import { AudioCaptureAdapter } from '../infrastructure/audio-capture-adapter';
import { PcmAudioQueue } from '../infrastructure/pcm-audio-queue';

type CallMonitorAudio = {
  pushAmplitude: (value: number) => void;
  connectAnalyser: (analyser: AnalyserNode, sampleRate?: number) => void;
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

export function bootLiveMonitor(apiUrl: string): void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector('#conversationEmpty');
  if (!conversation) return;

  const restartBtn = stealButton('rewindBtn');
  const callBtn = stealButton('startBtn');
  const pauseBtn = stealButton('playBtn');
  if (!callBtn || !restartBtn || !pauseBtn) return;

  setControl(restartBtn, 'rotate-ccw', 'Reiniciar', 'Reiniciar llamada');
  setControl(callBtn, 'phone', 'Llamar', 'Empezar llamada');
  setControl(pauseBtn, 'pause', 'Pausa', 'Pausar llamada');
  lucideRefresh();

  const capture = new AudioCaptureAdapter(`${apiUrl}/transcribe`);
  const pcm = new PcmAudioQueue();
  const socketUrl = `${apiUrl.replace(/^http/, 'ws')}/ws/call`;
  const pendingTools = new Map<string, string>();

  let socket: WebSocket | null = null;
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
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => row.classList.remove('enter'), 900);
    return row;
  }

  function note(message: string, icon = 'info'): void {
    appendRow(
      'system-event',
      `<span class="call-ended-label"><i data-lucide="${icon}"></i><span>${escapeHtml(message)}</span></span>`,
    );
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
      `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Agente</span></div><div class="message"></div></div>`,
    );
    agentBubble = row.querySelector('.message');
    return agentBubble as HTMLElement;
  }

  function appendToken(text: string): void {
    appendTokens(ensureAgent(), text);
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

  function addTool(id: string, title: string, status: string): void {
    appendRow(
      'tool-row',
      `<div class="tool-call" id="${id}"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Tool del agente</span></div><div class="tool-title">${escapeHtml(title)}</div><div class="tool-status loading">${escapeHtml(status)}</div></div><div class="loader" aria-label="Cargando"><span class="loader-dot" style="--angle:0deg"></span><span class="loader-dot" style="--angle:45deg"></span><span class="loader-dot" style="--angle:90deg"></span><span class="loader-dot" style="--angle:135deg"></span><span class="loader-dot" style="--angle:180deg"></span><span class="loader-dot" style="--angle:225deg"></span><span class="loader-dot" style="--angle:270deg"></span><span class="loader-dot" style="--angle:315deg"></span><span class="loader-runner"></span></div><div class="done-mark" aria-hidden="true"><i data-lucide="check"></i></div></div>`,
    );
  }

  function completeTool(id: string, title: string, status: string): void {
    const tool = document.getElementById(id);
    if (!tool) return;
    tool.classList.add('done');
    const titleNode = tool.querySelector('.tool-title');
    const statusNode = tool.querySelector('.tool-status');
    if (titleNode) titleNode.textContent = title;
    if (statusNode) {
      statusNode.textContent = status;
      statusNode.classList.remove('loading');
    }
    const loader = tool.querySelector('.loader') as HTMLElement | null;
    if (loader) window.setTimeout(() => { loader.style.display = 'none'; }, 420);
  }

  function clearTranscript(): void {
    conversation.querySelectorAll('.message-row, .tool-row, .system-event').forEach((node) => node.remove());
    agentBubble = null;
    customerBubble = null;
    customerShown = '';
    pendingTools.clear();
    setEmpty(false);
  }

  async function listen(): Promise<void> {
    if (!live || paused) return;
    capture.primeContext();
    capture.onLevel((level) => {
      if (!live || paused) return;
      waveApi?.pushAmplitude(level);
    });
    const started = await capture.startPcmStream((frame) => {
      if (!live || paused || !socket || socket.readyState !== WebSocket.OPEN) return;
      waveApi?.pushAmplitude(capture.voiceLevel());
      socket.send(frame.buffer);
    });
    if (!started) note('No se pudo abrir el micrófono', 'mic-off');
  }

  function handleEvent(data: Record<string, unknown>): void {
    const type = String(data.type ?? '');
    if (type === 'call.connected') {
      socket?.send(JSON.stringify({ type: 'pcm.start', sample_rate: 16000 }));
      void listen();
    } else if (type === 'wave.level') {
      waveApi?.setPlaying?.(true);
      waveApi?.pushAmplitude(Number(data.value) || 0.04);
    } else if (type === 'customer.partial') {
      setCustomerPartial(String(data.text ?? ''));
    } else if (type === 'customer.transcript') {
      processing = true;
      finishCustomer(String(data.text ?? ''));
    } else if (type === 'agent.token') {
      appendToken(String(data.text ?? ''));
    } else if (type === 'tool.started') {
      const title = String(data.title ?? data.tool ?? 'Tool');
      const id = `tool-${String(data.tool ?? 'tool')}-${Date.now()}`;
      addTool(id, title, String(data.status ?? 'Ejecutando'));
      pendingTools.set(String(data.tool ?? 'tool'), id);
    } else if (type === 'tool.completed') {
      const id = pendingTools.get(String(data.tool ?? 'tool'));
      if (id) completeTool(id, String(data.title ?? data.tool ?? 'Tool'), String(data.status ?? 'Completado'));
    } else if (type === 'turn.started') {
      ignoreTts = false;
    } else if (type === 'tts.format') {
      if (ignoreTts) return;
      if (!pcmReady) {
        pcm.start(Number(data.sample_rate) || 24000);
        pcmReady = true;
      }
    } else if (type === 'tts.cancel' || type === 'turn.cancelled') {
      ignoreTts = true;
      pcm.cancel();
      pcmReady = false;
      processing = false;
      finishAgent();
    } else if (type === 'turn.completed') {
      finishAgent();
      processing = false;
      pcmReady = false;
      pcm.finish(() => undefined);
    } else if (type === 'transcript.empty') {
      processing = false;
    } else if (type === 'error') {
      processing = false;
      const message = String(data.message ?? 'Error en la llamada');
      if (!/no detect[eé] una frase/i.test(message)) note(message, 'triangle-alert');
    }
  }

  function bindSocket(): void {
    socket = new WebSocket(socketUrl);
    socket.binaryType = 'arraybuffer';
    socket.addEventListener('message', (event) => {
      if (typeof event.data !== 'string') {
        if (paused || ignoreTts) return;
        const bytes = new Uint8Array(event.data as ArrayBuffer);
        pcm.enqueue(bytes, () => undefined, () => undefined);
        waveApi?.pushAmplitude(pcm.voiceLevel());
        return;
      }
      handleEvent(JSON.parse(event.data) as Record<string, unknown>);
    });
    socket.addEventListener('close', () => {
      if (closing || !live) return;
      void hangup(false);
      note('La llamada se desconectó', 'unplug');
    });
  }

  async function startCall(): Promise<void> {
    if (live && !paused) return;
    if (live && paused) {
      await resumeCall();
      return;
    }
    capture.primeContext();
    if (!waveApi) {
      try {
        waveApi = await waitForMonitor(1500);
      } catch {
        waveApi = monitor() ?? null;
      }
    }
    live = true;
    paused = false;
    processing = false;
    pcmReady = false;
    closing = false;
    startedAt = performance.now();
    waveApi?.resetLiveWave?.();
    waveApi?.setPlaying?.(true);
    syncControls();
    bindSocket();
  }

  async function resumeCall(): Promise<void> {
    if (!live || !paused) return;
    capture.primeContext();
    paused = false;
    processing = false;
    pcmReady = false;
    waveApi?.setPlaying?.(true);
    socket?.send(JSON.stringify({ type: 'pcm.start', sample_rate: 16000 }));
    syncControls();
    void listen();
  }

  function pauseCall(): void {
    if (!live || paused) return;
    paused = true;
    processing = false;
    pcmReady = false;
    capture.stopPcmStream();
    capture.cancelRecording();
    socket?.send(JSON.stringify({ type: 'pcm.stop' }));
    pcm.cancel();
    waveApi?.setPlaying?.(false);
    syncControls();
  }

  async function hangup(notify = true): Promise<void> {
    if (!live && !socket) return;
    closing = true;
    live = false;
    paused = false;
    processing = false;
    capture.stopPcmStream();
    capture.abort();
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'pcm.stop' }));
    pcm.cancel();
    waveApi?.setPlaying?.(false);
    waveApi?.disconnectAnalyser();
    const current = socket;
    socket = null;
    if (current && current.readyState === WebSocket.OPEN) current.close();
    finishAgent();
    syncControls();
    if (notify) {
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
      note(error instanceof Error ? error.message : 'No se pudo iniciar la llamada', 'phone-off');
    });
  });
  pauseBtn.addEventListener('click', () => {
    pauseCall();
  });
  restartBtn.addEventListener('click', () => {
    void restartCall().catch((error) => {
      note(error instanceof Error ? error.message : 'No se pudo reiniciar la llamada', 'phone-off');
    });
  });
  syncControls();

  void waitForMonitor().then((api) => {
    waveApi = api;
    api.setPlaying?.(false);
  }).catch(() => undefined);
}
