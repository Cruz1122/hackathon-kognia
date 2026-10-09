import assert from 'node:assert/strict';
import { test } from 'node:test';
import { appRouteFromPath } from './active-route.ts';

test('static guide routes stay distinct with or without a trailing slash', () => {
  assert.equal(appRouteFromPath('/instructions'), 'instructions');
  assert.equal(appRouteFromPath('/instructions/'), 'instructions');
  assert.equal(appRouteFromPath('/specifications'), 'specifications');
  assert.equal(appRouteFromPath('/specifications/'), 'specifications');
});

test('nested product routes keep their section and do not fall through to analytics', () => {
  assert.equal(appRouteFromPath('/calls/demo'), 'calls');
  assert.equal(appRouteFromPath('/calls/saved/'), 'calls');
  assert.equal(appRouteFromPath('/dev/replay'), 'dev');
  assert.equal(appRouteFromPath('/analytics'), 'analytics');
  assert.equal(appRouteFromPath('/'), null);
});
