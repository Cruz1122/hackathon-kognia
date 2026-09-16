import { backendMessage } from '../infrastructure/backend-error';
import { completeRetrievalCard, createRetrievalCardMarkup, shouldRenderRetrieval } from './retrieval-card';
import { bindDetailClicks, mountSessionPanel, readDetail, refreshOpenDetail, toolDetailFromEvent, writeDetail } from './detail-panel';

function lucideRefresh(): void {
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

type RealtimeEnvelope = {
  type?: unknown;
  payload?: unknown;
};

export function bootEventsMonitor(apiUrl: string): void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector('#conversationEmpty');
  const hubChip = document.querySelector('#hubChipText');
  if (!conversation) return;
  bindDetailClicks(conversation);
  mountSessionPanel();

  const conversationId = sessionStorage.getItem('kognia.auth.conversation-id')?.trim() ?? '';
  const token = sessionStorage.getItem('kognia.auth.access-token')?.trim() ?? '';
  if (!token) {
    if (hubChip) hubChip.textContent = 'Demo local';
    return;
  }

  const socketUrl = `${apiUrl.replace(/^http/, 'ws')}/ws/events`;
  const pendingTools = new Map<string, string>();
  let pendingRetrievalId: string | null = null;
  let agentBubble: HTMLElement | null = null;
  let customerBubble: HTMLElement | null = null;
  let customerShown = '';
  let live = false;

  function setEmpty(hidden: boolean): void {
    if (conversationEmpty instanceof HTMLElement) conversationEmpty.hidden = hidden;
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

  function completePendingRetrieval(payload: unknown): void {
    if (!shouldRenderRetrieval(payload)) {
      cancelPendingRetrieval();
      return;
    }
    const id = pendingRetrievalId ?? `hub-rag-${Date.now()}`;
    if (!document.getElementById(id)) {
      const markup = createRetrievalCardMarkup(id, payload);
      if (markup) {
        const row = appendRow('tool-row', markup);
        const agentRow = agentBubble?.closest('.message-row');
        if (agentRow && agentRow.parentElement === conversation) conversation.insertBefore(row, agentRow);
      }
    }
    completeRetrievalCard(id, payload);
    pendingRetrievalId = null;
  }

  function cancelPendingRetrieval(): void {
    if (!pendingRetrievalId) return;
    document.getElementById(pendingRetrievalId)?.closest('.tool-row')?.remove();
    pendingRetrievalId = null;
  }

  function enterLiveFeed(): void {
    if (live) return;
    live = true;
    document.body.dataset.hubLive = '1';
    conversation.querySelectorAll('.timeline-item').forEach((node) => node.remove());
    setEmpty(true);
    if (hubChip) hubChip.textContent = 'Hub en vivo';
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
    const tokenNode = document.createElement('span');
    tokenNode.className = 'token';
    tokenNode.textContent = delta;
    customerBubble.append(tokenNode);
    customerShown = next;
  }

  function finishCustomer(text: string): void {
    setCustomerPartial(text);
    if (customerBubble) customerBubble.classList.add('complete');
    customerBubble = null;
    customerShown = '';
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

  function handleEnvelope(envelope: RealtimeEnvelope): void {
    const type = String(envelope.type ?? '');
    const eventConversation = typeof (envelope as { conversation_id?: unknown }).conversation_id === 'string'
      ? String((envelope as { conversation_id?: unknown }).conversation_id)
      : '';
    if (conversationId && eventConversation && eventConversation !== conversationId) return;
    const payload = envelope.payload && typeof envelope.payload === 'object' && !Array.isArray(envelope.payload)
      ? envelope.payload as Record<string, unknown>
      : {};
    if (type === 'customer.partial') {
      enterLiveFeed();
      setCustomerPartial(String(payload.text ?? ''));
    } else if (type === 'customer.transcript') {
      enterLiveFeed();
      finishCustomer(String(payload.text ?? ''));
    } else if (type === 'agent.token') {
      enterLiveFeed();
      const node = document.createElement('span');
      node.className = 'token';
      node.textContent = String(payload.text ?? '');
      ensureAgent().append(node);
    } else if (type === 'tool.started') {
      enterLiveFeed();
      const id = `hub-tool-${Date.now()}`;
      pendingTools.set(String(payload.tool ?? 'tool'), id);
      const detail = toolDetailFromEvent(payload);
      appendRow(
        'tool-row',
        `<button type="button" class="tool-call" id="${id}" data-detail="${escapeHtml(JSON.stringify(detail))}"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(detail.name)}</div><div class="tool-status loading">${escapeHtml(String(payload.status ?? 'Ejecutando'))}</div></div></button>`,
      );
    } else if (type === 'tool.completed') {
      const id = pendingTools.get(String(payload.tool ?? 'tool'));
      const tool = id ? document.getElementById(id) : null;
      if (tool && id) {
        tool.classList.add('done');
        const previous = readDetail(tool);
        const detail = toolDetailFromEvent(payload, previous?.kind === 'tool' ? previous : undefined);
        writeDetail(tool, detail);
        refreshOpenDetail(id, detail);
        const titleNode = tool.querySelector('.tool-title');
        if (titleNode) titleNode.textContent = detail.name;
        const statusNode = tool.querySelector('.tool-status');
        if (statusNode) {
          statusNode.textContent = String(payload.status ?? 'Completado');
          statusNode.classList.remove('loading');
        }
      }
    } else if (type === 'rag.started') {
      enterLiveFeed();
      const id = `hub-rag-${Date.now()}`;
      pendingRetrievalId = shouldRenderRetrieval(payload) ? id : null;
      if (pendingRetrievalId) {
        const markup = createRetrievalCardMarkup(id, payload);
        if (markup) {
          const row = appendRow('tool-row', markup);
          const agentRow = agentBubble?.closest('.message-row');
          if (agentRow && agentRow.parentElement === conversation) conversation.insertBefore(row, agentRow);
        }
      }
    } else if (type === 'rag.completed') {
      enterLiveFeed();
      completePendingRetrieval(payload);
    } else if (type === 'turn.completed' || type === 'turn.cancelled') {
      cancelPendingRetrieval();
      if (agentBubble) agentBubble.classList.add('complete');
      agentBubble = null;
    } else if (type === 'error') {
      enterLiveFeed();
      appendRow(
        'system-event',
        `<span class="call-ended-label"><i data-lucide="triangle-alert"></i><span>${escapeHtml(backendMessage(payload, 'El backend reportó un error.'))}</span></span>`,
      );
    }
  }

  const socket = new WebSocket(socketUrl);
  socket.addEventListener('open', () => {
    socket.send(JSON.stringify({ type: 'auth', token }));
    enterLiveFeed();
    if (hubChip) hubChip.textContent = 'Esperando eventos';
  });
  socket.addEventListener('message', (event) => {
    if (typeof event.data !== 'string') {
      return;
    }
    try {
      const data: unknown = JSON.parse(event.data);
      if (!data || typeof data !== 'object' || Array.isArray(data)) return;
      handleEnvelope(data as RealtimeEnvelope);
    } catch {
      return;
    }
  });
  socket.addEventListener('close', (event) => {
    if (event.code === 4401) {
      if (hubChip) hubChip.textContent = 'Sesión inválida';
      return;
    }
    if (hubChip) hubChip.textContent = 'Hub desconectado';
  });
}
