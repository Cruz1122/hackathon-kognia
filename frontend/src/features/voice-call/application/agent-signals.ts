import { EMPTY_AGENT_SIGNALS } from '../../../lib/agent-signals/model';
import {
  parseAgentSignalsEnvelope,
  updateAgentSignals,
  type AgentSignalsPanelElement,
} from '../../../lib/agent-signals/dom';

function panel(): AgentSignalsPanelElement | null {
  const target = document.getElementById('callAgentSignals');
  return target instanceof HTMLElement ? target as AgentSignalsPanelElement : null;
}

export function applyCallAgentSignals(payload: unknown): boolean {
  const target = panel();
  const envelope = parseAgentSignalsEnvelope(payload);
  if (!target || !envelope) return false;
  updateAgentSignals(target, envelope);
  return true;
}

export function resetCallAgentSignals(): void {
  const target = panel();
  if (!target) return;
  target.dataset.hasObservedData = 'false';
  target.resetSignalHistory?.();
  updateAgentSignals(target, EMPTY_AGENT_SIGNALS);
}
