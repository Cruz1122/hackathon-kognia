import type { AgentSignalsEnvelope } from './types';
import { EMPTY_AGENT_SIGNALS } from './model';

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

export function parseAgentSignalsEnvelope(value: unknown, aggregated = false): AgentSignalsEnvelope | null {
  const source = record(value);
  const signals = record(source.signals);
  if (!Object.keys(signals).length) return null;
  const ordinal = ['very_low', 'low', 'neutral', 'high', 'very_high'] as const;
  const confirmation = signals.confirmation
    ? signal(signals.confirmation, ['rejected', 'uncertain', 'explicit'] as const, 'uncertain')
    : undefined;
  const integrity = signals.integrity
    ? signal(signals.integrity, ['unsupported', 'uncertain', 'supported'] as const, 'uncertain')
    : undefined;
  const sampleCount = typeof source.sample_count === 'number' ? source.sample_count : undefined;
  return {
    aggregated,
    sampleCount,
    signals: {
      satisfaction: signal(signals.satisfaction, ordinal, EMPTY_AGENT_SIGNALS.signals.satisfaction.value),
      frustration: signal(signals.frustration, ordinal, EMPTY_AGENT_SIGNALS.signals.frustration.value),
      confirmation,
      integrity,
      intent: signal(signals.intent, ['continue', 'correct', 'cancel', 'callback', 'human', 'unknown'] as const, 'unknown'),
      human: signal(signals.human, ['requested', 'not_requested', 'unknown'] as const, 'unknown'),
      schedule_flexibility: signal(signals.schedule_flexibility, ['flexible', 'fixed', 'unknown'] as const, 'unknown'),
    },
  };
}
