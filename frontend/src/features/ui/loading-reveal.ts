type TransitionHandle = {
  finished: Promise<void>;
};

type TransitionDocument = Document & {
  startViewTransition?: (update: () => void) => TransitionHandle;
};

type RevealTarget = HTMLElement | null | undefined;

const ROUTE_SETTLE_MS = 680;

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
): Promise<void> {
  const contentNodes = targets(content);
  if (!loader || !contentNodes.length || (loader.hidden && contentNodes.every((node) => !node.hidden))) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  await waitForLoaderFade(loader, reducedMotion);

  const update = (): void => {
    loader.hidden = true;
    loader.setAttribute('aria-hidden', 'true');
    loader.classList.remove('is-leaving');
    contentNodes.forEach(show);
    beforeReveal?.();
  };
  const transitionDocument = document as TransitionDocument;

  if (!reducedMotion && transitionDocument.startViewTransition) {
    const primaryContent = contentNodes[0];
    primaryContent.style.viewTransitionName = 'kognia-loaded-content';
    const transition = transitionDocument.startViewTransition(update);
    try {
      await transition.finished;
    } finally {
      primaryContent.style.removeProperty('view-transition-name');
    }
    return;
  }

  update();
  if (reducedMotion) return;
  const primaryContent = contentNodes[0];
  primaryContent.classList.add('loading-curtain-fallback');
  primaryContent.addEventListener('animationend', () => primaryContent.classList.remove('loading-curtain-fallback'), { once: true });
}
