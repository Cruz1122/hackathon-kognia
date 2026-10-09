import { backendMessage } from '../infrastructure/backend-error';
import { accessTokenKey, conversationIdKey, redirectToLogin } from '../../auth/session-guard';
import { completeRetrievalCard, createRetrievalCardMarkup, shouldRenderRetrieval } from './retrieval-card';
import { bindDetailClicks, mountSessionPanel, patchSessionFromAgentState, readDetail, refreshOpenDetail, toolDetailFromEvent, writeDetail } from './detail-panel';
import { applyCallAgentSignals } from './agent-signals';
import { mountConversationScroll } from './conversation-scroll';

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

export function bootEventsMonitor(apiUrl: string): () => void {
  const conversation = document.querySelector('#conversation');
  const conversationEmpty = document.querySelector('#conversationEmpty');
  const hubChip = document.querySelector('#hubChipText');
  if (!conversation) return () => undefined;
  bindDetailClicks(conversation);
  mountSessionPanel();

  const currentToken = (): string => sessionStorage.getItem(accessTokenKey)?.trim() ?? '';
  const conversationId = sessionStorage.getItem(conversationIdKey)?.trim() ?? '';
  if (!currentToken()) {
    if (hubChip) hubChip.textContent = 'Demo local';
    return () => undefined;
  }
  const scrollController = mountConversationScroll({
    scroller: document.getElementById('appContent'),
    conversation: conversation instanceof HTMLElement ? conversation : null,
  });
  if (conversationId) {
    void fetch(`${apiUrl}/conversations/${conversationId}/agent-state`, {
      headers: { Authorization: `Bearer ${currentToken()}` },
    }).then(async (response) => {
      if (response.ok) patchSessionFromAgentState(await response.json());
    }).catch(() => undefined);
  }

  const socketUrl = `${apiUrl.replace(/^http/, 'ws')}/ws/events`;
  const pendingTools = new Map<string, string>();
  let pendingRetrievalId: string | null = null;
  let agentBubble: HTMLElement | null = null;
  let customerBubble: HTMLElement | null = null;
  let customerShown = '';
  let live = false;
  let disposed = false;

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
    scrollController.follow(row);
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
    scrollController.follow(customerBubble);
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
      `<div class="message-wrap"><div class="message-meta"><i data-lucide="headset" aria-hidden="true"></i><span>Wane</span></div><div class="message"></div></div>`,
    );
    agentBubble = row.querySelector('.message');
    return agentBubble as HTMLElement;
  }

  function handleEnvelope(envelope: RealtimeEnvelope): void {
    if (disposed) return;
    const type = String(envelope.type ?? '');
    const eventConversation = typeof (envelope as { conversation_id?: unknown }).conversation_id === 'string'
      ? String((envelope as { conversation_id?: unknown }).conversation_id)
      : '';
    const conversationId = sessionStorage.getItem('kognia.auth.conversation-id')?.trim() ?? '';
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
      const bubble = ensureAgent();
      bubble.append(node);
      scrollController.follow(bubble);
    } else if (type === 'agent.signals') {
      applyCallAgentSignals(payload);
      patchSessionFromAgentState(payload);
    } else if (type === 'tool.started') {
      enterLiveFeed();
      const toolCallId = String(payload.tool_call_id ?? payload.id ?? `${String(payload.tool ?? 'tool')}-${Date.now()}`);
      const id = `hub-tool-${toolCallId}`;
      pendingTools.set(toolCallId, id);
      const detail = toolDetailFromEvent(payload);
      appendRow(
        'tool-row',
        `<button type="button" class="tool-call" id="${id}" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-busy="true"><div class="tool-icon" aria-hidden="true"><i data-lucide="bot"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="bot" aria-hidden="true"></i><span>Herramienta usada</span></div><div class="tool-title">${escapeHtml(detail.name)}</div><div class="tool-status loading">Cargando…</div></div></button>`,
      );
    } else if (type === 'tool.completed') {
      const toolCallId = String(payload.tool_call_id ?? payload.id ?? payload.tool ?? 'tool');
      const id = pendingTools.get(toolCallId);
      const tool = id ? document.getElementById(id) : null;
      if (tool && id) {
        tool.classList.add('done');
        tool.setAttribute('aria-busy', 'false');
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
    if (disposed) {
      socket.close();
      return;
    }
    const token = currentToken();
    if (!token) {
      redirectToLogin();
      socket.close();
      return;
    }
    socket.send(JSON.stringify({ type: 'auth', token }));
    enterLiveFeed();
    if (hubChip) hubChip.textContent = 'Esperando eventos';
  });
  socket.addEventListener('message', (event) => {
    if (disposed) return;
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
    if (disposed) return;
    if (event.code === 4401) {
      if (hubChip) hubChip.textContent = 'Sesión inválida';
      redirectToLogin();
      return;
    }
    if (hubChip) hubChip.textContent = 'Hub desconectado';
  });

  return () => {
    if (disposed) return;
    disposed = true;
    scrollController.dispose();
    pendingTools.clear();
    pendingRetrievalId = null;
    agentBubble = null;
    customerBubble = null;
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.close();
    if (document.body.dataset.hubLive === '1') delete document.body.dataset.hubLive;
  };
}
