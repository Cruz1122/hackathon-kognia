import { strict as assert } from 'node:assert';
import test from 'node:test';
import { AgentSignalsProjector, EMPTY_AGENT_SIGNALS, snapScale } from './model.ts';
import type { AgentSignalsEnvelope, IntegrityValue, OrdinalValue } from './types.ts';

function frame(overrides: Partial<AgentSignalsEnvelope['signals']> = {}, behavior?: AgentSignalsEnvelope['behavior']): AgentSignalsEnvelope {
  return { signals: { ...EMPTY_AGENT_SIGNALS.signals, ...overrides }, behavior };
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

test('places unknown in the middle with the gray face', () => {
  const snapped = snapScale(0, true);
  assert.equal(snapped.position, .5);
  assert.equal(snapped.emotion, 'unknown');
  const snapshot = new AgentSignalsProjector().project(frame({
    satisfaction: ordinal('unknown'),
    frustration: ordinal('unknown'),
    fluency: ordinal('unknown'),
  }));
  assert.equal(snapshot.scales.satisfaction.unknown, true);
  assert.equal(snapshot.scales.satisfaction.score, 2.5);
  assert.equal(snapshot.scales.tension.unknown, true);
  assert.equal(snapshot.scales.fluency.unknown, true);
  assert.equal(snapshot.scales.hallucination.unknown, true);
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

test('uses only the fluency signal', () => {
  const snapshot = new AgentSignalsProjector().project(frame({
    fluency: { value: 'high', probabilities: { high: 1 } },
  }));
  assert.equal(snapshot.scales.fluency.percentage, 75);
  assert.equal(snapshot.scales.fluency.unknown, false);
  assert.equal(new AgentSignalsProjector().project(frame()).scales.fluency.unknown, true);
});

test('projects dashboard aggregates from averaged probabilities', () => {
  const aggregate = new AgentSignalsProjector().project({
    ...frame({
      fluency: { value: 'neutral', probabilities: { very_low: .25, neutral: .5, very_high: .25 } },
      integrity: { value: 'supported', probabilities: { unsupported: .2, uncertain: .2, supported: .6 } },
    }),
    aggregated: true,
  });
  assert.equal(aggregate.scales.fluency.score, 2.5);
  assert.equal(aggregate.scales.hallucination.percentage, 54);
  assert.equal(aggregate.scales.hallucination.unknown, false);
});

test('shows a lower hallucination percentage when the risk is low', () => {
  const integrity = (value: IntegrityValue) => ({ value, probabilities: { [value]: 1 } });
  const supported = new AgentSignalsProjector().project(frame({ integrity: integrity('supported') }));
  const unsupported = new AgentSignalsProjector().project(frame({ integrity: integrity('unsupported') }));
  assert.equal(supported.scales.hallucination.score, 5);
  assert.equal(supported.scales.hallucination.percentage, 0);
  assert.equal(unsupported.scales.hallucination.score, 0);
  assert.equal(unsupported.scales.hallucination.percentage, 100);
});

test('keeps hallucination unknown until the first integrity verdict', () => {
  const projector = new AgentSignalsProjector();
  assert.equal(projector.project(frame()).scales.hallucination.unknown, true);
  assert.equal(projector.project(frame({ integrity: { value: 'unsupported', probabilities: { unsupported: 1 } } })).scales.hallucination.percentage, 100);
  assert.equal(projector.project(frame({ integrity: { value: 'supported', probabilities: { supported: 1 } } })).scales.hallucination.percentage, 82);
});

test('labels emotion, intent and behavior', () => {
  const snapshot = new AgentSignalsProjector().project(frame({
    emotion: { value: 'frustrated', probabilities: { frustrated: 1 } },
    intent: { value: 'buscar_ips', probabilities: { buscar_ips: 1 } },
  }, { tone: 'calm', response_length: 'short', next_step: 'correct_search' }));
  assert.equal(snapshot.categories.emotion.label, 'Frustración');
  assert.equal(snapshot.categories.emotion.face, 'angry');
  assert.equal(snapshot.categories.intent.label, 'Buscar IPS');
  assert.equal(snapshot.categories.intent.icon, 'search');
  assert.equal(snapshot.categories.behavior.label, 'Corregir y simplificar');
  assert.equal(snapshot.categories.behavior.icon, 'rotate-ccw');
  assert.equal(snapshot.categories.behavior.detail, 'Tono calmado · Respuesta corta');
  const unknown = new AgentSignalsProjector().project(frame());
  assert.equal(unknown.categories.emotion.label, 'Sin identificar');
  assert.equal(unknown.categories.emotion.face, 'unknown');
  assert.equal(unknown.categories.intent.gradient[0], '#ebebeb');
});
