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
 * Pins the transcript to the latest row until the person scrolls upward.
 * Follow updates assign scrollTop directly so token growth does not restart
 * a smooth scroll that never settles.
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

  const threshold = Math.max(24, options.threshold ?? 80);
  const { button, owned } = ensureJumpButton(scroller, options.jumpButton);
  let pausedByUser = false;
  let pinning = false;
  let touchStartY = 0;
  let disposed = false;

  const atBottom = (): boolean => distanceFromBottom(scroller) <= threshold;

  const syncButton = (): void => {
    if (!button) return;
    const away = pausedByUser && !atBottom();
    button.hidden = !away;
    button.setAttribute('aria-hidden', String(!away));
    button.classList.toggle('is-active', away);
  };

  const pin = (): void => {
    pinning = true;
    scroller.scrollTop = scroller.scrollHeight;
    window.requestAnimationFrame(() => {
      if (disposed) return;
      scroller.scrollTop = scroller.scrollHeight;
      pinning = false;
      syncButton();
    });
  };

  const onScroll = (): void => {
    if (disposed || pinning) return;
    if (atBottom()) pausedByUser = false;
    syncButton();
  };

  const onWheel = (event: WheelEvent): void => {
    if (event.deltaY < -1) pausedByUser = true;
    else if (event.deltaY > 1 && atBottom()) pausedByUser = false;
    syncButton();
  };

  const onTouchStart = (event: TouchEvent): void => {
    touchStartY = event.touches[0]?.clientY ?? 0;
  };

  const onTouchMove = (event: TouchEvent): void => {
    const next = event.touches[0]?.clientY ?? touchStartY;
    if (next - touchStartY > 8) pausedByUser = true;
    else if (touchStartY - next > 8 && atBottom()) pausedByUser = false;
    touchStartY = next;
    syncButton();
  };

  const onKey = (event: KeyboardEvent): void => {
    if (event.key === 'ArrowUp' || event.key === 'PageUp' || event.key === 'Home') pausedByUser = true;
    if ((event.key === 'ArrowDown' || event.key === 'PageDown' || event.key === 'End') && atBottom()) pausedByUser = false;
    syncButton();
  };

  const scrollToLatest = (): void => {
    if (disposed) return;
    pausedByUser = false;
    pin();
  };

  scroller.addEventListener('scroll', onScroll, { passive: true });
  scroller.addEventListener('wheel', onWheel, { passive: true });
  scroller.addEventListener('touchstart', onTouchStart, { passive: true });
  scroller.addEventListener('touchmove', onTouchMove, { passive: true });
  scroller.addEventListener('keydown', onKey);
  button?.addEventListener('click', scrollToLatest);
  syncButton();

  return {
    follow: () => {
      if (disposed || pausedByUser) {
        syncButton();
        return;
      }
      pin();
    },
    scrollToLatest,
    isFollowing: () => !pausedByUser,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      scroller.removeEventListener('scroll', onScroll);
      scroller.removeEventListener('wheel', onWheel);
      scroller.removeEventListener('touchstart', onTouchStart);
      scroller.removeEventListener('touchmove', onTouchMove);
      scroller.removeEventListener('keydown', onKey);
      button?.removeEventListener('click', scrollToLatest);
      if (owned) button?.remove();
    },
  };
}
