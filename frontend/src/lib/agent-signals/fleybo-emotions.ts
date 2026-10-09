import {
  DEFAULT_EMOTIONS,
  type EmotionDefinition,
  type EmotionMap,
} from '../../components/mascot/mascot-core';
import { SIGNAL_EMOTION_GRADIENTS } from './model';
import type { GradientStops } from './types';

export type GradientEmotionDefinition = EmotionDefinition & { gradient: GradientStops };
export type GradientEmotionMap = EmotionMap & Record<string, GradientEmotionDefinition>;

// The current canonical core intentionally exposes only the states used by the
// application shell. These three state definitions are the missing original
// Fleybo states supplied by fleybo-agent-signals-astro; MascotFace remains the
// only renderer and animation engine.
const SURPRISED: EmotionDefinition = {
  label: 'Surprised',
  blink: true,
  state: {
    faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: 0,
    leftEyeX: 24, leftEyeY: -34, leftEyeWidth: 13, leftEyeHeight: 38, leftEyeRadius: 9, leftEyeRotation: -12,
    rightEyeX: 63, rightEyeY: -33, rightEyeWidth: 13, rightEyeHeight: 38, rightEyeRadius: 9, rightEyeRotation: -12,
    mouthX: 50, mouthY: 10, mouthWidth: 0, mouthCurve: 0, mouthLeftOffset: 0, mouthRightOffset: 3,
    mouthControlSpread: .2, mouthStroke: 8, mouthRotation: 0,
  },
};

const SAD: EmotionDefinition = {
  label: 'Sad',
  blink: true,
  state: {
    faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: 0,
    leftEyeX: 24, leftEyeY: -34, leftEyeWidth: 11, leftEyeHeight: 26, leftEyeRadius: 9, leftEyeRotation: -12,
    rightEyeX: 63, rightEyeY: -33, rightEyeWidth: 11, rightEyeHeight: 26, rightEyeRadius: 9, rightEyeRotation: -12,
    mouthX: 39, mouthY: 26, mouthWidth: 92, mouthCurve: -30, mouthLeftOffset: 0, mouthRightOffset: 3,
    mouthControlSpread: .19, mouthStroke: 8, mouthRotation: 7,
  },
};

const ANGRY: EmotionDefinition = {
  label: 'Angry',
  blink: false,
  state: {
    faceX: 0, faceY: 0, faceWidth: 200, faceHeight: 200, faceRadius: 100, faceRotation: -6,
    leftEyeX: 19, leftEyeY: -15, leftEyeWidth: 8, leftEyeHeight: 25, leftEyeRadius: 11, leftEyeRotation: -73,
    rightEyeX: 65, rightEyeY: -22, rightEyeWidth: 8, rightEyeHeight: 23, rightEyeRadius: 9, rightEyeRotation: -128,
    mouthX: 45, mouthY: 12, mouthWidth: 33, mouthCurve: -7, mouthLeftOffset: 0, mouthRightOffset: 3,
    mouthControlSpread: .2, mouthStroke: 7, mouthRotation: -23,
  },
};

export const SIGNAL_EMOTIONS: GradientEmotionMap = {
  ...DEFAULT_EMOTIONS,
  angry: { ...ANGRY, gradient: SIGNAL_EMOTION_GRADIENTS.angry },
  sad: { ...SAD, gradient: SIGNAL_EMOTION_GRADIENTS.sad },
  surprised: { ...SURPRISED, gradient: SIGNAL_EMOTION_GRADIENTS.surprised },
  intimidated: { ...DEFAULT_EMOTIONS.intimidated, gradient: SIGNAL_EMOTION_GRADIENTS.intimidated },
  'default-happy': { ...DEFAULT_EMOTIONS['default-happy'], gradient: SIGNAL_EMOTION_GRADIENTS['default-happy'] },
  unknown: { ...SURPRISED, gradient: SIGNAL_EMOTION_GRADIENTS.unknown },
  privacy: {
    ...DEFAULT_EMOTIONS.privacy,
    gradient: SIGNAL_EMOTION_GRADIENTS.surprised,
  },
};
