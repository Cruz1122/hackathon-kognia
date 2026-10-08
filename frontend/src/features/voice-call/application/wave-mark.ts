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
const ICON_PATHS = Object.fromEntries(
  (Object.keys(ICONS) as EventMarkIcon[]).map((icon) => [icon, ICONS[icon].map((path) => new Path2D(path))]),
) as Record<EventMarkIcon, Path2D[]>;

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

export type WaveMarkTone = 'default' | 'accent' | 'error';
export type WaveMark = { ratio: number; icon: EventMarkIcon; tone?: WaveMarkTone };

const waveSize = new WeakMap<HTMLCanvasElement, { width: number; height: number; dpr: number }>();

function themeColor(variable: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable).trim();
  return value || fallback;
}

export function measureWave(canvas: HTMLCanvasElement): { width: number; height: number; dpr: number } {
  const rect = (canvas.parentElement ?? canvas).getBoundingClientRect();
  return { width: Math.max(1, rect.width), height: Math.max(1, rect.height), dpr: Math.min(window.devicePixelRatio || 1, 2) };
}

function canvasBox(canvas: HTMLCanvasElement): { width: number; height: number; dpr: number } {
  const cached = waveSize.get(canvas);
  if (cached) return cached;
  const measured = measureWave(canvas);
  waveSize.set(canvas, measured);
  return measured;
}

export function resizeWave(canvas: HTMLCanvasElement, context: CanvasRenderingContext2D): void {
  const box = measureWave(canvas);
  waveSize.set(canvas, box);
  canvas.width = Math.round((box.width + WAVE_BLEED * 2) * box.dpr);
  canvas.height = Math.round(box.height * box.dpr);
  context.setTransform(box.dpr, 0, 0, box.dpr, WAVE_BLEED * box.dpr, 0);
}

function sampleSeries(series: number[], x: number, width: number): number {
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

function crestFromLevel(level: number, baseline: number, height: number): number {
  const amplitude = Math.min(height - 1, 5 + Math.pow(Math.min(1, Math.max(0, level)), 1.25) * height * 0.5);
  return baseline - amplitude;
}

function drawEventMark(
  context: CanvasRenderingContext2D,
  x: number,
  baseline: number,
  width: number,
  icon: EventMarkIcon,
  tone: WaveMarkTone,
  accent: string,
): void {
  const lineX = Math.min(width, Math.max(0, x));
  const ballX = lineX;
  const ballY = MARK_RADIUS;
  const markColor = tone === 'error' ? '#d55353' : tone === 'accent' ? accent : '#414141';
  context.save();
  context.beginPath();
  context.setLineDash([2, 3]);
  context.moveTo(lineX, 0);
  context.lineTo(lineX, baseline);
  context.strokeStyle = markColor;
  context.lineWidth = 2;
  context.lineCap = 'butt';
  context.stroke();
  context.restore();
  context.beginPath();
  context.arc(ballX, ballY, MARK_RADIUS, 0, Math.PI * 2);
  context.fillStyle = markColor;
  context.fill();
  context.save();
  context.translate(ballX, ballY);
  context.scale(12 / 24, 12 / 24);
  context.translate(-12, -12);
  context.strokeStyle = '#f8f8f8';
  context.lineWidth = 2.4;
  context.lineCap = 'round';
  context.lineJoin = 'round';
  for (const path of ICON_PATHS[icon]) context.stroke(path);
  context.restore();
}

export function paintCallWave(
  context: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  customerPeaks: number[],
  agentPeaks: number[],
  progress: number,
  marks: WaveMark[],
  flat = false,
): void {
  const accent = themeColor('--ui-accent', '#f7c974');
  const accentSoft = themeColor('--ui-accent-soft', '#faeccf');
  const measured = measureWave(canvas);
  const cached = waveSize.get(canvas);
  if (!cached || cached.width !== measured.width || cached.height !== measured.height || cached.dpr !== measured.dpr) {
    resizeWave(canvas, context);
  }
  const { width, height, dpr } = canvasBox(canvas);
  const held = Math.min(1, Math.max(0, progress));
  context.setTransform(dpr, 0, 0, dpr, WAVE_BLEED * dpr, 0);
  context.clearRect(-WAVE_BLEED, 0, width + WAVE_BLEED * 2, height);
  const baseline = height - 1;
  const radius = 5;
  const head = Math.min(width, Math.max(0, held * width));
  const paintMarks = () => {
    for (const mark of marks) {
      if (mark.ratio < 0 || mark.ratio > 1) continue;
      drawEventMark(context, mark.ratio * width, baseline, width, mark.icon, mark.tone ?? 'default', accent);
    }
  };
  if (flat) {
    context.beginPath();
    context.moveTo(0, baseline);
    context.lineTo(head, baseline);
    context.strokeStyle = accent;
    context.lineWidth = 3;
    context.lineCap = 'butt';
    context.stroke();
    context.beginPath();
    context.moveTo(head, baseline);
    context.lineTo(width, baseline);
    context.strokeStyle = 'rgba(65,65,65,.22)';
    context.stroke();
    paintMarks();
    const ballY = Math.min(height - radius, Math.max(radius, baseline));
    context.beginPath();
    context.moveTo(head, ballY);
    context.lineTo(head, baseline);
    context.strokeStyle = '#414141';
    context.lineWidth = 2;
    context.stroke();
    context.beginPath();
    context.arc(head, ballY, radius, 0, Math.PI * 2);
    context.fillStyle = '#414141';
    context.fill();
    return;
  }
  const count = Math.max(customerPeaks.length, agentPeaks.length, 1);
  const heard = new Array<number>(count);
  for (let index = 0; index < count; index += 1) heard[index] = Math.max(customerPeaks[index] ?? 0, agentPeaks[index] ?? 0);
  const span = Math.max(1, count - 1);
  const smooth = (series: number[], index: number) => {
    const prev = series[Math.max(0, index - 1)] ?? 0;
    const value = series[index] ?? 0;
    const next = series[Math.min(series.length - 1, index + 1)] ?? value;
    return prev * 0.22 + value * 0.56 + next * 0.22;
  };
  const shape = new Path2D();
  const yAt = (index: number) => baseline - (5 + Math.pow(Math.min(1, smooth(heard, index)), 1.25) * height * 0.5);
  shape.moveTo(0, baseline);
  shape.lineTo(0, yAt(0));
  for (let index = 1; index < count - 1; index += 1) {
    const currentX = (index / span) * width;
    const nextX = ((index + 1) / span) * width;
    const currentY = yAt(index);
    const nextY = yAt(index + 1);
    shape.quadraticCurveTo(currentX, currentY, (currentX + nextX) / 2, (currentY + nextY) / 2);
  }
  shape.lineTo(width, yAt(count - 1));
  shape.lineTo(width, baseline);
  shape.closePath();
  if (head < width - 0.5) {
    context.save();
    context.beginPath();
    context.rect(head, 0, width - head, height);
    context.clip();
    context.fillStyle = 'rgba(65,65,65,.14)';
    context.fill(shape);
    context.restore();
  }
  context.save();
  context.beginPath();
  context.rect(0, 0, head, height);
  context.clip();
  context.fillStyle = accent;
  context.fill(shape);
  let runStart = -1;
  const paintAgentRun = (runEnd: number) => {
    if (runStart < 0) return;
    const x0 = (runStart / span) * width;
    const runWidth = Math.max(1, ((runEnd - runStart) / span) * width);
    const fade = Math.min(0.42, Math.max(0.12, 22 / runWidth));
    const voice = context.createLinearGradient(x0, 0, x0 + runWidth, 0);
    voice.addColorStop(0, accent);
    voice.addColorStop(fade, accentSoft);
    voice.addColorStop(1 - fade, accentSoft);
    voice.addColorStop(1, accent);
    context.save();
    context.beginPath();
    context.rect(x0, 0, runWidth, height);
    context.clip();
    context.fillStyle = voice;
    context.fill(shape);
    context.restore();
    runStart = -1;
  };
  for (let index = 0; index < count; index += 1) {
    const agent = smooth(agentPeaks, index);
    const customer = smooth(customerPeaks, index);
    const agentSpeaking = agent >= 0.02 && agent >= customer;
    if (agentSpeaking && runStart < 0) runStart = index;
    if (!agentSpeaking && runStart >= 0) paintAgentRun(index);
  }
  if (runStart >= 0) paintAgentRun(count - 1);
  context.restore();
  paintMarks();
  const crest = crestFromLevel(sampleSeries(heard, head, width), baseline, height);
  context.beginPath();
  context.moveTo(head, Math.min(baseline, crest));
  context.lineTo(head, baseline);
  context.strokeStyle = '#414141';
  context.lineWidth = 2;
  context.lineCap = 'butt';
  context.stroke();
  context.beginPath();
  context.arc(head, Math.min(height - radius, Math.max(radius, crest)), radius, 0, Math.PI * 2);
  context.fillStyle = '#414141';
  context.fill();
}
