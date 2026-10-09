import { Marked } from 'marked';
import { phoneDisplayMarkup } from '../../phone/phone-display.ts';

export type DetailField = { label: string; value: string };

export type ToolDetail = {
  kind: 'tool';
  name: string;
  inputs: DetailField[];
  outputs?: DetailField[];
};

export type SourceDetail = {
  kind: 'source';
  title: string;
  content: string;
};

export type TechnicalDetailSection = {
  title: string;
  value: unknown;
};

export type TechnicalDetail = {
  kind: 'technical';
  title: string;
  kicker?: string;
  sections: TechnicalDetailSection[];
};

export type DetailPayload = ToolDetail | SourceDetail | TechnicalDetail;

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

function isSafeUrl(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed || /^javascript:/i.test(trimmed) || /^data:/i.test(trimmed)) return false;
  try {
    return ['http:', 'https:', 'mailto:'].includes(new URL(trimmed, 'https://example.invalid').protocol);
  } catch {
    return trimmed.startsWith('#') || trimmed.startsWith('/');
  }
}

const markdown = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    html() {
      return '';
    },
    link({ href, title, text }) {
      if (!isSafeUrl(href)) return text;
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
      return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`;
    },
    image({ href, title, text }) {
      if (!isSafeUrl(href)) return escapeHtml(text);
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
      const alt = escapeHtml(text);
      return `<img src="${escapeHtml(href)}" alt="${alt}"${titleAttr}>`;
    },
  },
});

export function renderMarkdown(source: string): string {
  const parsed = markdown.parse(source, { async: false });
  return typeof parsed === 'string' && parsed.trim() ? parsed : `<p>${escapeHtml(source)}</p>`;
}

function parseFields(value: unknown): DetailField[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const record = asRecord(item);
    const label = String(record.label ?? '').trim();
    const fieldValue = String(record.value ?? '').trim();
    if (!label || !fieldValue || isPendingValue(fieldValue)) return [];
    return [{ label, value: fieldValue }];
  });
}

export function toolDetailFromEvent(payload: unknown, previous?: ToolDetail): ToolDetail {
  const record = asRecord(payload);
  const inputs = parseFields(record.inputs);
  const outputs = parseFields(record.outputs);
  return {
    kind: 'tool',
    name: String(record.title ?? record.tool ?? previous?.name ?? 'Herramienta'),
    inputs: inputs.length ? inputs : previous?.inputs ?? [],
    outputs: outputs.length ? outputs : previous?.outputs,
  };
}

export function sourceDetailFromEvent(payload: unknown): SourceDetail {
  const record = asRecord(payload);
  const title = String(record.title ?? record.message ?? 'Documento').trim() || 'Documento';
  return {
    kind: 'source',
    title,
    content: String(record.content ?? '').trim(),
  };
}

export function writeDetail(card: HTMLElement, detail: DetailPayload): void {
  card.dataset.detail = JSON.stringify(detail);
}

export function readDetail(card: HTMLElement): DetailPayload | null {
  const raw = card.dataset.detail;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as DetailPayload;
    if (parsed && (parsed.kind === 'tool' || parsed.kind === 'source' || parsed.kind === 'technical')) return parsed;
  } catch {
    return null;
  }
  return null;
}

function isPendingValue(value: string): boolean {
  return value.trim() === 'Pendiente';
}

function isLongValue(value: string): boolean {
  return value.length > 64 || value.includes('\n');
}

function keywordAt(source: string, index: number, word: string): boolean {
  if (!source.startsWith(word, index)) return false;
  const next = source[index + word.length] ?? '';
  return !/[A-Za-z0-9_]/.test(next);
}

function parsePythonLiteral(source: string): unknown | undefined {
  let index = 0;

  function skipSpace(): void {
    while (index < source.length && /\s/.test(source[index] ?? '')) index += 1;
  }

  function fail(): never {
    throw new Error('literal');
  }

  function parseString(): string {
    const quote = source[index];
    if (quote !== "'" && quote !== '"') fail();
    index += 1;
    let text = '';
    while (index < source.length) {
      const char = source[index];
      index += 1;
      if (char === '\\') {
        const escaped = source[index] ?? '';
        index += 1;
        text += ({ n: '\n', t: '\t', r: '\r', '\\': '\\', "'": "'", '"': '"' } as Record<string, string>)[escaped] ?? escaped;
        continue;
      }
      if (char === quote) return text;
      text += char ?? '';
    }
    fail();
  }

  function parseNumber(): number {
    const start = index;
    if (source[index] === '-') index += 1;
    while (index < source.length && /[0-9.eE+-]/.test(source[index] ?? '')) index += 1;
    const value = Number(source.slice(start, index));
    if (!Number.isFinite(value) || start === index) fail();
    return value;
  }

  function parseValue(): unknown {
    skipSpace();
    if (keywordAt(source, index, 'None')) {
      index += 4;
      return null;
    }
    if (keywordAt(source, index, 'True')) {
      index += 4;
      return true;
    }
    if (keywordAt(source, index, 'False')) {
      index += 5;
      return false;
    }
    const char = source[index];
    if (char === '{') return parseObject();
    if (char === '[') return parseArray();
    if (char === "'" || char === '"') return parseString();
    if (char === '-' || (char !== undefined && char >= '0' && char <= '9')) return parseNumber();
    fail();
  }

  function parseObject(): Record<string, unknown> {
    index += 1;
    const record: Record<string, unknown> = {};
    skipSpace();
    if (source[index] === '}') {
      index += 1;
      return record;
    }
    while (index < source.length) {
      const key = parseValue();
      if (typeof key !== 'string' && typeof key !== 'number') fail();
      skipSpace();
      if (source[index] !== ':') fail();
      index += 1;
      record[String(key)] = parseValue();
      skipSpace();
      if (source[index] === ',') {
        index += 1;
        continue;
      }
      if (source[index] === '}') {
        index += 1;
        return record;
      }
      fail();
    }
    fail();
  }

  function parseArray(): unknown[] {
    index += 1;
    const items: unknown[] = [];
    skipSpace();
    if (source[index] === ']') {
      index += 1;
      return items;
    }
    while (index < source.length) {
      items.push(parseValue());
      skipSpace();
      if (source[index] === ',') {
        index += 1;
        continue;
      }
      if (source[index] === ']') {
        index += 1;
        return items;
      }
      fail();
    }
    fail();
  }

  try {
    const first = parseValue();
    skipSpace();
    if (index < source.length && source[index] === ',') {
      const items = [first];
      while (index < source.length && source[index] === ',') {
        index += 1;
        items.push(parseValue());
        skipSpace();
      }
      return index === source.length ? items : undefined;
    }
    return index === source.length ? first : undefined;
  } catch {
    return undefined;
  }
}

function jsonBlock(value: string): unknown | undefined {
  const trimmed = value.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return undefined;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed !== null && typeof parsed === 'object') return parsed;
  } catch {
    const parsed = parsePythonLiteral(trimmed);
    if (parsed !== undefined && parsed !== null && typeof parsed === 'object') return parsed;
  }
  return undefined;
}

function fieldRow(field: DetailField): string {
  const pending = isPendingValue(field.value);
  if (pending || !field.value.trim()) return '';
  const json = jsonBlock(field.value);
  const long = json !== undefined || isLongValue(field.value);
  const status = json === undefined && !long && field.label === 'Estado';
  const rowClass = ['detail-field', long ? 'is-long' : '', json !== undefined ? 'is-json' : ''].filter(Boolean).join(' ');
  const valueClass = [long ? 'is-long' : '', status ? 'is-status' : '', json !== undefined ? 'is-json' : ''].filter(Boolean).join(' ');
  const value = json !== undefined
    ? `<pre class="detail-json">${prettyJson(json)}</pre>`
    : status
      ? `<span class="detail-chip">${escapeHtml(field.value)}</span>`
      : field.label === 'Teléfono'
        ? phoneDisplayMarkup(field.value)
        : escapeHtml(field.value);
  return `<div class="${rowClass}"><dt>${escapeHtml(field.label)}</dt><dd${valueClass ? ` class="${valueClass}"` : ''}>${value}</dd></div>`;
}

function fieldsMarkup(fields: DetailField[], _empty = ''): string {
  const visible = fields.filter((field) => field.value.trim() && !isPendingValue(field.value));
  if (!visible.length) return '';
  return `<dl class="detail-fields">${visible.map(fieldRow).join('')}</dl>`;
}

function groupedFields(groups: { title: string; fields?: DetailField[]; empty?: string }[]): string {
  const visibleGroups = groups.filter((group) => (group.fields ?? []).some((field) => field.value.trim() && !isPendingValue(field.value)));
  if (!visibleGroups.length) return '';
  return `<div class="detail-sheet">${visibleGroups.map((group) => `<section class="detail-group"><h3>${escapeHtml(group.title)}</h3>${fieldsMarkup(group.fields ?? [], group.empty)}</section>`).join('')}</div>`;
}

function prettyJson(value: unknown): string {
  let serialized = '';
  try {
    serialized = JSON.stringify(value, null, 2) ?? String(value ?? '');
  } catch {
    serialized = String(value);
  }
  const tokenPattern = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/g;
  let result = '';
  let cursor = 0;
  serialized.replace(tokenPattern, (token, quoted: string | undefined, colon: string | undefined, index: number) => {
    result += escapeHtml(serialized.slice(cursor, index));
    if (quoted) {
      const className = colon ? 'json-key' : 'json-string';
      result += `<span class="${className}">${escapeHtml(quoted)}</span>${colon ? escapeHtml(colon) : ''}`;
    } else if (token === 'true' || token === 'false') {
      result += `<span class="json-boolean">${token}</span>`;
    } else if (token === 'null') {
      result += `<span class="json-null">${token}</span>`;
    } else {
      result += `<span class="json-number">${token}</span>`;
    }
    cursor = index + token.length;
    return token;
  });
  return result + escapeHtml(serialized.slice(cursor));
}

type SessionState = {
  name: string;
  phone: string;
  status: string;
};

const session: SessionState = {
  name: '',
  phone: '',
  status: '',
};

export function resetSession(): void {
  session.name = '';
  session.phone = '';
  session.status = '';
  renderSession();
}

export function patchSession(partial: { name?: string; phone?: string; status?: string }): void {
  if (typeof partial.name === 'string') session.name = partial.name.trim();
  if (typeof partial.phone === 'string') session.phone = partial.phone.trim();
  if (typeof partial.status === 'string' && partial.status.trim()) session.status = partial.status.trim();
  renderSession();
}

type AgentStateView = {
  phase?: unknown;
  customer_name?: unknown;
  state?: unknown;
};

function phaseLabel(value: unknown): string {
  if (value === 'completed') return 'Consulta completada';
  if (value === 'confirming') return 'Esperando confirmación';
  if (value === 'searching') return 'Consultando instituciones';
  if (value === 'presenting') return 'Presentando opciones';
  return 'Recopilando datos';
}

export function patchSessionFromAgentState(value: unknown): void {
  const envelope = asRecord(value) as AgentStateView;
  const nested = asRecord(envelope.state);
  const state = Object.keys(nested).length ? nested : asRecord(value);
  const stateRecord = state as JsonRecord;
  const hasObservedState = Boolean(
    Object.keys(asRecord(state.signals)).length
      || Object.keys(asRecord(state.facts)).length
      || (typeof stateRecord.last_turn_id === 'string' && stateRecord.last_turn_id)
      || (typeof stateRecord.last_response === 'string' && stateRecord.last_response)
      || (typeof state.phase === 'string' && state.phase !== 'understanding'),
  );
  if (!hasObservedState) return;
  const name = String(state.customer_name ?? '').trim();
  const phase = typeof state.phase === 'string' && state.phase !== 'understanding' ? state.phase : '';
  patchSession({
    ...(name ? { name } : {}),
    ...(phase ? { status: phaseLabel(phase) } : {}),
  });
}

function sessionFields(): DetailField[] {
  return [
    ...(session.name ? [{ label: 'Nombre', value: session.name }] : []),
    ...(session.phone ? [{ label: 'Teléfono', value: session.phone }] : []),
    ...(session.status ? [{ label: 'Estado', value: session.status }] : []),
  ];
}

function renderSession(): void {
  const root = document.getElementById('detailPanel');
  const panel = root?.querySelector('#sessionSummary');
  if (!panel) return;
  const hasData = Boolean(session.name || session.phone || session.status);
  panel.toggleAttribute('hidden', !hasData);
  if (!hasData) {
    panel.replaceChildren();
    return;
  }
  const title = session.status ? 'Estado global del cliente' : 'Cliente';
  panel.innerHTML = `<p class="detail-kicker">Cliente</p><h2 class="detail-title">${title}</h2><div class="detail-sheet"><section class="detail-group">${fieldsMarkup(sessionFields())}</section></div>`;
}

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function playContentEnter(node: HTMLElement): void {
  node.classList.remove('is-entering');
  if (prefersReducedMotion()) return;
  void node.offsetWidth;
  node.classList.add('is-entering');
  node.addEventListener('animationend', () => node.classList.remove('is-entering'), { once: true });
}

function showSessionView(): void {
  const root = document.getElementById('detailPanel');
  if (!root) return;
  const leavingTool = root.classList.contains('is-tool');
  root.classList.remove('open', 'is-tool');
  document.body.classList.remove('detail-open');
  delete root.dataset.cardId;
  const close = root.querySelector('.detail-panel__close');
  const body = root.querySelector('.detail-panel__body');
  const sessionNode = root.querySelector('#sessionPanel');
  if (close instanceof HTMLElement) close.hidden = true;
  if (body instanceof HTMLElement) body.hidden = true;
  if (sessionNode instanceof HTMLElement) {
    sessionNode.hidden = false;
    if (leavingTool) playContentEnter(sessionNode);
  }
  renderSession();
}

export function renderDetailMarkup(detail: DetailPayload): string {
  if (detail.kind === 'source') {
    const content = detail.content ? `<div class="detail-content">${renderMarkdown(detail.content)}</div>` : '';
    return `<p class="detail-kicker">Fuente</p><h2 class="detail-title">${escapeHtml(detail.title)}</h2>${content}`;
  }
  if (detail.kind === 'technical') {
    const sections = detail.sections
      .filter((section) => section.title.trim())
      .map((section) => `<details class="detail-technical-section"><summary>${escapeHtml(section.title)}</summary><pre>${prettyJson(section.value)}</pre></details>`)
      .join('');
    return `<p class="detail-kicker">${escapeHtml(detail.kicker ?? 'Telemetría')}</p><h2 class="detail-title">${escapeHtml(detail.title)}</h2>${sections}`;
  }
  return `<p class="detail-kicker">Herramienta usada</p><h2 class="detail-title">${escapeHtml(detail.name)}</h2>${groupedFields([
    { title: 'Entrada', fields: detail.inputs },
    { title: 'Resultado', fields: detail.outputs },
  ])}`;
}

function lucideRefresh(): void {
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

function bindPanelChrome(root: HTMLElement): void {
  if (root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  document.addEventListener('keydown', (event: KeyboardEvent) => {
    if (event.key === 'Escape' && root.classList.contains('is-tool')) showSessionView();
  });
  root.addEventListener('click', (event) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest('[data-detail-close]')) closeDetail();
  });
}

let sessionEventsBound = false;

function bindSessionEvents(): void {
  if (sessionEventsBound) return;
  sessionEventsBound = true;
  window.addEventListener('call-session-patch', (event: Event) => {
    const detail = (event as CustomEvent<Parameters<typeof patchSession>[0]>).detail;
    if (detail) patchSession(detail);
  });
}

function ensurePanel(): HTMLElement | null {
  const existing = document.getElementById('detailPanel');
  if (!existing) return null;
  bindPanelChrome(existing);
  bindSessionEvents();
  return existing;
}

export function mountSessionPanel(): void {
  const root = ensurePanel();
  if (!root) return;
  session.name = '';
  session.phone = '';
  session.status = '';
  showSessionView();
}

export function openDetail(detail: DetailPayload, cardId?: string): void {
  const root = ensurePanel();
  const body = root?.querySelector('.detail-panel__body');
  const sessionNode = root?.querySelector('#sessionPanel');
  const close = root?.querySelector('.detail-panel__close');
  if (!root || !(body instanceof HTMLElement)) return;
  const sameCard = Boolean(cardId && root.dataset.cardId === cardId && root.classList.contains('open'));
  body.innerHTML = renderDetailMarkup(detail);
  body.hidden = false;
  if (sessionNode instanceof HTMLElement) sessionNode.hidden = true;
  if (close instanceof HTMLElement) close.hidden = false;
  const heading = body.querySelector('.detail-title');
  if (heading) heading.id = 'detailPanelTitle';
  if (cardId) root.dataset.cardId = cardId;
  root.classList.add('open', 'is-tool');
  if (!sameCard) playContentEnter(body);
  lucideRefresh();
}

export function closeDetail(): void {
  showSessionView();
}

export function refreshOpenDetail(cardId: string, detail: DetailPayload): void {
  const root = document.getElementById('detailPanel');
  if (!root || !root.classList.contains('open') || root.dataset.cardId !== cardId) return;
  openDetail(detail, cardId);
}

export function bindDetailClicks(conversation: Element): void {
  if (conversation instanceof HTMLElement && conversation.dataset.detailBound === '1') return;
  if (conversation instanceof HTMLElement) conversation.dataset.detailBound = '1';
  ensurePanel();
  conversation.addEventListener('click', (event) => {
    const card = (event.target as HTMLElement | null)?.closest('.tool-call');
    if (!(card instanceof HTMLElement)) return;
    const detail = readDetail(card);
    if (!detail) return;
    openDetail(detail, card.id);
  });
}
