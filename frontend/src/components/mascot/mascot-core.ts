// Adapted from Cruz1122/fleibo-face at commit 31064aab4bb757daafd8cd798194187d06cf1e39.
// Colors are intentionally mapped to Kognia's UI palette.
export interface FaceState {
  faceX: number;
  faceY: number;
  faceWidth: number;
  faceHeight: number;
  faceRadius: number;
  faceRotation: number;
  leftEyeX: number;
  leftEyeY: number;
  leftEyeWidth: number;
  leftEyeHeight: number;
  leftEyeRadius: number;
  leftEyeRotation: number;
  rightEyeX: number;
  rightEyeY: number;
  rightEyeWidth: number;
  rightEyeHeight: number;
  rightEyeRadius: number;
  rightEyeRotation: number;
  mouthX: number;
  mouthY: number;
  mouthWidth: number;
  mouthCurve: number;
  mouthLeftOffset: number;
  mouthRightOffset: number;
  mouthControlSpread: number;
  mouthStroke: number;
  mouthRotation: number;
}

export interface EmotionDefinition {
  label: string;
  state: FaceState;
  blink?: boolean;
}

export type EmotionMap = Record<string, EmotionDefinition>;

export const GRAPHITE = '#414141';
export const AMBER = '#f7c974';

export const DEFAULT_EMOTIONS: EmotionMap = {
  'default-happy': {
    label: 'Happy',
    state: {
      faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: 0,
      leftEyeX: 24, leftEyeY: -34, leftEyeWidth: 11, leftEyeHeight: 34, leftEyeRadius: 9, leftEyeRotation: -12,
      rightEyeX: 63, rightEyeY: -33, rightEyeWidth: 11, rightEyeHeight: 34, rightEyeRadius: 9, rightEyeRotation: -12,
      mouthX: 39, mouthY: 10, mouthWidth: 92, mouthCurve: 30, mouthLeftOffset: 0, mouthRightOffset: 3,
      mouthControlSpread: 0.19, mouthStroke: 8, mouthRotation: 7,
    },
  },
  intimidated: {
    label: 'Intimidated',
    state: {
      faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: 0,
      leftEyeX: 11, leftEyeY: -27, leftEyeWidth: 10, leftEyeHeight: 10, leftEyeRadius: 9, leftEyeRotation: -12,
      rightEyeX: 63, rightEyeY: -24, rightEyeWidth: 10, rightEyeHeight: 10, rightEyeRadius: 9, rightEyeRotation: 20,
      mouthX: 36, mouthY: -2, mouthWidth: 20, mouthCurve: 8, mouthLeftOffset: 0, mouthRightOffset: 3,
      mouthControlSpread: 0.19, mouthStroke: 8, mouthRotation: 1,
    },
  },
  privacy: {
    label: 'Privacy',
    state: {
      faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: 0,
      leftEyeX: 18, leftEyeY: -28, leftEyeWidth: 22, leftEyeHeight: 7, leftEyeRadius: 4, leftEyeRotation: -8,
      rightEyeX: 59, rightEyeY: -28, rightEyeWidth: 22, rightEyeHeight: 7, rightEyeRadius: 4, rightEyeRotation: -8,
      mouthX: 39, mouthY: 13, mouthWidth: 44, mouthCurve: 2, mouthLeftOffset: 0, mouthRightOffset: 1,
      mouthControlSpread: 0.2, mouthStroke: 7, mouthRotation: 5,
    },
  },
};

export const FACE_STATE_KEYS = Object.keys(DEFAULT_EMOTIONS['default-happy'].state) as (keyof FaceState)[];

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function lerp(from: number, to: number, progress: number): number {
  return from + (to - from) * progress;
}

export function easeInOutCubic(progress: number): number {
  const value = clamp(progress, 0, 1);
  return value < 0.5 ? 4 * value ** 3 : 1 - ((-2 * value + 2) ** 3) / 2;
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

export function roundedRectPath(cx: number, cy: number, width: number, height: number, radius: number): string {
  const w = Math.max(0.001, Math.abs(width));
  const h = Math.max(0.001, Math.abs(height));
  const r = Math.max(0, Math.min(Math.abs(radius), w / 2, h / 2));
  const x = cx - w / 2;
  const y = cy - h / 2;
  const x2 = x + w;
  const y2 = y + h;
  return [
    `M ${formatNumber(x + r)} ${formatNumber(y)}`,
    `H ${formatNumber(x2 - r)}`,
    `A ${formatNumber(r)} ${formatNumber(r)} 0 0 1 ${formatNumber(x2)} ${formatNumber(y + r)}`,
    `V ${formatNumber(y2 - r)}`,
    `A ${formatNumber(r)} ${formatNumber(r)} 0 0 1 ${formatNumber(x2 - r)} ${formatNumber(y2)}`,
    `H ${formatNumber(x + r)}`,
    `A ${formatNumber(r)} ${formatNumber(r)} 0 0 1 ${formatNumber(x)} ${formatNumber(y2 - r)}`,
    `V ${formatNumber(y + r)}`,
    `A ${formatNumber(r)} ${formatNumber(r)} 0 0 1 ${formatNumber(x + r)} ${formatNumber(y)}`,
    'Z',
  ].join(' ');
}

export function rotationTransform(angle: number, cx: number, cy: number): string {
  return `rotate(${formatNumber(angle)} ${formatNumber(cx)} ${formatNumber(cy)})`;
}

export function mouthPath(state: FaceState): string {
  const half = state.mouthWidth / 2;
  const leftY = state.mouthY + state.mouthLeftOffset;
  const rightY = state.mouthY + state.mouthRightOffset;
  const x1 = state.mouthX - half;
  const x2 = state.mouthX + half;
  const spread = state.mouthWidth * state.mouthControlSpread;
  return `M ${formatNumber(x1)} ${formatNumber(leftY)} C ${formatNumber(x1 + spread)} ${formatNumber(leftY + state.mouthCurve)} ${formatNumber(x2 - spread)} ${formatNumber(rightY + state.mouthCurve)} ${formatNumber(x2)} ${formatNumber(rightY)}`;
}

export function interpolateFaceState(from: FaceState, to: FaceState, progress: number): FaceState {
  const state = {} as FaceState;
  for (const key of FACE_STATE_KEYS) state[key] = lerp(from[key], to[key], progress) as never;
  return state;
}
