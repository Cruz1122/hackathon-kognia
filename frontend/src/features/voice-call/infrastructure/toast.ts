export type ToastVariant = 'success' | 'info' | 'warning' | 'error' | 'neutral';

const ICONS: Record<ToastVariant, { face: string; target: string }> = {
  success: { face: 'smile', target: 'phone' },
  info: { face: 'laugh', target: 'message-circle' },
  warning: { face: 'meh', target: 'triangle-alert' },
  error: { face: 'frown', target: 'bug' },
  neutral: { face: 'annoyed', target: 'clock-3' },
};

export function showToast(message: string, variant: ToastVariant = 'info'): void {
  const stack = document.querySelector('.toast-stack');
  if (!stack) return;
  const icons = ICONS[variant];
  const stage = document.createElement('div');
  stage.className = `toast-stage toast--${variant}`;
  stage.innerHTML = `
    <div class="toast-shell">
      <div class="toast-shell__body"></div>
      <div class="toast-shell__cap toast-shell__cap--left"></div>
      <div class="toast-shell__cap toast-shell__cap--right"></div>
    </div>
    <div class="toast-translate">
      <div class="toast-rotate">
        <div class="toast-mass"></div>
        <div class="toast-icon-layer">
          <i class="toast-icon toast-icon--face" data-lucide="${icons.face}"></i>
          <i class="toast-icon toast-icon--target" data-lucide="${icons.target}"></i>
        </div>
      </div>
    </div>
    <div class="toast-message"></div>
  `;
  const copy = stage.querySelector('.toast-message');
  if (copy) copy.textContent = message;
  stack.prepend(stage);
  const lucide = (window as Window & { lucide?: { createIcons: (opts?: object) => void } }).lucide;
  lucide?.createIcons({ attrs: { 'stroke-width': 2.5 } });
  window.setTimeout(() => stage.remove(), 4650);
}
