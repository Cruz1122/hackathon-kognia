import type { AgentSignalsEnvelope } from './types';

const certain = <T extends string>(value: T) => ({ value, probabilities: { [value]: 1 } as Partial<Record<T, number>> });

export const DEMO_FRAMES: readonly AgentSignalsEnvelope[] = [
  {
    signals: {
      satisfaction: { value: 'low', probabilities: { very_low: .2, low: .8 } },
      frustration: { value: 'very_high', probabilities: { high: .4, very_high: .6 } },
      fluency: { value: 'very_low', probabilities: { very_low: .36, low: .64 } },
      emotion: certain('frustrated'),
      intent: certain('buscar_ips'),
      integrity: certain('supported'),
    },
    behavior: { tone: 'calm', response_length: 'short', next_step: 'correct_search' },
  },
  {
    signals: {
      emotion: certain('unknown'),
      intent: certain('unknown'),
    },
    behavior: { tone: 'natural', response_length: 'normal', next_step: 'continue' },
  },
  {
    signals: {
      satisfaction: certain('low'),
      frustration: { value: 'high', probabilities: { neutral: .15, high: .55, very_high: .3 } },
      fluency: certain('low'),
      emotion: certain('worried'),
      intent: certain('emergencia'),
      integrity: certain('supported'),
    },
    behavior: { tone: 'calm', response_length: 'short', next_step: 'emergency_services' },
  },
  {
    signals: {
      satisfaction: certain('neutral'),
      frustration: certain('neutral'),
      fluency: certain('neutral'),
      emotion: certain('worried'),
      intent: certain('capacidad_ips'),
      integrity: certain('supported'),
    },
    behavior: { tone: 'calm', response_length: 'normal', next_step: 'query_data' },
  },
  {
    signals: {
      satisfaction: certain('very_high'),
      frustration: certain('very_low'),
      fluency: certain('very_high'),
      emotion: certain('relieved'),
      intent: certain('unknown'),
      integrity: certain('supported'),
    },
    behavior: { tone: 'natural', response_length: 'short', next_step: 'facilitate_closing' },
  },
];
