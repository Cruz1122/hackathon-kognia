import { strict as assert } from 'node:assert';
import test from 'node:test';
import { CancellationController } from './cancellation-controller.ts';

test('invalidates callbacks from an obsolete generation', () => {
  const cancellation = new CancellationController();
  const first = cancellation.begin();
  cancellation.cancel('user-interrupted');
  const second = cancellation.begin();

  assert.equal(first.signal.aborted, true);
  assert.equal(cancellation.isCurrent(first.id), false);
  assert.equal(cancellation.isCurrent(second.id), true);
});
