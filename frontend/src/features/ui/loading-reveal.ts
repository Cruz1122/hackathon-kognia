type RevealTarget = HTMLElement | null | undefined;

const ROUTE_SETTLE_MS = 680;
/** Pause with the loader on screen before the content curtain starts. */
const CURTAIN_DELAY_MS = 800;

function targets(value: RevealTarget | RevealTarget[]): HTMLElement[] {
  return (Array.isArray(value) ? value : [value]).filter((node): node is HTMLElement => node instanceof HTMLElement);
}

function hide(node: HTMLElement): void {
  node.hidden = true;
  node.setAttribute('aria-hidden', 'true');
  node.setAttribute('inert', '');
}

function show(node: HTMLElement): void {
  node.hidden = false;
  node.setAttribute('aria-hidden', 'false');
  node.removeAttribute('inert');
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

function waitForLoaderFade(loader: HTMLElement, reducedMotion: boolean): Promise<void> {
  if (reducedMotion) return Promise.resolve();
  loader.classList.remove('is-leaving');
  void loader.offsetWidth;
  loader.classList.add('is-leaving');
  return new Promise<void>((resolve) => {
    let settled = false;
    const finish = (): void => {
      if (settled) return;
      settled = true;
      loader.removeEventListener('animationend', finish);
      resolve();
    };
    loader.addEventListener('animationend', finish, { once: true });
    window.setTimeout(finish, 300);
  });
}

export async function waitForRouteSettle(): Promise<void> {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  await new Promise<void>((resolve) => window.setTimeout(resolve, ROUTE_SETTLE_MS));
}

export function showContentLoader(loader: RevealTarget, content: RevealTarget | RevealTarget[]): void {
  if (!loader) return;
  targets(content).forEach(hide);
  loader.hidden = false;
  loader.removeAttribute('aria-hidden');
  loader.classList.remove('is-leaving');
  // Reinserted loaders should replay their short entrance fade.
  loader.style.animation = 'none';
  void loader.offsetWidth;
  loader.style.removeProperty('animation');
}

export async function revealLoadedContent(
  loader: RevealTarget,
  content: RevealTarget | RevealTarget[],
  beforeReveal?: () => void,
  waitForReady?: () => Promise<void>,
): Promise<void> {
  const contentNodes = targets(content);
  if (!loader || !contentNodes.length || (loader.hidden && contentNodes.every((node) => !node.hidden))) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Keep the loader on screen a beat longer so the reveal is perceivable.
  if (!reducedMotion) await new Promise<void>((resolve) => window.setTimeout(resolve, CURTAIN_DELAY_MS));
  await waitForLoaderFade(loader, reducedMotion);

  loader.hidden = true;
  loader.setAttribute('aria-hidden', 'true');
  loader.classList.remove('is-leaving');
  contentNodes.forEach(show);
  beforeReveal?.();

  if (reducedMotion) return;
  const primaryContent = contentNodes[0];
  // Show the content clipped while it finishes rendering (charts, layout). The
  // curtain then reveals a finished view instead of loading mid-animation.
  primaryContent.classList.add('loading-curtain-armed');
  try {
    await (waitForReady ? waitForReady() : nextPaint());
  } finally {
    primaryContent.classList.remove('loading-curtain-armed');
  }
  primaryContent.classList.add('loading-curtain-fallback');
  const onCurtainEnd = (event: AnimationEvent): void => {
    if (event.target !== primaryContent || event.animationName !== 'kognia-content-curtain-in') return;
    primaryContent.classList.remove('loading-curtain-fallback');
    primaryContent.removeEventListener('animationend', onCurtainEnd);
  };
  primaryContent.addEventListener('animationend', onCurtainEnd);
}
