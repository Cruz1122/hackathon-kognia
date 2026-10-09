import type {
  AgentBehavior,
  AgentSignalsEnvelope,
  AgentSignalsSnapshot,
  CategoryKey,
  CategorySnapshot,
  GradientStops,
  ScaleKey,
  ScaleSnapshot,
  Signal,
} from './types';

const RED: GradientStops = ['#ffc0b7', '#ff9586', '#eb6856', '#c94232'];
const ORANGE: GradientStops = ['#ffe1a7', '#ffc978', '#f0a84c', '#d77735'];
const YELLOW: GradientStops = ['#fff0bf', '#fadd8d', '#f7c974', '#d8a745'];
const YELLOW_GREEN: GradientStops = ['#edf5ad', '#d3e982', '#a8d864', '#78b54d'];
const GREEN: GradientStops = ['#d4f5bf', '#a8e68b', '#78cf62', '#4aa847'];
const NEUTRAL: GradientStops = ['#f8f8f8', '#faeccf', '#f7c974', '#414141'];
const GRAY: GradientStops = ['#ebebeb', '#d7d7d7', '#b8b8b8', '#939393'];

export const SIGNAL_EMOTION_GRADIENTS = {
  angry: RED,
  sad: ORANGE,
  surprised: YELLOW,
  intimidated: YELLOW_GREEN,
  'default-happy': GREEN,
  unknown: GRAY,
} as const;

export const JEV_EMOTION_FACES = {
  frustrated: 'angry',
  sad: 'sad',
  surprised: 'surprised',
  worried: 'intimidated',
  relieved: 'default-happy',
  unknown: 'unknown',
} as const;

const SATISFACTION = { very_low: 0, low: 1.25, neutral: 2.5, high: 3.75, very_high: 5 } as const;
const TENSION = { very_low: 5, low: 3.75, neutral: 2.5, high: 1.25, very_high: 0 } as const;
const INTEGRITY_RISK = { supported: 0, uncertain: 2.5, unsupported: 5 } as const;

const EMOTIONS = ['angry', 'sad', 'surprised', 'intimidated', 'default-happy'] as const;

const clamp = (value: number, min = 0, max = 5): number => Math.min(max, Math.max(min, value));

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

class IntegrityRiskWindow {
  readonly #values: number[] = [];

  push(value: number): void {
    this.#values.push(value);
    if (this.#values.length > 10) this.#values.shift();
  }

  risk(): number {
    let current = 0;
    for (const value of this.#values) {
      if (value >= 5) current = 5;
      else if (value >= 2.5) current = Math.max(current * .9, 2.5);
      else current *= .82;
    }
    return clamp(current);
  }

  get empty(): boolean {
    return this.#values.length === 0;
  }

  clear(): void {
    this.#values.length = 0;
  }
}

function aggregateIntegrityRisk(probabilities: Partial<Record<keyof typeof INTEGRITY_RISK, number>>): number {
  const unsupported = Math.max(0, Math.min(1, probabilities.unsupported ?? 0));
  const uncertain = Math.max(0, Math.min(1, probabilities.uncertain ?? 0));
  const severeRisk = 1 - (1 - unsupported) ** 3;
  return 5 * Math.min(1, severeRisk + uncertain * .5 * (1 - severeRisk));
}

function scale(
  key: ScaleKey,
  title: string,
  score: number,
  topLabel: string,
  bottomLabel: string,
  unknown = false,
  stale = false,
  invertPercentage = false,
): ScaleSnapshot {
  const normalized = unknown ? 2.5 : clamp(score);
  const percentage = unknown ? 0 : Math.round((invertPercentage ? 1 - normalized / 5 : normalized / 5) * 100);
  return { key, title, score: normalized, percentage, unknown, stale, topLabel, bottomLabel };
}

function readOrdinal<TLabel extends string>(
  signal: Signal<TLabel> | undefined,
  anchors: Readonly<Record<TLabel, number>>,
): { score: number; unknown: boolean } {
  if (!signal || signal.value === 'unknown') return { score: 2.5, unknown: true };
  return { score: centroid(signal.probabilities, anchors), unknown: false };
}

interface CategoryDefinition {
  label: string;
  icon: string;
  gradient: GradientStops;
  face?: string;
}

const CATEGORY_CONFIG: Record<CategoryKey, { title: string; unknown: CategoryDefinition; values: Record<string, CategoryDefinition> }> = {
  emotion: {
    title: 'Emoción',
    unknown: { label: 'Sin identificar', icon: '', gradient: GRAY, face: JEV_EMOTION_FACES.unknown },
    values: {
      frustrated: { label: 'Frustración', icon: '', gradient: RED, face: JEV_EMOTION_FACES.frustrated },
      sad: { label: 'Tristeza', icon: '', gradient: ORANGE, face: JEV_EMOTION_FACES.sad },
      surprised: { label: 'Sorpresa', icon: '', gradient: YELLOW, face: JEV_EMOTION_FACES.surprised },
      worried: { label: 'Preocupación', icon: '', gradient: YELLOW_GREEN, face: JEV_EMOTION_FACES.worried },
      relieved: { label: 'Alivio', icon: '', gradient: GREEN, face: JEV_EMOTION_FACES.relieved },
    },
  },
  intent: {
    title: 'Intención',
    unknown: { label: 'Sin identificar', icon: 'circle-question-mark', gradient: GRAY },
    values: {
      buscar_ips: { label: 'Buscar IPS', icon: 'search', gradient: NEUTRAL },
      informacion_ips: { label: 'Información de IPS', icon: 'hospital', gradient: NEUTRAL },
      capacidad_ips: { label: 'Capacidad', icon: 'bed-double', gradient: NEUTRAL },
      comparar_ips: { label: 'Comparar IPS', icon: 'chart-column', gradient: NEUTRAL },
      orientacion_salud: { label: 'Orientación', icon: 'compass', gradient: NEUTRAL },
      fuera_alcance: { label: 'Fuera de alcance', icon: 'circle-slash', gradient: ORANGE },
      emergencia: { label: 'Emergencia', icon: 'siren', gradient: RED },
    },
  },
  behavior: {
    title: 'Comportamiento',
    unknown: { label: 'Sin identificar', icon: 'circle-question-mark', gradient: GRAY },
    values: {
      emergency_services: { label: 'Orientar a emergencias', icon: 'siren', gradient: RED },
      rephrase_with_evidence: { label: 'Reformular con evidencia', icon: 'shield-check', gradient: ORANGE },
      explain_scope: { label: 'Explicar alcance', icon: 'circle-slash', gradient: YELLOW },
      correct_search: { label: 'Corregir y simplificar', icon: 'rotate-ccw', gradient: YELLOW },
      ask_one_clarification: { label: 'Pedir una aclaración', icon: 'message-circle-question-mark', gradient: YELLOW },
      offer_alternative: { label: 'Ofrecer alternativa', icon: 'split', gradient: YELLOW },
      facilitate_closing: { label: 'Facilitar el cierre', icon: 'check-check', gradient: GREEN },
      query_data: { label: 'Consultar datos', icon: 'search', gradient: NEUTRAL },
      compare_data: { label: 'Comparar datos', icon: 'chart-column', gradient: NEUTRAL },
      explain_simply: { label: 'Explicar en simple', icon: 'lightbulb', gradient: NEUTRAL },
      continue: { label: 'Continuar', icon: 'message-circle', gradient: NEUTRAL },
    },
  },
};

function category(key: CategoryKey, value: string, detail?: string): CategorySnapshot {
  const config = CATEGORY_CONFIG[key];
  const selected = config.values[value] ?? config.unknown;
  return { key, title: config.title, value: config.values[value] ? value : 'unknown', ...selected, detail };
}

function behaviorDetail(behavior: AgentBehavior | undefined): string | undefined {
  if (!behavior) return undefined;
  const tone = behavior.tone === 'calm' ? 'Tono calmado' : 'Tono natural';
  const length = behavior.response_length === 'short' ? 'Respuesta corta' : 'Respuesta normal';
  return `${tone} · ${length}`;
}

export class AgentSignalsProjector {
  readonly #integrityRisk = new IntegrityRiskWindow();

  reset(): void {
    this.#integrityRisk.clear();
  }

  project(envelope: AgentSignalsEnvelope): AgentSignalsSnapshot {
    const signals = envelope.signals;
    const satisfaction = readOrdinal(signals.satisfaction, SATISFACTION);
    const tension = readOrdinal(signals.frustration, TENSION);
    const fluency = readOrdinal(signals.fluency, SATISFACTION);

    if (!envelope.aggregated && signals.integrity) {
      const anchor = INTEGRITY_RISK[signals.integrity.value];
      if (typeof anchor === 'number') this.#integrityRisk.push(anchor);
    }
    const hallucinationUnknown = envelope.aggregated ? !signals.integrity : this.#integrityRisk.empty;
    const integrityRisk = envelope.aggregated && signals.integrity
      ? aggregateIntegrityRisk(signals.integrity.probabilities)
      : this.#integrityRisk.risk();
    const hallucinationDisplay = hallucinationUnknown ? 2.5 : 5 - integrityRisk;

    return {
      scales: {
        satisfaction: scale('satisfaction', 'Satisfacción', satisfaction.score, 'Muy alta', 'Muy baja', satisfaction.unknown),
        tension: scale('tension', 'Tensión', tension.score, 'Calma', 'Tensión', tension.unknown, false, true),
        fluency: scale('fluency', 'Fluidez de conversación', fluency.score, 'Fluida', 'Trabada', fluency.unknown),
        hallucination: scale(
          'hallucination', 'Alucinaciones del agente', hallucinationDisplay, 'Muy bajo', 'Muy alto',
          hallucinationUnknown, !signals.integrity && !hallucinationUnknown, true,
        ),
      },
      categories: {
        emotion: category('emotion', signals.emotion?.value ?? 'unknown'),
        intent: category('intent', signals.intent?.value ?? 'unknown'),
        behavior: category('behavior', envelope.behavior?.next_step ?? 'unknown', behaviorDetail(envelope.behavior)),
      },
    };
  }
}

export function snapScale(score: number, unknown = false): { index: number; position: number; score: number; emotion: string } {
  if (unknown) return { index: 2, position: .5, score: 2.5, emotion: 'unknown' };
  const index = Math.min(4, Math.max(0, Math.round((clamp(score) / 5) * 4)));
  return { index, position: index / 4, score: index * 1.25, emotion: EMOTIONS[index] };
}

export const EMPTY_AGENT_SIGNALS: AgentSignalsEnvelope = { signals: {} };
