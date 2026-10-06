import { strict as assert } from 'node:assert';
import test from 'node:test';
import {
  accessTokenKey,
  refreshAccessToken,
  tokenNeedsRefresh,
} from './session-guard.ts';

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type TestGlobals = { sessionStorage: StorageLike; fetch: typeof fetch };
const globals = globalThis as unknown as TestGlobals;

function tokenWithExpiry(expiresAtSeconds: number): string {
  const encode = (value: unknown): string => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({ sub: 'user', type: 'access', exp: expiresAtSeconds })}.signature`;
}

function installStorage(initial: Record<string, string>): { store: Map<string, string>; restore: () => void } {
  const store = new Map(Object.entries(initial));
  const originalStorage = globals.sessionStorage;
  const originalFetch = globals.fetch;
  globals.sessionStorage = {
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => { store.set(key, value); },
    removeItem: (key) => { store.delete(key); },
  };
  return {
    store,
    restore: () => {
      globals.sessionStorage = originalStorage;
      globals.fetch = originalFetch;
    },
  };
}

test('tokenNeedsRefresh detects near-expiry and expired tokens', () => {
  const now = Date.UTC(2026, 0, 1);
  const nowSeconds = now / 1000;

  assert.equal(tokenNeedsRefresh(tokenWithExpiry(nowSeconds + 600), now), false);
  assert.equal(tokenNeedsRefresh(tokenWithExpiry(nowSeconds + 300), now), true);
  assert.equal(tokenNeedsRefresh(tokenWithExpiry(nowSeconds - 1), now), true);
});

test('tokenNeedsRefresh ignores undecodable tokens', () => {
  assert.equal(tokenNeedsRefresh('not-a-jwt'), false);
  assert.equal(tokenNeedsRefresh('header.payload.signature'), false);
});

test('refreshAccessToken keeps the session when the backend rejects the token', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  globals.fetch = async () => new Response('{}', { status: 401 });
  try {
    assert.equal(await refreshAccessToken('http://api'), false);
    assert.equal(storage.store.get(accessTokenKey), 'old-token');
  } finally {
    storage.restore();
  }
});

test('refreshAccessToken keeps the session on transient network errors', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  globals.fetch = async () => { throw new TypeError('Failed to fetch'); };
  try {
    assert.equal(await refreshAccessToken('http://api'), true);
    assert.equal(storage.store.get(accessTokenKey), 'old-token');
  } finally {
    storage.restore();
  }
});

test('refreshAccessToken stores the renewed token', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  globals.fetch = async () => new Response(JSON.stringify({ access_token: 'new-token' }), { status: 200 });
  try {
    assert.equal(await refreshAccessToken('http://api'), true);
    assert.equal(storage.store.get(accessTokenKey), 'new-token');
  } finally {
    storage.restore();
  }
});
