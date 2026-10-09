type ScrollRoot = HTMLElement;

export type ConversationScrollController = {
  follow: (target?: HTMLElement | null) => void;
  scrollToLatest: () => void;
  isFollowing: () => boolean;
  dispose: () => void;
};

type ConversationScrollOptions = {
  scroller?: ScrollRoot | null;
  conversation?: HTMLElement | null;
  jumpButton?: HTMLButtonElement | null;
  threshold?: number;
};

type LucideWindow = Window & {
  lucide?: { createIcons: (options?: object) => void };
};

function reducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

function refreshIcons(): void {
  (window as LucideWindow).lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
}

function distanceFromBottom(scroller: ScrollRoot): number {
  return Math.max(0, scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight);
}

function ensureJumpButton(
  scroller: ScrollRoot,
  provided: HTMLButtonElement | null | undefined,
): { button: HTMLButtonElement | null; owned: boolean } {
  if (provided) return { button: provided, owned: false };
  const anchor = scroller.closest<HTMLElement>('.call-demo-page')
    ?? scroller.parentElement;
  if (!anchor) return { button: null, owned: false };
  const existing = anchor.querySelector<HTMLButtonElement>('[data-conversation-scroll-jump]');
  if (existing) return { button: existing, owned: false };
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'call-scroll-jump';
  button.dataset.conversationScrollJump = 'true';
  button.setAttribute('aria-label', 'Ir a los mensajes recientes');
  button.setAttribute('title', 'Ir a los mensajes recientes');
  button.hidden = true;
  button.innerHTML = '<i data-lucide="arrow-down" aria-hidden="true"></i>';
  anchor.append(button);
  refreshIcons();
  return { button, owned: true };
}

/**
 * Keeps a call transcript pinned to its latest row until a person scrolls away.
 * The same controller is used by live calls and both replay renderers so the
 * pause/resume behavior stays identical across routes.
 */
export function mountConversationScroll(options: ConversationScrollOptions = {}): ConversationScrollController {
  const conversation = options.conversation ?? document.querySelector<HTMLElement>('#conversation');
  const scroller = options.scroller
    ?? document.querySelector<ScrollRoot>('#appContent')
    ?? conversation;
  if (!(scroller instanceof HTMLElement)) {
    return {
      follow: () => undefined,
      scrollToLatest: () => undefined,
      isFollowing: () => true,
      dispose: () => undefined,
    };
  }

  const threshold = Math.max(24, options.threshold ?? 112);
  const { button, owned } = ensureJumpButton(scroller, options.jumpButton);
  let pausedByUser = false;
  let userGesturePending = false;
  let programmaticUntil = 0;
  let scrollRest = 0;
  let followFrame = 0;
  let disposed = false;

  const atBottom = (): boolean => distanceFromBottom(scroller) <= threshold;

  const syncButton = (): void => {
    if (!button) return;
    const away = !atBottom();
    button.hidden = !away;
    button.setAttribute('aria-hidden', String(!away));
    button.classList.toggle('is-active', away);
  };

  const markUserIntent = (): void => {
    // A wheel/touch gesture is an explicit user action. It must be allowed to
    // interrupt a smooth programmatic scroll immediately.
    programmaticUntil = 0;
    userGesturePending = true;
  };

  const onScroll = (): void => {
    if (disposed) return;
    scroller.classList.add('is-scrolling');
    window.clearTimeout(scrollRest);
    scrollRest = window.setTimeout(() => scroller.classList.remove('is-scrolling'), 780);
    if (userGesturePending) {
      pausedByUser = !atBottom();
      userGesturePending = false;
    } else if (performance.now() >= programmaticUntil) {
      if (atBottom()) pausedByUser = false;
      else if (scroller.scrollHeight > scroller.clientHeight) pausedByUser = true;
    }
    syncButton();
  };

  const scrollToLatest = (): void => {
    if (disposed) return;
    pausedByUser = false;
    userGesturePending = false;
    programmaticUntil = performance.now() + (reducedMotion() ? 80 : 1100);
    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: reducedMotion() ? 'auto' : 'smooth',
    });
    window.setTimeout(() => {
      if (disposed) return;
      if (atBottom()) pausedByUser = false;
      syncButton();
    }, reducedMotion() ? 90 : 1120);
  };

  const onJump = (): void => scrollToLatest();
  const onUserGesture = (): void => markUserIntent();
  const onKeyGesture = (event: KeyboardEvent): void => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) markUserIntent();
  };

  scroller.addEventListener('scroll', onScroll, { passive: true });
  scroller.addEventListener('wheel', onUserGesture, { passive: true });
  scroller.addEventListener('touchstart', onUserGesture, { passive: true });
  scroller.addEventListener('pointerdown', onUserGesture, { passive: true });
  scroller.addEventListener('keydown', onKeyGesture);
  button?.addEventListener('click', onJump);
  syncButton();

  return {
    follow: () => {
      if (pausedByUser) {
        syncButton();
        return;
      }
      if (followFrame) return;
      followFrame = window.requestAnimationFrame(() => {
        followFrame = 0;
        if (!pausedByUser) scrollToLatest();
      });
    },
    scrollToLatest,
    isFollowing: () => !pausedByUser,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      scroller.removeEventListener('scroll', onScroll);
      scroller.removeEventListener('wheel', onUserGesture);
      scroller.removeEventListener('touchstart', onUserGesture);
      scroller.removeEventListener('pointerdown', onUserGesture);
      scroller.removeEventListener('keydown', onKeyGesture);
      button?.removeEventListener('click', onJump);
      window.clearTimeout(scrollRest);
      if (followFrame) window.cancelAnimationFrame(followFrame);
      scroller.classList.remove('is-scrolling');
      if (owned) button?.remove();
    },
  };
}
