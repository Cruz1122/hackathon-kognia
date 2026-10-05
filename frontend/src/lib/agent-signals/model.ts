import type {
  AgentSignalsEnvelope,
  AgentSignalsSnapshot,
  CategoryKey,
  CategorySnapshot,
  GradientStops,
  ScaleKey,
  ScaleSnapshot,
} from './types';

const RED: GradientStops = ['#ffc0b7', '#ff9586', '#eb6856', '#c94232'];
const ORANGE: GradientStops = ['#ffe1a7', '#ffc978', '#f0a84c', '#d77735'];
const YELLOW: GradientStops = ['#fff0bf', '#fadd8d', '#f7c974', '#d8a745'];
const YELLOW_GREEN: GradientStops = ['#edf5ad', '#d3e982', '#a8d864', '#78b54d'];
const GREEN: GradientStops = ['#d4f5bf', '#a8e68b', '#78cf62', '#4aa847'];
const NEUTRAL: GradientStops = ['#f8f8f8', '#faeccf', '#f7c974', '#414141'];

export const SIGNAL_EMOTION_GRADIENTS = {
  angry: RED,
  sad: ORANGE,
  surprised: YELLOW,
  intimidated: YELLOW_GREEN,
  'default-happy': GREEN,
} as const;

const SATISFACTION = { very_low: 0, low: 1.25, neutral: 2.5, high: 3.75, very_high: 5 } as const;
const TENSION = { very_low: 5, low: 3.75, neutral: 2.5, high: 1.25, very_high: 0 } as const;
const CONFIRMATION = { rejected: 0, uncertain: 2.5, explicit: 5 } as const;
const INTEGRITY_RISK = { supported: 0, uncertain: 2.5, unsupported: 5 } as const;

const EMOTIONS = ['angry', 'sad', 'surprised', 'intimidated', 'default-happy'] as const;

const clamp = (value: number, min = 0, max = 5): number => Math.min(max, Math.max(min, value));

function mean(values: readonly number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function centroid<TLabel extends string>(
  probabilities: Partial<Record<TLabel, number>>,
  anchors: Readonly<Record<TLabel, number>>,
): number {
  let weighted = 0;
  let probabilityMass = 0;
  for (const [label, anchor] of Object.entries(anchors) as [TLabel, number][]) {
    const probability = probabilities[label];
    if (typeof probability !== 'number' || !Number.isFinite(probability) || probability <= 0) continue;
    weighted += probability * anchor;
    probabilityMass += probability;
  }
  return probabilityMass > 0 ? clamp(weighted / probabilityMass) : 2.5;
}

class RollingWindow {
  readonly #values: number[] = [];
  readonly #size: number;

  constructor(size = 10) {
    this.#size = size;
  }

  push(value: number): void {
    this.#values.push(value);
    if (this.#values.length > this.#size) this.#values.shift();
  }

  average(fallback = 2.5): number {
    return this.#values.length ? mean(this.#values) : fallback;
  }

  clear(): void {
    this.#values.length = 0;
  }
}

function scale(
  key: ScaleKey,
  title: string,
  score: number,
  topLabel: string,
  bottomLabel: string,
  stale = false,
  invertPercentage = false,
): ScaleSnapshot {
  const normalized = clamp(score);
  const percentage = Math.round((invertPercentage ? 1 - normalized / 5 : normalized / 5) * 100);
  return { key, title, score: normalized, percentage, stale, topLabel, bottomLabel };
}

interface CategoryDefinition {
  label: string;
  icon: string;
  gradient: GradientStops;
}

const CATEGORY_CONFIG: Record<CategoryKey, { title: string; unknown: CategoryDefinition; values: Record<string, CategoryDefinition> }> = {
  intent: {
    title: 'Intención',
    unknown: { label: 'Desconocido', icon: 'circle-help', gradient: NEUTRAL },
    values: {
      continue: { label: 'Continuar', icon: 'arrow-right', gradient: GREEN },
      correct: { label: 'Corregir', icon: 'pencil', gradient: YELLOW },
      callback: { label: 'Devolver llamada', icon: 'phone', gradient: YELLOW_GREEN },
      human: { label: 'Transferir a humano', icon: 'user-round', gradient: ORANGE },
      cancel: { label: 'Cancelar', icon: 'x', gradient: RED },
    },
  },
  human: {
    title: 'Humano',
    unknown: { label: 'Desconocido', icon: 'circle-help', gradient: NEUTRAL },
    values: {
      requested: { label: 'Solicitado', icon: 'user-round-check', gradient: YELLOW },
      not_requested: { label: 'No solicitado', icon: 'user-round-x', gradient: NEUTRAL },
    },
  },
  schedule: {
    title: 'Flexibilidad',
    unknown: { label: 'Desconocido', icon: 'circle-help', gradient: NEUTRAL },
    values: {
      flexible: { label: 'Flexible', icon: 'clock-arrow-up', gradient: GREEN },
      fixed: { label: 'Fijo', icon: 'lock-keyhole', gradient: RED },
    },
  },
};

function category(key: CategoryKey, value: string): CategorySnapshot {
  const config = CATEGORY_CONFIG[key];
  const selected = config.values[value] ?? config.unknown;
  return { key, title: config.title, value, ...selected };
}

export class AgentSignalsProjector {
  readonly #confirmation = new RollingWindow(10);
  readonly #integrityRisk = new RollingWindow(10);

  reset(): void {
    this.#confirmation.clear();
    this.#integrityRisk.clear();
  }

  project(envelope: AgentSignalsEnvelope): AgentSignalsSnapshot {
    const signals = envelope.signals;
    const satisfaction = centroid(signals.satisfaction.probabilities, SATISFACTION);
    const tension = centroid(signals.frustration.probabilities, TENSION);

    if (!envelope.aggregated) {
      if (signals.confirmation) this.#confirmation.push(CONFIRMATION[signals.confirmation.value]);
      if (signals.integrity) this.#integrityRisk.push(INTEGRITY_RISK[signals.integrity.value]);
    }

    const fluency = envelope.aggregated && signals.confirmation
      ? centroid(signals.confirmation.probabilities, CONFIRMATION)
      : this.#confirmation.average();
    const integrityRisk = envelope.aggregated && signals.integrity
      ? centroid(signals.integrity.probabilities, INTEGRITY_RISK)
      : this.#integrityRisk.average();
    const hallucinationDisplay = 5 - integrityRisk;

    return {
      scales: {
        satisfaction: scale('satisfaction', 'Satisfacción', satisfaction, 'Muy alta', 'Muy baja'),
        tension: scale('tension', 'Tensión', tension, 'Calma', 'Tensión'),
        fluency: scale('fluency', 'Fluidez de conversación', fluency, 'Fluida', 'Trabada', !signals.confirmation),
        hallucination: scale('hallucination', 'Alucinaciones del agente', hallucinationDisplay, 'Muy bajo', 'Muy alto', !signals.integrity, true),
      },
      categories: {
        intent: category('intent', signals.intent.value),
        human: category('human', signals.human.value),
        schedule: category('schedule', signals.schedule_flexibility.value),
      },
    };
  }
}

export function snapScale(score: number): { index: number; position: number; score: number; emotion: typeof EMOTIONS[number] } {
  const index = Math.min(4, Math.max(0, Math.round((clamp(score) / 5) * 4)));
  return { index, position: index / 4, score: index * 1.25, emotion: EMOTIONS[index] };
}

export const EMPTY_AGENT_SIGNALS: AgentSignalsEnvelope = {
  signals: {
    satisfaction: { value: 'neutral', probabilities: { neutral: 1 } },
    frustration: { value: 'neutral', probabilities: { neutral: 1 } },
    intent: { value: 'unknown', probabilities: { unknown: 1 } },
    human: { value: 'unknown', probabilities: { unknown: 1 } },
    schedule_flexibility: { value: 'unknown', probabilities: { unknown: 1 } },
  },
};
