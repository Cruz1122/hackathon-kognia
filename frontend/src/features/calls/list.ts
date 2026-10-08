import { showToast } from '../voice-call/infrastructure/toast';
import { revealLoadedContent, showContentLoader } from '../ui/loading-reveal';
import { phoneDisplayMarkup } from '../phone/phone-display';

type ListedCall = {
  id: string;
  lifecycle?: string;
  status?: string;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number;
  caller?: string;
  customer_name?: string;
  replayable?: boolean;
};

type CallsPayload = { calls?: ListedCall[]; recent?: ListedCall[] };

const PAGE_SIZE = 8;
type SortKey = 'name' | 'phone' | 'started' | 'duration' | 'status';
type SortDir = 'asc' | 'desc';

export function bootCallsList(apiUrl: string, token: string): () => void {
  if (document.querySelector('[data-dev-catalog]') || window.location.pathname.startsWith('/dev')) {
    return () => undefined;
  }
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
  if (!search || !status || !body || !empty || !table || !pager || !controls || !loading || !content || !error || !board || sortButtons.length === 0) {
    return () => undefined;
  }
  const emptyTitle = empty.querySelector<HTMLElement>('.empty-state__title');

  const params = new URLSearchParams(window.location.search);
  search.value = params.get('q') ?? '';
  const requestedStatus = params.get('status');
  const initialStatus = requestedStatus === 'live' || requestedStatus === 'ended' || requestedStatus === 'failed' ? requestedStatus : 'all';
  applyStatus(status, initialStatus);
  let page = Math.max(1, Number(params.get('page')) || 1);
  let sortKey = parseSortKey(params.get('sort'));
  let sortDir = params.get('dir') === 'asc' || params.get('dir') === 'desc' ? params.get('dir') as SortDir : 'desc';
  let calls: ListedCall[] = [];
  let liveIds = new Set<string>();
  let disposed = false;
  let timer = 0;

  const show = (node: HTMLElement, visible: boolean): void => {
    node.hidden = !visible;
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
    const url = `${window.location.pathname}${suffix ? `?${suffix}` : ''}`;
    window.history.replaceState(window.history.state, '', url);
  };

  const filtered = (): ListedCall[] => {
    const query = search.value.trim().toLowerCase();
    return calls.filter((call) => {
      const live = liveIds.has(call.id);
      const failed = call.status === 'failed' || call.lifecycle === 'failed';
      const currentStatus = statusValue(status);
      if (currentStatus === 'live' && !live) return false;
      if (currentStatus === 'ended' && (live || failed)) return false;
      if (currentStatus === 'failed' && !failed) return false;
      if (!query) return true;
      const haystack = [call.customer_name, call.caller, call.id].filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(query);
    });
  };

  const paint = (): void => {
    const rows = filtered().slice().sort((left, right) => compareCalls(left, right, sortKey, sortDir, liveIds));
    sortButtons.forEach((button) => {
      const header = button.closest('th');
      const active = button.dataset.sort === sortKey;
      button.classList.toggle('is-sorted', active);
      if (active) header?.setAttribute('aria-sort', sortDir === 'asc' ? 'ascending' : 'descending');
      else header?.removeAttribute('aria-sort');
    });
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    if (page > pages) page = pages;
    const start = (page - 1) * PAGE_SIZE;
    const slice = rows.slice(start, start + PAGE_SIZE);
    body.replaceChildren(renderTestRow());
    slice.forEach((call) => body.append(renderRow(call, liveIds.has(call.id))));
    const hasRows = rows.length > 0;
    show(table, true);
    show(empty, !hasRows);
    show(pager, hasRows);
    if (emptyTitle) emptyTitle.textContent = calls.length === 0 ? 'Aún no hay llamadas.' : 'Ninguna llamada coincide con la búsqueda.';
    renderPager(controls, page, pages, (next) => {
      page = next;
      syncUrl();
      paint();
    });
    const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
    lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
    syncUrl();
  };

  const load = async (): Promise<void> => {
    try {
      const response = await fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(`calls ${response.status}`);
      const payload = await response.json() as CallsPayload;
      if (disposed) return;
      const live = sortCalls(payload.calls ?? []);
      const recent = sortCalls(payload.recent ?? []);
      liveIds = new Set(live.map((call) => call.id));
      calls = [...live, ...recent].sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at));
      show(error, false);
      paint();
      show(board, true);
      await revealLoadedContent(loading, content);
    } catch {
      if (disposed) return;
      show(board, false);
      show(error, true);
      await revealLoadedContent(loading, content);
    }
  };

  const onFilter = (): void => {
    page = 1;
    paint();
  };
  search.addEventListener('input', onFilter);
  status.addEventListener('gooey-change', onFilter);
  sortButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const key = parseSortKey(button.dataset.sort ?? null);
      if (key === sortKey) sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      else {
        sortKey = key;
        sortDir = key === 'name' || key === 'phone' || key === 'status' ? 'asc' : 'desc';
      }
      page = 1;
      paint();
    });
  });
  document.querySelector('#callsRetry')?.addEventListener('click', () => {
    showContentLoader(loading, content);
    void load();
  });
  void load();
  timer = window.setInterval(() => { void load(); }, 4000);

  return () => {
    disposed = true;
    window.clearInterval(timer);
  };
}

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
  root.dispatchEvent(new CustomEvent('gooey-set', { detail: { value } }));
}

function sortCalls(calls: ListedCall[]): ListedCall[] {
  return calls.slice().sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at));
}

function renderTestRow(): HTMLTableRowElement {
  const row = document.createElement('tr');
  row.className = 'calls-test-row';
  const status = document.createElement('td');
  const chip = document.createElement('span');
  chip.className = 'calls-status';
  chip.textContent = 'Demo';
  status.append(chip);
  row.append(
    cell('Llamada de prueba', 'calls-name'),
    cell('Navegador'),
    cell('—', 'calls-muted'),
    cell('—', 'calls-muted'),
    status,
    callActionCell(),
  );
  return row;
}

function callActionCell(): HTMLTableCellElement {
  const node = document.createElement('td');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'calls-view';
  button.setAttribute('aria-label', 'Iniciar llamada de prueba');
  const icon = document.createElement('i');
  icon.dataset.lucide = 'phone';
  icon.setAttribute('aria-hidden', 'true');
  button.append(icon);
  button.addEventListener('click', () => {
    showToast('Iniciando llamada de prueba', 'success');
    const link = document.createElement('a');
    link.href = '/calls/demo?autostart=1';
    link.dataset.route = 'calls-demo';
    document.body.append(link);
    link.click();
    link.remove();
  });
  node.append(button);
  return node;
}

function renderRow(call: ListedCall, live: boolean): HTMLTableRowElement {
  const row = document.createElement('tr');
  const name = call.customer_name?.trim() || 'Sin nombre';
  const phone = call.caller?.trim() || '—';
  row.append(
    cell(name, name === 'Sin nombre' ? 'calls-name calls-muted' : 'calls-name'),
    phoneCell(phone),
    cell(formatWhen(call.started_at)),
    cell(durationLabel(call, live)),
    statusCell(call, live),
    viewCell(call, name),
  );
  return row;
}

function viewCell(call: ListedCall, name: string): HTMLTableCellElement {
  const node = document.createElement('td');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'calls-view';
  button.setAttribute('aria-label', `Reproducir llamada de ${name}`);
  const icon = document.createElement('i');
  icon.dataset.lucide = 'play';
  icon.setAttribute('aria-hidden', 'true');
  button.append(icon);
  button.addEventListener('click', () => {
    const link = document.createElement('a');
    link.href = `/calls/saved?id=${encodeURIComponent(call.id)}`;
    link.dataset.route = 'calls-saved';
    document.body.append(link);
    link.click();
    link.remove();
  });
  node.append(button);
  return node;
}

function parseSortKey(value: string | null): SortKey {
  if (value === 'name' || value === 'phone' || value === 'started' || value === 'duration' || value === 'status') return value;
  return 'started';
}

function compareCalls(left: ListedCall, right: ListedCall, key: SortKey, dir: SortDir, liveIds: Set<string>): number {
  const factor = dir === 'asc' ? 1 : -1;
  const result = sortValue(left, key, liveIds).localeCompare(sortValue(right, key, liveIds), 'es', { numeric: true, sensitivity: 'base' });
  return result * factor;
}

function sortValue(call: ListedCall, key: SortKey, liveIds: Set<string>): string {
  const live = liveIds.has(call.id);
  if (key === 'name') return call.customer_name?.trim() || 'Sin nombre';
  if (key === 'phone') return call.caller?.trim() || '';
  if (key === 'started') return String(Date.parse(call.started_at) || 0).padStart(16, '0');
  if (key === 'duration') return String(live ? Number.MAX_SAFE_INTEGER : call.duration_ms ?? 0).padStart(16, '0');
  if (live) return '0';
  if (call.status === 'failed' || call.lifecycle === 'failed') return '2';
  return '1';
}

function cell(text: string, className = ''): HTMLTableCellElement {
  const node = document.createElement('td');
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function phoneCell(value: string): HTMLTableCellElement {
  const node = document.createElement('td');
  if (!value || value === '—') {
    node.className = 'calls-muted';
    node.textContent = '—';
    return node;
  }
  node.innerHTML = phoneDisplayMarkup(value);
  return node;
}

function statusCell(call: ListedCall, live: boolean): HTMLTableCellElement {
  const node = document.createElement('td');
  const chip = document.createElement('span');
  const failed = !live && (call.status === 'failed' || call.lifecycle === 'failed');
  chip.className = `calls-status${live ? ' is-live' : ''}${failed ? ' is-failed' : ''}`;
  if (live) {
    const dot = document.createElement('span');
    dot.className = 'calls-live-dot';
    dot.setAttribute('aria-hidden', 'true');
    chip.append(dot);
  }
  chip.append(live ? 'En vivo' : failed ? 'Fallida' : 'Finalizada');
  node.append(chip);
  return node;
}

function renderPager(host: HTMLElement, page: number, pages: number, go: (page: number) => void): void {
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

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('es', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date);
}

function durationLabel(call: ListedCall, live: boolean): string {
  if (live) return 'En curso';
  if (!call.ended_at && !(call.duration_ms ?? 0)) return '—';
  return formatDuration(call.duration_ms ?? 0);
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

export function watchLiveCalls(apiUrl: string): void {
  const watch = window as Window & { __kogniaCallNav?: boolean; __kogniaCallNavStop?: () => void };
  if (watch.__kogniaCallNav) return;
  watch.__kogniaCallNav = true;
  const seen = new Set<string>();
  let primed = false;
  let refreshing = false;
  let stopped = false;
  const tick = async (): Promise<void> => {
    if (stopped || refreshing) return;
    const token = sessionStorage.getItem('kognia.auth.access-token')?.trim() ?? '';
    if (!token) return;
    refreshing = true;
    try {
      const response = await fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) return;
      const body = await response.json() as CallsPayload;
      if (stopped) return;
      const live = body.calls ?? [];
      if (primed) {
        live.forEach((call) => {
          if (!seen.has(call.id)) showToast('Llamada en vivo', 'success');
        });
      }
      seen.clear();
      live.forEach((call) => seen.add(call.id));
      primed = true;
    } finally {
      refreshing = false;
    }
  };
  const timer = window.setInterval(() => { void tick(); }, 2000);
  watch.__kogniaCallNavStop = () => {
    stopped = true;
    window.clearInterval(timer);
    watch.__kogniaCallNav = false;
    watch.__kogniaCallNavStop = undefined;
  };
  void tick();
}

export function stopLiveCalls(): void {
  const watch = window as Window & { __kogniaCallNavStop?: () => void };
  watch.__kogniaCallNavStop?.();
}
