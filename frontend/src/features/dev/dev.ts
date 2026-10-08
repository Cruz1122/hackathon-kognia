import { showToast } from '../voice-call/infrastructure/toast';
import { revealLoadedContent, showContentLoader } from '../ui/loading-reveal';
import { phoneDisplayMarkup } from '../phone/phone-display';

type DevCall = {
  call_id: string;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number;
  recording_duration_ms?: number;
  status?: string;
  lifecycle?: string;
  customer_name?: string;
  caller?: string;
};

type DevCallsPayload = { calls?: DevCall[] };
type SortKey = 'name' | 'phone' | 'started' | 'duration' | 'status';
type SortDir = 'asc' | 'desc';

const PAGE_SIZE = 8;

function statusValue(root: HTMLElement): string {
  return root.querySelector<HTMLInputElement>('[data-dropdown-input]')?.value || 'all';
}

function applyStatus(root: HTMLElement, value: string): void {
  const input = root.querySelector<HTMLInputElement>('[data-dropdown-input]');
  const options = [...root.querySelectorAll<HTMLButtonElement>('[data-dropdown-option]')];
  const selected = options.find((option) => option.dataset.value === value);
  if (!input || !selected) return;
  input.value = value;
  root.dataset.selectedIndex = selected.dataset.index ?? '0';
  const label = root.querySelector<HTMLElement>('[data-dropdown-value]');
  const text = selected.querySelector('.gooey-dropdown__option-text')?.textContent?.trim();
  if (label && text) label.textContent = text;
  options.forEach((option) => {
    const active = option === selected;
    option.setAttribute('aria-selected', String(active));
    option.tabIndex = active ? 0 : -1;
  });
}

function parseSortKey(value: string | null): SortKey {
  if (value === 'name' || value === 'phone' || value === 'started' || value === 'duration' || value === 'status') return value;
  return 'started';
}

function sortValue(call: DevCall, key: SortKey): string {
  if (key === 'name') return call.customer_name?.trim() || '';
  if (key === 'phone') return call.caller?.trim() || '';
  if (key === 'started') return String(Date.parse(call.started_at) || 0).padStart(16, '0');
  if (key === 'duration') return String(call.recording_duration_ms ?? call.duration_ms ?? 0).padStart(16, '0');
  if (call.status === 'failed' || call.lifecycle === 'failed') return '1';
  return '0';
}

function compareCalls(left: DevCall, right: DevCall, key: SortKey, dir: SortDir): number {
  const factor = dir === 'asc' ? 1 : -1;
  return sortValue(left, key).localeCompare(sortValue(right, key), 'es', { numeric: true, sensitivity: 'base' }) * factor;
}

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('es', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatDuration(durationMs: number): string {
  const total = Math.max(0, Math.round(durationMs / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours} h ${minutes} min`;
  if (minutes > 0) return `${minutes} min ${seconds.toString().padStart(2, '0')} s`;
  return `${seconds} s`;
}

function durationLabel(call: DevCall): string {
  const duration = call.recording_duration_ms ?? call.duration_ms ?? 0;
  return duration > 0 ? formatDuration(duration) : '';
}

function cell(text: string, className = ''): HTMLTableCellElement {
  const node = document.createElement('td');
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function phoneCell(value: string): HTMLTableCellElement {
  const node = document.createElement('td');
  if (!value) {
    node.className = 'calls-muted';
    return node;
  }
  node.innerHTML = phoneDisplayMarkup(value);
  return node;
}

function statusCell(call: DevCall): HTMLTableCellElement {
  const node = document.createElement('td');
  const chip = document.createElement('span');
  const failed = call.status === 'failed' || call.lifecycle === 'failed';
  chip.className = `calls-status${failed ? ' is-failed' : ''}`;
  chip.textContent = failed ? 'Fallida' : 'Finalizada';
  node.append(chip);
  return node;
}

function viewCell(call: DevCall): HTMLTableCellElement {
  const node = document.createElement('td');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'calls-view';
  const customer = call.customer_name?.trim() || 'llamada grabada';
  button.setAttribute('aria-label', `Reproducir ${customer}`);
  const icon = document.createElement('i');
  icon.dataset.lucide = 'play';
  icon.setAttribute('aria-hidden', 'true');
  button.append(icon);
  button.addEventListener('click', () => {
    window.location.assign(`/dev/replay?id=${encodeURIComponent(call.call_id)}`);
  });
  node.append(button);
  return node;
}

function renderRow(call: DevCall): HTMLTableRowElement {
  const row = document.createElement('tr');
  const openReplay = (): void => {
    window.location.assign(`/dev/replay?id=${encodeURIComponent(call.call_id)}`);
  };
  row.tabIndex = 0;
  row.classList.add('calls-row--interactive');
  row.addEventListener('click', (event) => {
    if (event.target instanceof Element && event.target.closest('button, a, input, select')) return;
    openReplay();
  });
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openReplay();
  });
  const name = call.customer_name?.trim() || 'Llamada grabada';
  const phone = call.caller?.trim() || '';
  const when = formatWhen(call.started_at);
  const duration = durationLabel(call);
  row.append(
    cell(name, call.customer_name?.trim() ? 'calls-name' : 'calls-name calls-muted'),
    phoneCell(phone),
    cell(when, when ? '' : 'calls-muted'),
    cell(duration, duration ? '' : 'calls-muted'),
    statusCell(call),
    viewCell(call),
  );
  return row;
}

function renderPager(host: HTMLElement, page: number, pages: number, go: (next: number) => void): void {
  host.replaceChildren();
  host.append(pagerButton('Anterior', page <= 1, () => go(page - 1)));
  pageWindow(page, pages).forEach((item) => {
    if (item === 'gap') {
      const gap = document.createElement('span');
      gap.className = 'calls-pager__gap';
      gap.textContent = '…';
      gap.setAttribute('aria-hidden', 'true');
      host.append(gap);
      return;
    }
    const button = pagerButton(String(item), false, () => go(item));
    if (item === page) button.setAttribute('aria-current', 'page');
    host.append(button);
  });
  host.append(pagerButton('Siguiente', page >= pages, () => go(page + 1)));
}

function pageWindow(page: number, pages: number): Array<number | 'gap'> {
  const wanted = new Set([1, pages, page - 1, page, page + 1]);
  const items: Array<number | 'gap'> = [];
  let previous = 0;
  for (let index = 1; index <= pages; index += 1) {
    if (!wanted.has(index)) continue;
    if (previous && index - previous > 1) items.push('gap');
    items.push(index);
    previous = index;
  }
  return items;
}

function pagerButton(label: string, disabled: boolean, onClick: () => void): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.disabled = disabled;
  button.addEventListener('click', onClick);
  return button;
}

export function bootDev(apiUrl: string, token: string): () => void {
  const search = document.querySelector<HTMLInputElement>('#callsSearch');
  const status = document.querySelector<HTMLElement>('#callsStatus');
  const body = document.querySelector<HTMLTableSectionElement>('#callsTableBody');
  const empty = document.querySelector<HTMLElement>('#callsEmpty');
  const table = document.querySelector<HTMLElement>('#callsTable');
  const pager = document.querySelector<HTMLElement>('#callsPager');
  const controls = document.querySelector<HTMLElement>('#callsPagerControls');
  const loading = document.querySelector<HTMLElement>('#callsLoading');
  const content = document.querySelector<HTMLElement>('#callsContent');
  const error = document.querySelector<HTMLElement>('#callsError');
  const board = document.querySelector<HTMLElement>('#callsBoard');
  const sortButtons = [...document.querySelectorAll<HTMLButtonElement>('[data-sort]')];
  if (!search || !status || !body || !empty || !table || !pager || !controls || !loading || !content || !error || !board || !sortButtons.length) return () => undefined;
  const emptyTitle = empty.querySelector<HTMLElement>('.empty-state__title');
  const params = new URLSearchParams(window.location.search);
  search.value = params.get('q') ?? '';
  const requestedStatus = params.get('status');
  applyStatus(status, requestedStatus === 'failed' || requestedStatus === 'ended' ? requestedStatus : 'all');
  let page = Math.max(1, Number(params.get('page')) || 1);
  let sortKey = parseSortKey(params.get('sort'));
  let sortDir: SortDir = params.get('dir') === 'asc' || params.get('dir') === 'desc' ? params.get('dir') as SortDir : 'desc';
  let calls: DevCall[] = [];
  let disposed = false;

  const filtered = (): DevCall[] => {
    const query = search.value.trim().toLowerCase();
    const selectedStatus = statusValue(status);
    return calls.filter((call) => {
      const failed = call.status === 'failed' || call.lifecycle === 'failed';
      if (selectedStatus === 'failed' && !failed) return false;
      if (selectedStatus === 'ended' && failed) return false;
      if (selectedStatus === 'live') return false;
      if (!query) return true;
      return [call.customer_name, call.caller, call.call_id].filter(Boolean).join(' ').toLowerCase().includes(query);
    });
  };

  const paint = (): void => {
    const rows = filtered().slice().sort((left, right) => compareCalls(left, right, sortKey, sortDir));
    sortButtons.forEach((button) => {
      const header = button.closest('th');
      const active = button.dataset.sort === sortKey;
      button.classList.toggle('is-sorted', active);
      if (active) header?.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
      else header?.removeAttribute('aria-sort');
    });
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (page > pages) page = pages;
    body.replaceChildren(...rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(renderRow));
    table.hidden = rows.length === 0;
    empty.hidden = rows.length !== 0;
    if (emptyTitle) emptyTitle.textContent = calls.length ? 'Ninguna llamada coincide con la búsqueda.' : 'Aún no hay llamadas.';
    pager.hidden = rows.length === 0;
    renderPager(controls, page, pages, (next) => { page = next; paint(); syncUrl(); });
    const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
    lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
  };

  const syncUrl = (): void => {
    const next = new URLSearchParams();
    const query = search.value.trim();
    if (query) next.set('q', query);
    const currentStatus = statusValue(status);
    if (currentStatus !== 'all') next.set('status', currentStatus);
    if (sortKey !== 'started' || sortDir !== 'desc') {
      next.set('sort', sortKey);
      next.set('dir', sortDir);
    }
    if (page > 1) next.set('page', String(page));
    const suffix = next.toString();
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${suffix ? `?${suffix}` : ''}`);
  };

  const load = async (notify = false): Promise<void> => {
    try {
      const response = await fetch(`${apiUrl}/dev/calls`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
      if (!response.ok) throw new Error(`dev calls ${response.status}`);
      const payload = await response.json() as DevCallsPayload;
      if (disposed) return;
      calls = payload.calls ?? [];
      error.hidden = true;
      board.hidden = false;
      paint();
      await revealLoadedContent(loading, content);
      if (notify) showToast('Llamadas grabadas actualizadas', 'success');
    } catch {
      if (disposed) return;
      board.hidden = true;
      error.hidden = false;
      await revealLoadedContent(loading, content);
      showToast('No se pudieron cargar las llamadas grabadas.', 'error');
    }
  };

  search.addEventListener('input', () => { page = 1; paint(); syncUrl(); });
  status.addEventListener('gooey-change', () => { page = 1; paint(); syncUrl(); });
  sortButtons.forEach((button) => button.addEventListener('click', () => {
    const nextKey = parseSortKey(button.dataset.sort ?? null);
    if (nextKey === sortKey) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    else { sortKey = nextKey; sortDir = nextKey === 'name' || nextKey === 'phone' || nextKey === 'status' ? 'asc' : 'desc'; }
    page = 1;
    paint();
    syncUrl();
  }));
  document.querySelector('#callsRetry')?.addEventListener('click', () => {
    showContentLoader(loading, content);
    void load(true);
  });
  void load();

  return () => { disposed = true; };
}
