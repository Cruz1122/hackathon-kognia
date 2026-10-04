import { showToast } from '../voice-call/infrastructure/toast';

type TraceSpan = {
  name: string;
  start_ms: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
};

type Turn = {
  id: string;
  provider: string | null;
  model: string | null;
  status: string;
  started_at: string;
  duration_ms: number;
  prompt: string;
  answer: string;
  data: Record<string, unknown> & { spans?: TraceSpan[]; tools_available?: string[] };
};

type CallGroup = {
  key: string;
  call_id: string | null;
  conversation_id: string;
  turns: number;
  started_at: string;
  updated_at: string;
  provider: string | null;
  model: string | null;
  status: string;
  preview: string;
  channel?: string | null;
};

type CallsPayload = { calls?: CallGroup[] };
type TracesPayload = { call_id: string | null; conversation_id: string; turns?: Turn[] };

type SpanTone = 'rag' | 'llm' | 'tool' | 'error' | 'other';

const REFRESH_MS = 5000;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text = '',
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? '';
  } catch {
    return String(value);
  }
}

function relative(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('es', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

function duration(ms: number): string {
  const value = Math.max(0, Math.round(ms));
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
}

function spanMeta(name: string): { icon: string; title: string; tone: SpanTone } {
  if (name.startsWith('tool.')) return { icon: 'wrench', title: name.slice(5), tone: 'tool' };
  if (name === 'rag.retrieve') return { icon: 'book-open', title: 'Búsqueda en conocimiento', tone: 'rag' };
  if (name === 'llm.request') return { icon: 'sparkles', title: 'Llamada al modelo', tone: 'llm' };
  if (name === 'provider.error') return { icon: 'triangle-alert', title: 'Error del proveedor', tone: 'error' };
  if (name === 'agent.turn') return { icon: 'bot', title: 'Turno del agente', tone: 'other' };
  return { icon: 'dot', title: name, tone: 'other' };
}

function paintIcons(): void {
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

export function bootDev(apiUrl: string, token: string): () => void {
  const list = document.querySelector<HTMLElement>('#devCallsList');
  const callsCount = document.querySelector<HTMLElement>('#devCallsCount');
  const callsEmpty = document.querySelector<HTMLElement>('#devCallsEmpty');
  const detailBody = document.querySelector<HTMLElement>('#devDetailBody');
  const detailEmpty = document.querySelector<HTMLElement>('#devDetailEmpty');
  const search = document.querySelector<HTMLInputElement>('#devSearch');
  const loading = document.querySelector<HTMLElement>('#devLoading');
  const error = document.querySelector<HTMLElement>('#devError');
  const board = document.querySelector<HTMLElement>('#devBoard');
  if (!list || !callsCount || !callsEmpty || !detailBody || !detailEmpty || !search || !loading || !error || !board) {
    return () => undefined;
  }

  const headers = { Authorization: `Bearer ${token}` };
  let calls: CallGroup[] = [];
  let selectedKey = '';
  let turns: Turn[] = [];
  let selectedTurn = 0;
  let disposed = false;
  let busy = false;
  let signature = '';

  const show = (node: HTMLElement, visible: boolean): void => {
    node.hidden = !visible;
  };

  const visibleCalls = (): CallGroup[] => {
    const query = search.value.trim().toLowerCase();
    if (!query) return calls;
    return calls.filter((call) =>
      [call.preview, call.provider, call.model, call.call_id, call.conversation_id, call.channel]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query),
    );
  };

  const currentSignature = (): string =>
    `${calls.map((call) => `${call.key}:${call.turns}:${call.updated_at}`).join('|')}::${turns
      .map((turn) => turn.id)
      .join(',')}::${selectedTurn}`;

  async function loadCalls(): Promise<void> {
    const response = await fetch(`${apiUrl}/dev/calls`, { headers, cache: 'no-store' });
    if (!response.ok) throw new Error(`dev calls ${response.status}`);
    const payload = (await response.json()) as CallsPayload;
    calls = payload.calls ?? [];
    if (selectedKey && !calls.some((call) => call.key === selectedKey)) selectedKey = '';
    if (!selectedKey && calls.length > 0) selectedKey = calls[0].key;
  }

  async function loadTurns(): Promise<void> {
    const group = calls.find((call) => call.key === selectedKey);
    if (!group) {
      turns = [];
      return;
    }
    const url = group.call_id
      ? `${apiUrl}/calls/${encodeURIComponent(group.call_id)}/traces`
      : `${apiUrl}/dev/conversations/${encodeURIComponent(group.conversation_id)}/traces`;
    const response = await fetch(url, { headers, cache: 'no-store' });
    if (!response.ok) throw new Error(`traces ${response.status}`);
    const payload = (await response.json()) as TracesPayload;
    turns = payload.turns ?? [];
    if (selectedTurn >= turns.length) selectedTurn = Math.max(0, turns.length - 1);
    if (selectedTurn < 0) selectedTurn = 0;
  }

  function renderCalls(): void {
    const rows = visibleCalls();
    list!.replaceChildren();
    callsCount!.textContent = String(calls.length);
    show(callsEmpty!, calls.length === 0);
    if (calls.length > 0 && rows.length === 0) {
      callsEmpty!.textContent = 'Ninguna llamada coincide con la búsqueda.';
      show(callsEmpty!, true);
    } else {
      callsEmpty!.textContent = 'Aún no hay trazas. Realiza una llamada y vuelve a intentarlo.';
    }
    rows.forEach((call) => {
      const button = el('button', 'dev-call');
      button.type = 'button';
      button.dataset.key = call.key;
      if (call.key === selectedKey) button.classList.add('is-active');
      button.setAttribute('aria-pressed', String(call.key === selectedKey));

      const top = el('span', 'dev-call__top');
      const channel = el('span', 'dev-call__channel', call.channel ? call.channel : 'voz');
      const statusText = call.status === 'ok' ? 'ok' : call.status;
      const status = el('span', `dev-call__status dev-call__status--${call.status === 'ok' ? 'ok' : 'warn'}`, statusText);
      top.append(channel, status);

      const preview = el('span', 'dev-call__preview', call.preview || 'Sin texto de usuario');
      const meta = el('span', 'dev-call__meta');
      meta.append(
        el('span', '', `${call.turns} turno${call.turns === 1 ? '' : 's'}`),
        el('span', '', relative(call.updated_at)),
        el('span', '', `${call.provider ?? 'modelo'}${call.model ? ` · ${call.model}` : ''}`),
      );
      button.append(top, preview, meta);
      button.addEventListener('click', () => {
        if (selectedKey === call.key) return;
        selectedKey = call.key;
        selectedTurn = 0;
        turns = [];
        renderCalls();
        void refreshDetail(true);
      });
      list!.append(button);
    });
    paintIcons();
  }

  function fieldRow(label: string, value: unknown): HTMLElement {
    const row = el('div', 'dev-field');
    row.append(el('span', 'dev-field__label', label), el('span', 'dev-field__value', text(value)));
    return row;
  }

  function jsonDetails(value: unknown, label = 'JSON'): HTMLElement {
    const details = el('details', 'dev-json');
    details.append(el('summary', '', label));
    const pre = el('pre', 'dev-json__body', pretty(value));
    details.append(pre);
    return details;
  }

  function renderSpan(span: TraceSpan): HTMLElement {
    const meta = spanMeta(span.name);
    const item = el('article', `dev-span dev-span--${meta.tone}`);
    const head = el('div', 'dev-span__head');
    const icon = el('i', 'dev-span__icon');
    icon.dataset.lucide = meta.icon;
    icon.setAttribute('aria-hidden', 'true');
    const titleWrap = el('div', 'dev-span__titlewrap');
    const title = el('span', 'dev-span__title', meta.title);
    if (span.name === 'llm.request') {
      const provider = text(span.attributes.provider);
      const model = text(span.attributes.model);
      title.textContent = `Llamada al modelo · ${provider}/${model}`;
    }
    titleWrap.append(title);
    const timing = el('span', 'dev-span__timing', `${duration(span.duration_ms)} · +${duration(span.start_ms)}`);
    head.append(icon, titleWrap, timing);
    item.append(head);

    if (span.name === 'llm.request') {
      const attrs = span.attributes;
      const facts = el('div', 'dev-facts');
      facts.append(
        fieldRow('Provider', attrs.provider),
        fieldRow('Modelo', attrs.model),
        fieldRow('Intento', attrs.attempt),
        fieldRow('Ronda', attrs.round),
        fieldRow('Primer token', attrs.first_token_ms === null || attrs.first_token_ms === undefined ? '—' : duration(Number(attrs.first_token_ms))),
        fieldRow('Tools disponibles', Array.isArray(attrs.tools) ? `${attrs.tools.length}` : '0'),
      );
      item.append(facts);
      const rationale = el('div', 'dev-reason');
      rationale.append(el('span', 'dev-reason__label', 'Razonamiento / texto del modelo'));
      rationale.append(el('p', 'dev-reason__text', text(attrs.text).trim() || 'Sin texto antes de la respuesta.'));
      item.append(rationale);
      const calls = Array.isArray(attrs.tool_calls) ? attrs.tool_calls : [];
      if (calls.length > 0) {
        item.append(el('span', 'dev-reason__label', `Tools solicitadas (${calls.length})`));
        calls.forEach((call) => item.append(jsonDetails(call, String((call as { name?: string }).name ?? 'tool'))));
      }
      if (Array.isArray(attrs.messages)) item.append(jsonDetails(attrs.messages, 'Mensajes enviados'));
    } else if (span.name.startsWith('tool.')) {
      const attrs = span.attributes;
      const result = el('p', 'dev-span__result', text(attrs.result));
      item.append(result);
      const facts = el('div', 'dev-facts');
      facts.append(
        fieldRow('Éxito', attrs.ok === true ? 'Sí' : attrs.ok === false ? 'No' : '—'),
        fieldRow('Tool call', attrs.tool_call_id),
      );
      item.append(facts);
      const outputs = Array.isArray(attrs.outputs) ? attrs.outputs : [];
      outputs.forEach((output) => {
        const row = output as { label?: string; value?: string };
        item.append(fieldRow(String(row.label ?? 'Resultado'), row.value));
      });
      item.append(jsonDetails(attrs.arguments, 'Argumentos'));
    } else if (span.name === 'rag.retrieve') {
      const attrs = span.attributes;
      item.append(fieldRow('Usó conocimiento', attrs.used_rag === true ? 'Sí' : 'No'));
      item.append(fieldRow('Tema', attrs.topic));
      const hits = Array.isArray(attrs.hits) ? attrs.hits : [];
      item.append(el('span', 'dev-reason__label', `Fragmentos recuperados (${hits.length})`));
      hits.forEach((hit) => {
        const row = hit as { document?: string; section?: string; content?: string; score?: number };
        const card = el('div', 'dev-hit');
        card.append(el('span', 'dev-hit__title', [row.document, row.section].filter(Boolean).join(' · ') || 'Fragmento'));
        card.append(el('p', 'dev-hit__text', String(row.content ?? '')));
        item.append(card);
      });
    } else if (span.name === 'provider.error') {
      item.append(el('p', 'dev-span__result', text(span.attributes.message)));
    }
    return item;
  }

  function renderDetail(): void {
    const group = calls.find((call) => call.key === selectedKey);
    if (!group || turns.length === 0) {
      show(detailEmpty!, true);
      show(detailBody!, false);
      detailEmpty!.textContent = group
        ? 'Esta llamada aún no tiene turnos registrados.'
        : 'Selecciona una llamada para ver su traza.';
      return;
    }
    show(detailEmpty!, false);
    show(detailBody!, true);
    detailBody!.replaceChildren();

    const header = el('header', 'dev-detail__head');
    const heading = el('div', 'dev-detail__heading');
    heading.append(el('h3', '', `${turns.length} turno${turns.length === 1 ? '' : 's'} registrados`));
    heading.append(
      el(
        'p',
        'dev-detail__sub',
        `${group.channel ?? 'voz'} · ${group.call_id ? `llamada ${group.call_id.slice(0, 8)}` : `conversación ${group.conversation_id.slice(0, 8)}`}`,
      ),
    );
    const nav = el('div', 'dev-detail__nav');
    const prev = el('button', 'dev-nav-btn', 'Anterior');
    prev.type = 'button';
    prev.disabled = selectedTurn <= 0;
    prev.addEventListener('click', () => {
      selectedTurn = Math.max(0, selectedTurn - 1);
      renderDetail();
    });
    const next = el('button', 'dev-nav-btn', 'Siguiente');
    next.type = 'button';
    next.disabled = selectedTurn >= turns.length - 1;
    next.addEventListener('click', () => {
      selectedTurn = Math.min(turns.length - 1, selectedTurn + 1);
      renderDetail();
    });
    nav.append(prev, el('span', 'dev-detail__counter', `${selectedTurn + 1} / ${turns.length}`), next);
    header.append(heading, nav);
    detailBody!.append(header);

    const chips = el('div', 'dev-turns');
    turns.forEach((turn, index) => {
      const chip = el('button', `dev-turn-chip${index === selectedTurn ? ' is-active' : ''}`, `T${index + 1}`);
      chip.type = 'button';
      chip.setAttribute('aria-pressed', String(index === selectedTurn));
      if (turn.status !== 'ok') chip.classList.add('is-warn');
      chip.addEventListener('click', () => {
        selectedTurn = index;
        renderDetail();
      });
      chips.append(chip);
    });
    detailBody!.append(chips);

    const turn = turns[selectedTurn];
    const facts = el('div', 'dev-facts dev-facts--turn');
    facts.append(
      fieldRow('Estado', turn.status),
      fieldRow('Declarado', turn.provider && turn.model ? `${turn.provider}/${turn.model}` : '—'),
      fieldRow('Duración', duration(turn.duration_ms)),
      fieldRow('Inicio', relative(turn.started_at)),
    );
    detailBody!.append(facts);

    const transcript = el('section', 'dev-transcript');
    const user = el('div', 'dev-bubble dev-bubble--user');
    user.append(el('span', 'dev-bubble__label', 'Cliente'));
    user.append(el('p', 'dev-bubble__text', turn.prompt || '—'));
    const agent = el('div', 'dev-bubble dev-bubble--agent');
    agent.append(el('span', 'dev-bubble__label', 'Agente'));
    agent.append(el('p', 'dev-bubble__text', turn.answer || 'Sin respuesta registrada.'));
    transcript.append(user, agent);
    detailBody!.append(transcript);

    const spans = Array.isArray(turn.data.spans) ? turn.data.spans : [];
    const timeline = el('section', 'dev-timeline');
    timeline.append(el('h4', 'dev-section-title', 'Trazabilidad del turno'));
    const tools = Array.isArray(turn.data.tools_available) ? turn.data.tools_available : [];
    timeline.append(
      el('p', 'dev-section-sub', tools.length > 0 ? `Tools disponibles: ${tools.join(', ')}` : 'Sin tools disponibles en este turno.'),
    );
    spans
      .filter((span) => span.name !== 'agent.turn')
      .forEach((span) => timeline.append(renderSpan(span)));
    detailBody!.append(timeline);

    detailBody!.append(jsonDetails(turn.data, 'Traza completa (JSON)'));
    paintIcons();
  }

  async function refreshDetail(force: boolean): Promise<void> {
    await loadTurns();
    if (force || signature !== currentSignature()) {
      renderCalls();
      renderDetail();
    }
    signature = currentSignature();
  }

  async function refresh(notify = false): Promise<void> {
    if (busy || disposed) return;
    busy = true;
    try {
      await loadCalls();
      show(loading, false);
      show(error, false);
      show(board, true);
      await refreshDetail(false);
      if (notify) showToast('Trazas actualizadas', 'success');
    } catch {
      if (disposed) return;
      show(loading, false);
      show(error, true);
    } finally {
      busy = false;
    }
  }

  search.addEventListener('input', () => renderCalls());
  document.querySelector('#devRefresh')?.addEventListener('click', () => {
    showToast('Actualizando trazas', 'info');
    void refresh(true);
  });
  document.querySelector('#devRetry')?.addEventListener('click', () => {
    show(error, false);
    show(loading, true);
    void refresh();
  });

  void refresh();
  const timer = window.setInterval(() => {
    void refresh();
  }, REFRESH_MS);

  return () => {
    disposed = true;
    window.clearInterval(timer);
  };
}
