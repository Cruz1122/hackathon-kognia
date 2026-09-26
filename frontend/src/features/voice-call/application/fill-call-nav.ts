type ListedCall = { id: string; started_at: string };

export async function fillCallNav(apiUrl: string): Promise<void> {
  const list = document.getElementById('pstnCallList');
  const token = sessionStorage.getItem('kognia.auth.access-token')?.trim() ?? '';
  if (!list || !token) return;
  const response = await fetch(`${apiUrl}/calls`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) return;
  const body = await response.json() as { calls?: ListedCall[]; recent?: ListedCall[] };
  const calls = [...(body.calls ?? []), ...(body.recent ?? [])]
    .sort((left, right) => Date.parse(right.started_at) - Date.parse(left.started_at));
  const selected = new URLSearchParams(window.location.search).get('id');
  const currentLinks = [...list.querySelectorAll<HTMLAnchorElement>('a[data-call-id]')];
  const sameList = currentLinks.length === calls.length && calls.every((call, index) => currentLinks[index]?.dataset.callId === call.id);
  if (sameList) {
    currentLinks.forEach((link) => {
      const current = link.dataset.callId === selected;
      link.classList.toggle('is-active', current);
      if (current) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    openCallNav();
    return;
  }
  list.replaceChildren();
  calls.forEach((call, index) => {
    const link = document.createElement('a');
    const current = call.id === selected;
    link.className = `app-nav__item app-nav__call${current ? ' is-active' : ''}`;
    link.dataset.callId = call.id;
    link.href = `/calls/saved?id=${call.id}`;
    link.dataset.route = 'calls-saved';
    if (current) link.setAttribute('aria-current', 'page');
    const label = document.createElement('span');
    label.textContent = `Llamada ${index + 1}`;
    link.append(label);
    list.append(link);
  });
  openCallNav();
}

function openCallNav(): void {
  document.getElementById('callsGroup')?.classList.add('is-open');
  document.getElementById('callsToggle')?.setAttribute('aria-expanded', 'true');
  const children = document.getElementById('callsChildren');
  children?.removeAttribute('inert');
  children?.removeAttribute('aria-hidden');
}
