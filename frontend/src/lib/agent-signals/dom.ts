import type { AgentSignalsEnvelope } from './types';

export interface AgentSignalsPanelElement extends HTMLElement {
  setSignals?: (data: AgentSignalsEnvelope) => void;
  resetSignalHistory?: () => void;
  startDemo?: () => void;
  stopDemo?: () => void;
}

export function updateAgentSignals(target: AgentSignalsPanelElement, data: AgentSignalsEnvelope): void {
  target.dispatchEvent(new CustomEvent<AgentSignalsEnvelope>('agent-signals:update', { detail: data }));
}
