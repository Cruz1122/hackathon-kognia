import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '../../styles/driver-tour.css';
import { showToast } from '../../features/voice-call/infrastructure/toast';
import { TOUR_STEPS, isTourId, tourHasVisibleAnchor, type TourId } from './tours';

const DOCK_LABELS: Record<TourId, string> = {
  dashboard: 'Tablero',
  calls: 'Llamadas',
  dev: 'Modo dev',
  specifications: 'Especificaciones',
  call: 'Llamada',
  replay: 'Replay',
};

type DriverInstance = ReturnType<typeof driver>;

let currentTour: DriverInstance | null = null;
let activeTourId: TourId | null = null;
let suppressDismiss = false;
let queuedFrame = 0;
let observedRoot: Node | null = null;
let retryTimer = 0;
let retryAttempts = 0;
let tourPopoverPosition: { left: number; top: number } | null = null;
let tourPopoverDrag: {
  popover: HTMLElement;
  pointerId: number;
  startX: number;
  startY: number;
  left: number;
  top: number;
} | null = null;

const IDLE_EMOTION = 'surprised';
const ACTIVE_EMOTION = 'default-happy';

interface MascotFaceElement extends HTMLElement {
  setEmotion?: (emotion: string) => void;
}

function setDockEmotion(emotion: string): void {
  document.querySelector<MascotFaceElement>('[data-tour-dock] [data-mascot-face]')?.setEmotion?.(emotion);
}

function syncTourHeaderOffset(): void {
  const header = document.getElementById('appHeader');
  if (!(header instanceof HTMLElement) || header.hidden) {
    document.documentElement.style.removeProperty('--tour-header-offset');
    return;
  }
  const height = Math.ceil(header.getBoundingClientRect().height);
  document.documentElement.style.setProperty('--tour-header-offset', `${Math.max(0, height)}px`);
}

function clearTourHeaderOffset(): void {
  document.documentElement.style.removeProperty('--tour-header-offset');
  window.removeEventListener('resize', syncTourHeaderOffset);
}

function resetTourPopoverPosition(): void {
  tourPopoverPosition = null;
  tourPopoverDrag = null;
  const popover = document.querySelector<HTMLElement>('.driver-popover.hackakognia-tour');
  if (!popover) return;
  popover.classList.remove('is-user-positioned', 'is-dragging');
  ['position', 'left', 'top', 'right', 'bottom', 'transform', 'margin'].forEach((property) => {
    popover.style.removeProperty(property);
  });
}

function placeTourPopover(popover: HTMLElement, left: number, top: number): void {
  const rect = popover.getBoundingClientRect();
  const minLeft = 12;
  const maxLeft = Math.max(minLeft, window.innerWidth - rect.width - 12);
  const header = document.getElementById('appHeader');
  const minTop = Math.min(
    header instanceof HTMLElement && !header.hidden ? header.getBoundingClientRect().bottom + 12 : 12,
    Math.max(12, window.innerHeight - rect.height - 12),
  );
  const maxTop = Math.max(minTop, window.innerHeight - rect.height - 12);
  const boundedLeft = Math.max(minLeft, Math.min(maxLeft, left));
  const boundedTop = Math.max(minTop, Math.min(maxTop, top));
  popover.classList.add('is-user-positioned');
  popover.style.setProperty('position', 'fixed', 'important');
  popover.style.setProperty('left', `${boundedLeft}px`, 'important');
  popover.style.setProperty('top', `${boundedTop}px`, 'important');
  popover.style.setProperty('right', 'auto', 'important');
  popover.style.setProperty('bottom', 'auto', 'important');
  popover.style.setProperty('transform', 'none', 'important');
  popover.style.setProperty('margin', '0', 'important');
  tourPopoverPosition = { left: boundedLeft, top: boundedTop };
}

function onTourPopoverPointerDown(event: PointerEvent): void {
  if (!currentTour || event.button !== 0 || !(event.target instanceof Element)) return;
  const title = event.target.closest('.driver-popover.hackakognia-tour .driver-popover-title');
  const popover = title?.closest<HTMLElement>('.driver-popover.hackakognia-tour');
  if (!popover) return;
  const rect = popover.getBoundingClientRect();
  tourPopoverDrag = {
    popover,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    left: rect.left,
    top: rect.top,
  };
  event.preventDefault();
}

function onTourPopoverPointerMove(event: PointerEvent): void {
  const drag = tourPopoverDrag;
  if (!drag || drag.pointerId !== event.pointerId || !drag.popover.isConnected) return;
  placeTourPopover(
    drag.popover,
    drag.left + event.clientX - drag.startX,
    drag.top + event.clientY - drag.startY,
  );
  drag.popover.classList.add('is-dragging');
  event.preventDefault();
}

function onTourPopoverPointerEnd(event: PointerEvent): void {
  if (!tourPopoverDrag || tourPopoverDrag.pointerId !== event.pointerId) return;
  tourPopoverDrag.popover.classList.remove('is-dragging');
  tourPopoverDrag = null;
}

const TOUR_VERSION = '3';

function completionKey(id: TourId): string {
  return `hackakognia:onboarding:${id}:v${TOUR_VERSION}`;
}

function dismissalKey(id: TourId): string {
  return `hackakognia:onboarding:${id}:v${TOUR_VERSION}:dismissed`;
}

function markCompleted(id: TourId): void {
  try {
    localStorage.setItem(completionKey(id), 'completed');
  } catch {
    // The tour still finishes if storage is blocked.
  }
}

export function wasCompleted(id: TourId): boolean {
  try {
    return localStorage.getItem(completionKey(id)) === 'completed';
  } catch {
    return false;
  }
}

function markDismissed(id: TourId): void {
  try {
    sessionStorage.setItem(dismissalKey(id), '1');
  } catch {
    // A blocked session still lets the user close the tour.
  }
}

function wasDismissed(id: TourId): boolean {
  try {
    return sessionStorage.getItem(dismissalKey(id)) === '1';
  } catch {
    return false;
  }
}

export function stopTour(): void {
  suppressDismiss = true;
  currentTour?.destroy();
  currentTour = null;
  activeTourId = null;
  suppressDismiss = false;
  resetTourPopoverPosition();
  clearTourHeaderOffset();
  setDockEmotion(IDLE_EMOTION);
}

function normalizedPath(): string {
  const path = window.location.pathname;
  return path.length > 1 && path.endsWith('/') ? path.slice(0, -1) : path;
}

export function tourForPath(path = normalizedPath()): TourId | null {
  if (path === '/analytics') return 'dashboard';
  if (path === '/' && document.getElementById('authPanel')?.dataset.view === 'dashboard') return 'dashboard';
  if (path === '/calls') return 'calls';
  if (path === '/dev') return 'dev';
  if (path === '/specifications') return 'specifications';
  if (path === '/calls/demo' || path === '/calls/saved') return 'call';
  if (path === '/dev/replay') return 'replay';
  return null;
}

function revealed(id: string): boolean {
  const node = document.getElementById(id);
  if (!(node instanceof HTMLElement)) return false;
  if (node.hidden || node.hasAttribute('inert')) return false;
  if (node.classList.contains('loading-curtain-armed') || node.classList.contains('loading-curtain-fallback')) return false;
  return true;
}

function headerReady(): boolean {
  const header = document.getElementById('appHeader');
  if (!(header instanceof HTMLElement) || header.hidden) return false;
  const panel = document.getElementById('authPanel');
  if (panel && !header.classList.contains('is-header-revealed')) return false;
  return true;
}

function screenReady(id: TourId): boolean {
  if (id === 'dashboard') {
    const scope = document.querySelector('[data-tour-scope="dashboard"]');
    if (!(scope instanceof HTMLElement) || scope.getAttribute('aria-hidden') === 'true') return false;
    if (document.getElementById('authPanel') && !scope.classList.contains('is-active')) return false;
    return revealed('dashboardContent') && headerReady();
  }
  if (id === 'calls' || id === 'dev') return revealed('callsContent') && headerReady();
  if (id === 'specifications') return headerReady();
  return false;
}

function syncTourDock(): void {
  const dock = document.querySelector<HTMLElement>('[data-tour-dock]');
  if (!dock) return;
  const id = tourForPath();
  const button = dock.querySelector<HTMLButtonElement>('[data-tour-start]');
  if (!id || !headerReady()) {
    dock.hidden = true;
    return;
  }
  dock.hidden = false;
  if (button) {
    button.dataset.tourStart = id;
    button.setAttribute('aria-label', `Abrir recorrido: ${DOCK_LABELS[id]}`);
  }
}

export function startTour(id: TourId, source: 'auto' | 'manual' = 'manual'): void {
  stopTour();
  if (!tourHasVisibleAnchor(id)) {
    if (source === 'manual') showToast('No hay elementos visibles para este recorrido.', 'info');
    return;
  }

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const steps = TOUR_STEPS[id].filter((step) => {
    if (!step.element) return true;
    if (typeof step.element === 'function') return Boolean(step.element());
    if (typeof step.element === 'string') return Boolean(document.querySelector(step.element));
    return Boolean(step.element);
  });
  if (!steps.length) return;
  setDockEmotion(ACTIVE_EMOTION);
  const instance = driver({
    steps,
    popoverClass: 'hackakognia-tour',
    overlayColor: '#414141',
    overlayOpacity: 0.68,
    overlayClickBehavior: 'none',
    showProgress: true,
    progressText: '{{current}} de {{total}}',
    nextBtnText: 'Siguiente',
    prevBtnText: 'Anterior',
    doneBtnText: 'Finalizar',
    closeBtnLabel: 'Cerrar recorrido',
    allowClose: true,
    allowKeyboardControl: true,
    disableActiveInteraction: true,
    smoothScroll: !prefersReducedMotion,
    animate: !prefersReducedMotion,
    stagePadding: 10,
    stageRadius: 32,
    skipMissingElement: true,
    waitForElement: 400,
    onHighlightStarted: () => syncTourHeaderOffset(),
    onHighlighted: () => {
      syncTourHeaderOffset();
    },
    onPopoverRender: (popover) => {
      if (tourPopoverPosition) placeTourPopover(popover.wrapper, tourPopoverPosition.left, tourPopoverPosition.top);
    },
    onDoneClick: (_element, _step, { driver: active }) => {
      markCompleted(id);
      active.destroy();
    },
    onDestroyed: (_element, _step, { driver: destroyed }) => {
      resetTourPopoverPosition();
      if (currentTour === destroyed) {
        currentTour = null;
        activeTourId = null;
      }
      clearTourHeaderOffset();
      if (suppressDismiss) return;
      setDockEmotion(IDLE_EMOTION);
      if (!wasCompleted(id)) markDismissed(id);
    },
  });

  currentTour = instance;
  activeTourId = id;
  syncTourHeaderOffset();
  window.addEventListener('resize', syncTourHeaderOffset);
  instance.drive();
}

function clearRetry(): void {
  if (!retryTimer) return;
  window.clearTimeout(retryTimer);
  retryTimer = 0;
}

function scheduleRetry(): void {
  if (retryTimer || currentTour || retryAttempts > 40) return;
  retryAttempts += 1;
  retryTimer = window.setTimeout(() => {
    retryTimer = 0;
    syncTourDock();
    maybeStartScreenTour();
  }, 400);
}

function maybeStartScreenTour(): void {
  if (currentTour) return;
  const id = tourForPath();
  if (!id || id === 'call' || id === 'replay') {
    clearRetry();
    return;
  }
  if (wasCompleted(id) || wasDismissed(id)) {
    clearRetry();
    return;
  }
  if (!screenReady(id) || !tourHasVisibleAnchor(id)) {
    scheduleRetry();
    return;
  }
  clearRetry();
  startTour(id, 'auto');
}

function queueScreenTour(): void {
  if (queuedFrame) return;
  queuedFrame = window.requestAnimationFrame(() => {
    queuedFrame = 0;
    syncTourDock();
    maybeStartScreenTour();
  });
}

function ensureReadyObserver(): void {
  if (observedRoot === document.documentElement) return;
  observedRoot = document.documentElement;
  const observer = new MutationObserver(queueScreenTour);
  observer.observe(document.documentElement, {
    attributes: true,
    childList: true,
    subtree: true,
    attributeFilter: ['hidden', 'class', 'aria-hidden', 'inert', 'data-view'],
  });
}

export function installTourHandlers(): void {
  const runtime = window as Window & { __kogniaTour?: boolean };
  if (!runtime.__kogniaTour) {
    runtime.__kogniaTour = true;

    document.addEventListener('pointerdown', onTourPopoverPointerDown);
    document.addEventListener('pointermove', onTourPopoverPointerMove);
    document.addEventListener('pointerup', onTourPopoverPointerEnd);
    document.addEventListener('pointercancel', onTourPopoverPointerEnd);

    document.addEventListener('click', (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLElement>('[data-tour-start]');
      if (!button) return;
      const id = button.dataset.tourStart;
      if (!id || !isTourId(id)) return;
      event.preventDefault();
      startTour(id, 'manual');
    });

    document.addEventListener('astro:before-swap', () => {
      clearRetry();
      retryAttempts = 0;
      if (activeTourId && !wasCompleted(activeTourId)) markDismissed(activeTourId);
      stopTour();
    });
    document.addEventListener('astro:page-load', () => {
      retryAttempts = 0;
      queueScreenTour();
    });
  }

  ensureReadyObserver();
  queueScreenTour();
}
