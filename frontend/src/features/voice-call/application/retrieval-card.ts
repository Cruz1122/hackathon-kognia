import { refreshOpenDetail, sourceDetailFromEvent, writeDetail } from './detail-panel.ts';

type JsonRecord = Record<string, unknown>;

export type NormalizedRetrieval = {
  usedRag: boolean;
  message: string;
  title: string;
  content: string;
};

const DEFAULT_RETRIEVAL_MESSAGE = 'Información relevante para tu pregunta';

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

export function normalizeRetrievalPayload(payload: unknown): NormalizedRetrieval {
  const record = asRecord(payload);
  const explicit = record.used_rag ?? record.usedRag;
  const message = typeof record.message === 'string' && record.message.trim()
    ? record.message.trim()
    : DEFAULT_RETRIEVAL_MESSAGE;
  const title = typeof record.title === 'string' && record.title.trim()
    ? record.title.trim()
    : message;
  const content = typeof record.content === 'string' ? record.content.trim() : '';
  if (typeof explicit === 'boolean') return { usedRag: explicit, message, title, content };

  const evidenceState = typeof record.evidence_state === 'string'
    ? record.evidence_state.toUpperCase()
    : typeof record.evidenceState === 'string'
      ? record.evidenceState.toUpperCase()
      : '';
  const sources = Array.isArray(record.sources)
    ? record.sources
    : Array.isArray(record.hits)
      ? record.hits
      : [];
  return {
    usedRag: sources.length > 0 && ['SUFFICIENT', 'AMBIGUOUS'].includes(evidenceState),
    message,
    title,
    content,
  };
}

export function shouldRenderRetrieval(payload: unknown): boolean {
  return normalizeRetrievalPayload(payload).usedRag;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

function loaderMarkup(): string {
  return `<div class="loader" aria-label="Consultando RAG"><span class="loader-dot" style="--angle:0deg"></span><span class="loader-dot" style="--angle:45deg"></span><span class="loader-dot" style="--angle:90deg"></span><span class="loader-dot" style="--angle:135deg"></span><span class="loader-dot" style="--angle:180deg"></span><span class="loader-dot" style="--angle:225deg"></span><span class="loader-dot" style="--angle:270deg"></span><span class="loader-dot" style="--angle:315deg"></span><span class="loader-runner"></span></div>`;
}

export function createRetrievalCardMarkup(id: string, payload: unknown): string {
  if (!shouldRenderRetrieval(payload)) return '';
  const { message } = normalizeRetrievalPayload(payload);
  const detail = sourceDetailFromEvent(payload);
  return `<button type="button" class="tool-call" id="${escapeHtml(id)}" data-rag="true" data-detail="${escapeHtml(JSON.stringify(detail))}" aria-live="polite" aria-busy="true"><div class="tool-icon" aria-hidden="true"><i data-lucide="book-search"></i></div><div class="tool-copy"><div class="tool-label"><i data-lucide="book-search" aria-hidden="true"></i><span>Fuente</span></div><div class="tool-title">${escapeHtml(message)}</div><div class="tool-status loading">Relacionando</div></div>${loaderMarkup()}<div class="done-mark" aria-hidden="true"><i data-lucide="check"></i></div></button>`;
}

export function completeRetrievalCard(id: string, payload: unknown): void {
  if (!shouldRenderRetrieval(payload)) return;
  const card = document.getElementById(id);
  if (!card) return;
  card.classList.add('done');
  card.setAttribute('aria-busy', 'false');
  const detail = sourceDetailFromEvent(payload);
  writeDetail(card, detail);
  refreshOpenDetail(id, detail);

  const status = card.querySelector('.tool-status');
  if (status) {
    status.textContent = 'Listo';
    status.classList.remove('loading');
  }
  const loader = card.querySelector('.loader') as HTMLElement | null;
  if (loader) window.setTimeout(() => { loader.style.display = 'none'; }, 420);
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}
