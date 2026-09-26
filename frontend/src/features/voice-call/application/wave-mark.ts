const MARK_RADIUS = 10;
export const WAVE_BLEED = MARK_RADIUS;

const ICONS = {
  phone: ['M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384'],
  'phone-off': [
    'M10.1 13.9a14 14 0 0 0 3.732 2.668 1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2 18 18 0 0 1-12.728-5.272',
    'M22 2 2 22',
    'M4.76 13.582A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 .244.473',
  ],
  bot: ['M12 8V4H8', 'M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z', 'M2 14h2', 'M20 14h2', 'M15 13v2', 'M9 13v2'],
  'book-search': ['M11 22H5.5a1 1 0 0 1 0-5h4.501', 'm21 22-1.879-1.878', 'M3 19.5v-15A2.5 2.5 0 0 1 5.5 2H18a1 1 0 0 1 1 1v8', 'M17 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'],
  'triangle-alert': ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3', 'M12 9v4', 'M12 17h.01'],
} as const;

export type EventMarkIcon = keyof typeof ICONS;

const MARK_NAMES = new Set<string>(Object.keys(ICONS));

export function eventMarkIcon(item: HTMLElement): EventMarkIcon | null {
  if (item.classList.contains('message-row')) return null;
  const named = item.querySelector('svg')?.getAttribute('class')?.match(/lucide-([\w-]+)/)?.[1]
    ?? item.querySelector('i[data-lucide]')?.getAttribute('data-lucide')
    ?? '';
  if (MARK_NAMES.has(named)) return named as EventMarkIcon;
  if (item.classList.contains('call-ended')) return 'phone-off';
  if (item.querySelector('[data-rag="true"]')) return 'book-search';
  if (item.classList.contains('tool-row')) return 'bot';
  if (item.classList.contains('system-event')) return 'phone';
  return null;
}

export function sampleSeries(series: number[], x: number, width: number): number {
  const span = Math.max(1, series.length - 1);
  const exact = Math.min(span, Math.max(0, (x / Math.max(1, width)) * span));
  const left = Math.floor(exact);
  const right = Math.min(span, left + 1);
  const blend = exact - left;
  const eased = blend * blend * (3 - 2 * blend);
  const levelAt = (index: number) => {
    const prev = series[Math.max(0, index - 1)] ?? 0;
    const value = series[index] ?? 0;
    const next = series[Math.min(series.length - 1, index + 1)] ?? value;
    return prev * 0.22 + value * 0.56 + next * 0.22;
  };
  return levelAt(left) + (levelAt(right) - levelAt(left)) * eased;
}

export function crestFromLevel(level: number, baseline: number, height: number): number {
  const amplitude = Math.min(height - 1, 5 + Math.pow(Math.min(1, Math.max(0, level)), 1.25) * height * 0.5);
  return baseline - amplitude;
}

export function drawEventMark(
  context: CanvasRenderingContext2D,
  x: number,
  baseline: number,
  width: number,
  icon: EventMarkIcon,
): void {
  const lineX = Math.min(width, Math.max(0, x));
  const ballX = lineX;
  const ballY = MARK_RADIUS;
  context.save();
  context.beginPath();
  context.setLineDash([2, 3]);
  context.moveTo(lineX, 0);
  context.lineTo(lineX, baseline);
  context.strokeStyle = '#414141';
  context.lineWidth = 2;
  context.lineCap = 'butt';
  context.stroke();
  context.restore();
  context.beginPath();
  context.arc(ballX, ballY, MARK_RADIUS, 0, Math.PI * 2);
  context.fillStyle = '#414141';
  context.fill();
  context.save();
  context.translate(ballX, ballY);
  context.scale(12 / 24, 12 / 24);
  context.translate(-12, -12);
  context.strokeStyle = '#f8f8f8';
  context.lineWidth = 2.4;
  context.lineCap = 'round';
  context.lineJoin = 'round';
  for (const path of ICONS[icon]) context.stroke(new Path2D(path));
  context.restore();
}
