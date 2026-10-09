import type { AgentBehavior, AgentSignalsEnvelope, EmotionValue, IntentValue, NextStep, OrdinalValue } from './types';

export interface AgentSignalsPanelElement extends HTMLElement {
  setSignals?: (data: AgentSignalsEnvelope) => void;
  resetSignalHistory?: () => void;
  startDemo?: () => void;
  stopDemo?: () => void;
  startGaze?: () => void;
  stopGaze?: () => void;
}

export function updateAgentSignals(target: AgentSignalsPanelElement, data: AgentSignalsEnvelope): void {
  target.dispatchEvent(new CustomEvent<AgentSignalsEnvelope>('agent-signals:update', { detail: data }));
}

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

const ORDINAL = ['very_low', 'low', 'neutral', 'high', 'very_high', 'unknown'] as const;
const EMOTIONS = ['frustrated', 'sad', 'surprised', 'worried', 'relieved', 'unknown'] as const;
const INTENTS = [
  'buscar_ips', 'informacion_ips', 'capacidad_ips', 'comparar_ips',
  'orientacion_salud', 'fuera_alcance', 'emergencia', 'unknown',
] as const;
const NEXT_STEPS = [
  'emergency_services', 'rephrase_with_evidence', 'explain_scope', 'correct_search',
  'ask_one_clarification', 'offer_alternative', 'facilitate_closing', 'query_data',
  'compare_data', 'explain_simply', 'continue',
] as const;

function signal<TValue extends string>(
  value: unknown,
  allowed: readonly TValue[],
  fallback: TValue,
): { value: TValue; probabilities: Partial<Record<TValue, number>> } {
  const source = record(value);
  const selected = allowed.includes(source.value as TValue) ? source.value as TValue : fallback;
  const rawProbabilities = record(source.probabilities);
  const probabilities: Partial<Record<TValue, number>> = {};
  for (const label of allowed) {
    const probability = rawProbabilities[label];
    if (typeof probability === 'number' && Number.isFinite(probability) && probability >= 0) {
      probabilities[label] = probability;
    }
  }
  if (!Object.keys(probabilities).length) probabilities[selected] = 1;
  return { value: selected, probabilities };
}

function optionalSignal<TValue extends string>(
  value: unknown,
  allowed: readonly TValue[],
  fallback: TValue,
): { value: TValue; probabilities: Partial<Record<TValue, number>> } | undefined {
  if (value == null) return undefined;
  return signal(value, allowed, fallback);
}

function parseBehavior(value: unknown): AgentBehavior | undefined {
  const source = record(value);
  const nextStep = source.next_step;
  if (typeof nextStep !== 'string' || !NEXT_STEPS.includes(nextStep as NextStep)) return undefined;
  return {
    tone: source.tone === 'calm' ? 'calm' : 'natural',
    response_length: source.response_length === 'short' ? 'short' : 'normal',
    next_step: nextStep as NextStep,
  };
}

export function parseAgentSignalsEnvelope(value: unknown, aggregated = false): AgentSignalsEnvelope | null {
  const source = record(value);
  const signals = record(source.signals);
  const behavior = parseBehavior(source.behavior);
  if (!Object.keys(signals).length && !behavior) return null;
  const integrity = optionalSignal(signals.integrity, ['unsupported', 'uncertain', 'supported'] as const, 'uncertain');
  const sampleCount = typeof source.sample_count === 'number' ? source.sample_count : undefined;
  return {
    aggregated,
    sampleCount,
    behavior,
    signals: {
      satisfaction: optionalSignal(signals.satisfaction, ORDINAL, 'unknown' satisfies OrdinalValue),
      frustration: optionalSignal(signals.frustration, ORDINAL, 'unknown'),
      fluency: optionalSignal(signals.fluency, ORDINAL, 'unknown'),
      integrity,
      emotion: optionalSignal(signals.emotion, EMOTIONS, 'unknown' satisfies EmotionValue),
      intent: optionalSignal(signals.intent, INTENTS, 'unknown' satisfies IntentValue),
    },
  };
}
