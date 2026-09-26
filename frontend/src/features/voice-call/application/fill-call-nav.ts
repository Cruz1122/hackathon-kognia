import { showToast } from '../infrastructure/toast';

type ListedCall = { id: string; started_at: string };

const POLL_MS = 2000;
const seenLive = new Set<string>();
let primed = false;
let refreshing = false;

export async function fillCallNav(apiUrl: string): Promise<void> {
  const watch = window as Window & { __kogniaCallNav?: boolean };
  if (!watch.__kogniaCallNav) {
    watch.__kogniaCallNav = true;
    window.setInterval(() => { void refreshCallNav(apiUrl); }, POLL_MS);
  }
  await refreshCallNav(apiUrl);
}

async function refreshCallNav(apiUrl: string): Promise<void> {
  if (refreshing) return;
  const list = document.getElementById('pstnCallList');
  const token = sessionStorage.getItem('kognia.auth.access-token')?.trim() ?? '';
  if (!list || !token) return;
  refreshing = true;
  try {
    const response = await fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const body = await response.json() as { calls?: ListedCall[]; recent?: ListedCall[] };
    const live = sortCalls(body.calls ?? []);
    const recent = sortCalls(body.recent ?? []);
    const calls = [...live, ...recent];
    const liveIds = new Set(live.map((call) => call.id));
    if (primed) {
      live.forEach((call) => {
        if (!seenLive.has(call.id)) showToast('Llamada en vivo', 'success');
      });
    }
    seenLive.clear();
    liveIds.forEach((id) => seenLive.add(id));
    primed = true;
    renderCallNav(list, calls, liveIds);
  } finally {
    refreshing = false;
  }
}

function sortCalls(calls: ListedCall[]): ListedCall[] {
  return calls.slice().sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at));
}

function renderCallNav(list: HTMLElement, calls: ListedCall[], liveIds: Set<string>): void {
  const selected = new URLSearchParams(window.location.search).get('id');
  const currentLinks = [...list.querySelectorAll<HTMLAnchorElement>('a[data-call-id]')];
  const sameList = currentLinks.length === calls.length && calls.every((call, index) => currentLinks[index]?.dataset.callId === call.id);
  if (sameList) {
    currentLinks.forEach((link) => {
      const id = link.dataset.callId ?? '';
      const current = id === selected;
      link.classList.toggle('is-active', current);
      link.classList.toggle('is-live', liveIds.has(id));
      if (current) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      syncLiveDot(link, liveIds.has(id));
    });
    openCallNav();
    return;
  }
  list.replaceChildren();
  calls.forEach((call, index) => {
    const link = document.createElement('a');
    const current = call.id === selected;
    const live = liveIds.has(call.id);
    link.className = `app-nav__item app-nav__call${current ? ' is-active' : ''}${live ? ' is-live' : ''}`;
    link.dataset.callId = call.id;
    link.href = `/calls/saved?id=${call.id}`;
    link.dataset.route = 'calls-saved';
    if (current) link.setAttribute('aria-current', 'page');
    const label = document.createElement('span');
    label.textContent = `Llamada ${index + 1}`;
    link.append(label);
    syncLiveDot(link, live);
    list.append(link);
  });
  openCallNav();
}

function syncLiveDot(link: HTMLElement, live: boolean): void {
  const existing = link.querySelector('.app-nav__live-dot');
  if (!live) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const dot = document.createElement('span');
  dot.className = 'app-nav__live-dot';
  dot.setAttribute('aria-hidden', 'true');
  link.append(dot);
}

function openCallNav(): void {
  document.getElementById('callsGroup')?.classList.add('is-open');
  document.getElementById('callsToggle')?.setAttribute('aria-expanded', 'true');
  const children = document.getElementById('callsChildren');
  children?.removeAttribute('inert');
  children?.removeAttribute('aria-hidden');
}
