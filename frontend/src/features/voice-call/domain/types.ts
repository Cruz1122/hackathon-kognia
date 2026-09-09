export type CallState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'interrupted'
  | 'ended'
  | 'error';

export type ChatMessage = { role: 'user' | 'assistant'; content: string };

export type VoiceMetrics = {
  speechEndedAt?: number;
  transcriptFinalAt?: number;
  agentSentAt?: number;
  firstTokenAt?: number;
  firstChunkAt?: number;
  speechStartedAt?: number;
  completedAt?: number;
  chunks: number;
  interrupted: boolean;
  cancellationReason?: string;
};

export type VoiceSnapshot = {
  state: CallState;
  partialTranscript: string;
  lastUserMessage: string;
  assistantText: string;
  warning: string;
  error: string;
  muted: boolean;
  callStartedAt?: number;
  metrics: VoiceMetrics;
  history: ChatMessage[];
};

export const emptyMetrics = (): VoiceMetrics => ({ chunks: 0, interrupted: false });
