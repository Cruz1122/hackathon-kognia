export type OrdinalValue = 'very_low' | 'low' | 'neutral' | 'high' | 'very_high' | 'unknown';
export type IntegrityValue = 'unsupported' | 'uncertain' | 'supported';
export type EmotionValue = 'frustrated' | 'sad' | 'surprised' | 'worried' | 'relieved' | 'unknown';
export type IntentValue =
  | 'buscar_ips'
  | 'informacion_ips'
  | 'capacidad_ips'
  | 'comparar_ips'
  | 'orientacion_salud'
  | 'fuera_alcance'
  | 'emergencia'
  | 'unknown';
export type NextStep =
  | 'emergency_services'
  | 'rephrase_with_evidence'
  | 'explain_scope'
  | 'correct_search'
  | 'ask_one_clarification'
  | 'offer_alternative'
  | 'facilitate_closing'
  | 'query_data'
  | 'compare_data'
  | 'explain_simply'
  | 'continue';

export interface Signal<TValue extends string> {
  value: TValue;
  probabilities: Partial<Record<TValue, number>>;
}

export interface AgentBehavior {
  tone: 'calm' | 'natural';
  response_length: 'short' | 'normal';
  next_step: NextStep;
}

export interface AgentSignals {
  satisfaction?: Signal<OrdinalValue>;
  frustration?: Signal<OrdinalValue>;
  fluency?: Signal<OrdinalValue>;
  integrity?: Signal<IntegrityValue>;
  emotion?: Signal<EmotionValue>;
  intent?: Signal<IntentValue>;
}

export interface AgentSignalsEnvelope {
  signals: AgentSignals;
  behavior?: AgentBehavior;
  aggregated?: boolean;
  sampleCount?: number;
}

export type ScaleKey = 'satisfaction' | 'tension' | 'fluency' | 'hallucination';
export type CategoryKey = 'emotion' | 'intent' | 'behavior';
export type GradientStops = readonly [string, string, string, string];

export interface ScaleSnapshot {
  key: ScaleKey;
  title: string;
  score: number;
  percentage: number;
  unknown: boolean;
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
  face?: string;
  detail?: string;
}

export interface AgentSignalsSnapshot {
  scales: Record<ScaleKey, ScaleSnapshot>;
  categories: Record<CategoryKey, CategorySnapshot>;
}
