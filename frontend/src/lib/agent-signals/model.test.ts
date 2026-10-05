import { strict as assert } from 'node:assert';
import test from 'node:test';
import { AgentSignalsProjector, EMPTY_AGENT_SIGNALS, snapScale } from './model.ts';
import type { AgentSignalsEnvelope, ConfirmationValue, IntegrityValue, OrdinalValue } from './types.ts';

function frame(overrides: Partial<AgentSignalsEnvelope['signals']> = {}): AgentSignalsEnvelope {
  return { signals: { ...EMPTY_AGENT_SIGNALS.signals, ...overrides } };
}

function ordinal(value: OrdinalValue) {
  return { value, probabilities: { [value]: 1 } };
}

test('snaps the continuous score to the five Fleybo anchors', () => {
  assert.deepEqual([0, 1.25, 2.5, 3.75, 5].map((score) => snapScale(score).emotion), [
    'angry',
    'sad',
    'surprised',
    'intimidated',
    'default-happy',
  ]);
  assert.equal(snapScale(3.1).score, 2.5);
  assert.equal(snapScale(3.2).score, 3.75);
});

test('projects satisfaction and inverted tension with continuous percentages', () => {
  const snapshot = new AgentSignalsProjector().project(frame({
    satisfaction: { value: 'high', probabilities: { neutral: .4, high: .6 } },
    frustration: ordinal('very_high'),
  }));
  assert.equal(snapshot.scales.satisfaction.score, 3.25);
  assert.equal(snapshot.scales.satisfaction.percentage, 65);
  assert.equal(snapshot.scales.tension.score, 0);
  assert.equal(snapshot.scales.tension.percentage, 100);
  assert.equal(snapScale(snapshot.scales.tension.score).emotion, 'angry');
});

test('uses the dedicated conversation fluency signal when available', () => {
  const snapshot = new AgentSignalsProjector().project(frame({
    fluency: { value: 'high', probabilities: { high: 1 } },
    confirmation: { value: 'rejected', probabilities: { rejected: 1 } },
  }));
  assert.equal(snapshot.scales.fluency.percentage, 75);
  assert.equal(snapshot.scales.fluency.stale, false);
});

test('keeps confirmation history unchanged when a cycle omits the signal', () => {
  const projector = new AgentSignalsProjector();
  const confirmation = (value: ConfirmationValue) => ({ value, probabilities: { [value]: 1 } });
  projector.project(frame({ confirmation: confirmation('explicit') }));
  const stale = projector.project(frame());
  assert.equal(stale.scales.fluency.score, 5);
  assert.equal(stale.scales.fluency.percentage, 100);
  assert.equal(stale.scales.fluency.stale, true);
});

test('uses only the last ten confirmation samples', () => {
  const projector = new AgentSignalsProjector();
  const signal = (value: ConfirmationValue) => ({ value, probabilities: { [value]: 1 } });
  projector.project(frame({ confirmation: signal('rejected') }));
  for (let index = 0; index < 10; index += 1) projector.project(frame({ confirmation: signal('explicit') }));
  const snapshot = projector.project(frame());
  assert.equal(snapshot.scales.fluency.score, 5);
});

test('projects dashboard aggregates from averaged probabilities without mutating call history', () => {
  const projector = new AgentSignalsProjector();
  projector.project(frame({ confirmation: { value: 'explicit', probabilities: { explicit: 1 } } }));
  const aggregate = projector.project({
    ...frame({
      confirmation: { value: 'uncertain', probabilities: { rejected: .25, uncertain: .5, explicit: .25 } },
      integrity: { value: 'supported', probabilities: { unsupported: .2, uncertain: .2, supported: .6 } },
    }),
    aggregated: true,
  });
  assert.equal(aggregate.scales.fluency.score, 2.5);
  assert.equal(aggregate.scales.hallucination.percentage, 54);
  const live = projector.project(frame());
  assert.equal(live.scales.fluency.score, 5);
});

test('shows a lower hallucination percentage when Fleybo is at the top', () => {
  const integrity = (value: IntegrityValue) => ({ value, probabilities: { [value]: 1 } });
  const supported = new AgentSignalsProjector().project(frame({ integrity: integrity('supported') }));
  const unsupported = new AgentSignalsProjector().project(frame({ integrity: integrity('unsupported') }));
  assert.equal(supported.scales.hallucination.score, 5);
  assert.equal(supported.scales.hallucination.percentage, 0);
  assert.equal(unsupported.scales.hallucination.score, 0);
  assert.equal(unsupported.scales.hallucination.percentage, 100);
});

test('starts hallucination risk at zero and penalizes unsupported claims immediately', () => {
  const projector = new AgentSignalsProjector();
  assert.equal(projector.project(frame()).scales.hallucination.percentage, 0);
  assert.equal(projector.project(frame({ integrity: { value: 'unsupported', probabilities: { unsupported: 1 } } })).scales.hallucination.percentage, 100);
  assert.equal(projector.project(frame({ integrity: { value: 'supported', probabilities: { supported: 1 } } })).scales.hallucination.percentage, 82);
});

test('normalizes categorical labels and Lucide icon names', () => {
  const snapshot = new AgentSignalsProjector().project(frame({
    intent: { value: 'human', probabilities: { human: 1 } },
    human: { value: 'requested', probabilities: { requested: 1 } },
    schedule_flexibility: { value: 'fixed', probabilities: { fixed: 1 } },
  }));
  assert.deepEqual(
    [snapshot.categories.intent.label, snapshot.categories.intent.icon],
    ['Transferir a humano', 'user-round'],
  );
  assert.equal(snapshot.categories.human.label, 'Solicitado');
  assert.equal(snapshot.categories.schedule.label, 'Fijo');
});
