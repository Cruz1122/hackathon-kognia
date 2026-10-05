import { Marked } from 'marked';

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

export type DetailPayload = ToolDetail | SourceDetail;

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
    if (!label) return [];
    return [{ label, value: String(record.value ?? '') }];
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
    if (parsed && (parsed.kind === 'tool' || parsed.kind === 'source')) return parsed;
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

function fieldRow(field: DetailField): string {
  const pending = isPendingValue(field.value);
  const long = !pending && isLongValue(field.value);
  const status = !pending && !long && field.label === 'Estado';
  const rowClass = ['detail-field', long ? 'is-long' : ''].filter(Boolean).join(' ');
  const valueClass = [pending ? 'is-pending' : '', long ? 'is-long' : '', status ? 'is-status' : ''].filter(Boolean).join(' ');
  const value = status
    ? `<span class="detail-chip">${escapeHtml(field.value)}</span>`
    : escapeHtml(field.value);
  return `<div class="${rowClass}"><dt>${escapeHtml(field.label)}</dt><dd${valueClass ? ` class="${valueClass}"` : ''}>${value}</dd></div>`;
}

function fieldsMarkup(fields: DetailField[], empty = 'Sin datos'): string {
  if (!fields.length) return `<p class="detail-empty">${escapeHtml(empty)}</p>`;
  return `<dl class="detail-fields">${fields.map(fieldRow).join('')}</dl>`;
}

function groupedFields(groups: { title: string; fields?: DetailField[]; empty?: string }[]): string {
  return `<div class="detail-sheet">${groups.map((group) => `<section class="detail-group"><h3>${escapeHtml(group.title)}</h3>${fieldsMarkup(group.fields ?? [], group.empty)}</section>`).join('')}</div>`;
}

type SessionState = {
  name: string;
  phone: string;
  status: string;
};

const session: SessionState = {
  name: '',
  phone: '',
  status: 'En espera',
};

function pendingValue(value: string): string {
  return value.trim() || 'Pendiente';
}

export function patchSession(partial: { name?: string; phone?: string; status?: string }): void {
  if (typeof partial.name === 'string') session.name = partial.name.trim();
  if (typeof partial.phone === 'string') session.phone = partial.phone.trim();
  if (typeof partial.status === 'string' && partial.status.trim()) session.status = partial.status.trim();
  renderSession();
}

function sessionFields(): DetailField[] {
  return [
    { label: 'Nombre', value: pendingValue(session.name) },
    { label: 'Teléfono', value: pendingValue(session.phone) },
    { label: 'Estado', value: pendingValue(session.status) },
  ];
}

function renderSession(): void {
  const root = document.getElementById('detailPanel');
  const panel = root?.querySelector('#sessionSummary');
  if (!panel) return;
  panel.innerHTML = `<p class="detail-kicker">Cliente</p><h2 class="detail-title">Estado de la llamada</h2><div class="detail-sheet"><section class="detail-group">${fieldsMarkup(sessionFields())}</section></div>`;
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

function renderBody(detail: DetailPayload): string {
  if (detail.kind === 'source') {
    const content = detail.content ? renderMarkdown(detail.content) : 'Sin contenido recuperado';
    return `<p class="detail-kicker">Fuente</p><h2 class="detail-title">${escapeHtml(detail.title)}</h2><div class="detail-content">${content}</div>`;
  }
  return `<p class="detail-kicker">Herramienta usada</p><h2 class="detail-title">${escapeHtml(detail.name)}</h2>${groupedFields([
    { title: 'Entrada', fields: detail.inputs },
    { title: 'Resultado', fields: detail.outputs, empty: 'En curso…' },
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
  showSessionView();
}

export function openDetail(detail: DetailPayload, cardId?: string): void {
  const root = ensurePanel();
  const body = root?.querySelector('.detail-panel__body');
  const sessionNode = root?.querySelector('#sessionPanel');
  const close = root?.querySelector('.detail-panel__close');
  if (!root || !(body instanceof HTMLElement)) return;
  const sameCard = Boolean(cardId && root.dataset.cardId === cardId && root.classList.contains('open'));
  body.innerHTML = renderBody(detail);
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
