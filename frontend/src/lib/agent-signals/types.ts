export type OrdinalValue = 'very_low' | 'low' | 'neutral' | 'high' | 'very_high';
export type ConfirmationValue = 'rejected' | 'uncertain' | 'explicit';
export type IntegrityValue = 'unsupported' | 'uncertain' | 'supported';
export type IntentValue = 'continue' | 'correct' | 'cancel' | 'callback' | 'human' | 'unknown';
export type HumanValue = 'requested' | 'not_requested' | 'unknown';
export type ScheduleValue = 'flexible' | 'fixed' | 'unknown';

export interface Signal<TValue extends string> {
  value: TValue;
  probabilities: Partial<Record<TValue, number>>;
}

export interface AgentSignals {
  satisfaction: Signal<OrdinalValue>;
  frustration: Signal<OrdinalValue>;
  fluency?: Signal<OrdinalValue>;
  confirmation?: Signal<ConfirmationValue>;
  integrity?: Signal<IntegrityValue>;
  intent: Signal<IntentValue>;
  human: Signal<HumanValue>;
  schedule_flexibility: Signal<ScheduleValue>;
}

export interface AgentSignalsEnvelope {
  signals: AgentSignals;
  aggregated?: boolean;
  sampleCount?: number;
}

export type ScaleKey = 'satisfaction' | 'tension' | 'fluency' | 'hallucination';
export type CategoryKey = 'intent' | 'human' | 'schedule';
export type GradientStops = readonly [string, string, string, string];

export interface ScaleSnapshot {
  key: ScaleKey;
  title: string;
  score: number;
  percentage: number;
  stale: boolean;
  topLabel: string;
  bottomLabel: string;
}

export interface CategorySnapshot {
  key: CategoryKey;
  title: string;
  value: string;
  label: string;
  icon: string;
  gradient: GradientStops;
}

export interface AgentSignalsSnapshot {
  scales: Record<ScaleKey, ScaleSnapshot>;
  categories: Record<CategoryKey, CategorySnapshot>;
}
