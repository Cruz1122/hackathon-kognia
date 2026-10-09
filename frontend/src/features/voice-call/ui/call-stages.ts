export const CALL_STAGES = [
  { id: 'inicio', label: 'Inicio' },
  { id: 'entendiendo', label: 'Entendiendo' },
  { id: 'aclarando', label: 'Aclarando' },
  { id: 'buscando', label: 'Buscando' },
  { id: 'respondiendo', label: 'Respondiendo' },
  { id: 'cierre', label: 'Cierre' },
] as const;

const SPECIAL_STAGES: Record<string, string> = {
  fuera_alcance: 'Fuera de alcance',
  emergencia: 'Emergencia',
};

type StageWindow = Window & { setCallStage?: (stage: string) => void };

let currentStage = '';

export function stageFromSignals(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '';
  const record = payload as Record<string, unknown>;
  const nested = record.state;
  const state = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : record;
  const stage = typeof state.stage === 'string' ? state.stage : '';
  return stage;
}

export function setCallStage(stage: string): void {
  currentStage = stage;
  const list = document.getElementById('callStages');
  const kicker = document.getElementById('callStageKicker');
  if (!(list instanceof HTMLOListElement)) return;
  const special = SPECIAL_STAGES[stage];
  const activeIndex = CALL_STAGES.findIndex((item) => item.id === stage);
  if (kicker) {
    kicker.textContent = special
      ? special
      : activeIndex >= 0
        ? CALL_STAGES[activeIndex].label
        : 'Esperando';
  }
  list.querySelectorAll<HTMLElement>('[data-stage]').forEach((item) => {
    const id = item.dataset.stage ?? '';
    const index = CALL_STAGES.findIndex((entry) => entry.id === id);
    const active = !special && id === stage;
    const done = !special && activeIndex > index && index >= 0;
    item.classList.toggle('is-active', active);
    item.classList.toggle('is-done', done);
    item.classList.toggle('is-dim', Boolean(special));
    item.setAttribute('aria-current', active ? 'step' : 'false');
  });
  const specialNode = list.querySelector<HTMLElement>('[data-special]');
  if (specialNode) {
    specialNode.hidden = !special;
    specialNode.textContent = special ?? '';
    specialNode.classList.toggle('is-active', Boolean(special));
    specialNode.classList.toggle('is-emergency', stage === 'emergencia');
  }
}

export function mountCallStages(): () => void {
  const list = document.getElementById('callStages');
  if (!(list instanceof HTMLOListElement)) return () => undefined;
  list.replaceChildren();
  for (const stage of CALL_STAGES) {
    const item = document.createElement('li');
    item.dataset.stage = stage.id;
    item.textContent = stage.label;
    list.append(item);
  }
  const special = document.createElement('li');
  special.dataset.special = 'true';
  special.hidden = true;
  list.append(special);
  setCallStage(currentStage);
  (window as StageWindow).setCallStage = setCallStage;
  return () => {
    if ((window as StageWindow).setCallStage === setCallStage) delete (window as StageWindow).setCallStage;
  };
}
