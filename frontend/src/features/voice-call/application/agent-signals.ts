import { EMPTY_AGENT_SIGNALS } from '../../../lib/agent-signals/model';
import {
  parseAgentSignalsEnvelope,
  updateAgentSignals,
  type AgentSignalsPanelElement,
} from '../../../lib/agent-signals/dom';

let announcedEmergency = false;

function panel(): AgentSignalsPanelElement | null {
  const target = document.getElementById('callAgentSignals');
  return target instanceof HTMLElement ? target as AgentSignalsPanelElement : null;
}

export function applyCallAgentSignals(payload: unknown): boolean {
  const target = panel();
  const envelope = parseAgentSignalsEnvelope(payload);
  if (!target || !envelope) return false;
  const emergency = envelope.behavior?.next_step === 'emergency_services';
  announcedEmergency = emergency;
  updateAgentSignals(target, envelope);
  return true;
}

export function resetCallAgentSignals(): void {
  announcedEmergency = false;
  const target = panel();
  if (!target) return;
  target.dataset.hasObservedData = 'false';
  target.resetSignalHistory?.();
  updateAgentSignals(target, EMPTY_AGENT_SIGNALS);
}
