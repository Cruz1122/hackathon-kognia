import { strict as assert } from 'node:assert';
import test from 'node:test';
import {
  accessTokenKey,
  createAuthenticatedFetch,
  redirectToLogin,
  refreshAccessToken,
  tokenNeedsRefresh,
} from './session-guard.ts';

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type TestGlobals = { sessionStorage: StorageLike; fetch: typeof fetch };
type BrowserGlobals = { window?: { location: { replace: (url: string) => void } } };
const globals = globalThis as unknown as TestGlobals;
const browserGlobals = globalThis as unknown as BrowserGlobals;

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

test('redirectToLogin clears the session and navigates back to the login route', () => {
  const storage = installStorage({ [accessTokenKey]: 'expired-token' });
  const originalWindow = browserGlobals.window;
  let destination = '';
  browserGlobals.window = { location: { replace: (url) => { destination = url; } } };
  try {
    redirectToLogin();
    assert.equal(destination, '/');
    assert.equal(storage.store.has(accessTokenKey), false);
  } finally {
    if (originalWindow) browserGlobals.window = originalWindow;
    else delete browserGlobals.window;
    storage.restore();
  }
});

test('authenticated fetch refreshes once and retries a request with the new token', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  const requestTokens: string[] = [];
  let refreshCalls = 0;
  const originalFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (request.url === 'http://api/auth/refresh') {
      refreshCalls += 1;
      return new Response(JSON.stringify({ access_token: 'new-token' }), { status: 200 });
    }
    requestTokens.push(request.headers.get('Authorization') ?? '');
    return requestTokens.length === 1
      ? new Response('{}', { status: 401 })
      : new Response('{"ok":true}', { status: 200 });
  };
  const guardedFetch = createAuthenticatedFetch('http://api', originalFetch, () => {
    throw new Error('The session should have recovered.');
  });

  try {
    const response = await guardedFetch('http://api/protected', {
      headers: { Authorization: 'Bearer old-token' },
    });
    assert.equal(response.status, 200);
    assert.deepEqual(requestTokens, ['Bearer old-token', 'Bearer new-token']);
    assert.equal(refreshCalls, 1);
    assert.equal(storage.store.get(accessTokenKey), 'new-token');
  } finally {
    storage.restore();
  }
});

test('authenticated fetch replays a request body after refreshing', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  const requestBodies: string[] = [];
  const originalFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (request.url === 'http://api/auth/refresh') {
      return new Response(JSON.stringify({ access_token: 'new-token' }), { status: 200 });
    }
    requestBodies.push(await request.text());
    return requestBodies.length === 1
      ? new Response('{}', { status: 401 })
      : new Response('{"ok":true}', { status: 200 });
  };
  const guardedFetch = createAuthenticatedFetch('http://api', originalFetch, () => {
    throw new Error('The request body should have been replayed.');
  });
  const body = JSON.stringify({ prompt: 'ping' });

  try {
    const response = await guardedFetch('http://api/protected', {
      method: 'POST',
      headers: { Authorization: 'Bearer old-token', 'Content-Type': 'application/json' },
      body,
    });
    assert.equal(response.status, 200);
    assert.deepEqual(requestBodies, [body, body]);
  } finally {
    storage.restore();
  }
});

test('authenticated fetch invalidates the session when refresh is rejected', async () => {
  const storage = installStorage({ [accessTokenKey]: 'expired-token' });
  let invalidated = false;
  const originalFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (request.url === 'http://api/auth/refresh') return new Response('{}', { status: 401 });
    return new Response('{}', { status: 401 });
  };
  const guardedFetch = createAuthenticatedFetch('http://api', originalFetch, () => {
    invalidated = true;
  });

  try {
    const response = await guardedFetch('http://api/protected', {
      headers: { Authorization: 'Bearer expired-token' },
    });
    assert.equal(response.status, 401);
    assert.equal(invalidated, true);
  } finally {
    storage.restore();
  }
});

test('authenticated fetch keeps the session when refresh is temporarily unavailable', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  let invalidated = false;
  const originalFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (request.url === 'http://api/auth/refresh') throw new TypeError('Network unavailable');
    return new Response('{}', { status: 401 });
  };
  const guardedFetch = createAuthenticatedFetch('http://api', originalFetch, () => {
    invalidated = true;
  });

  try {
    const response = await guardedFetch('http://api/protected', {
      headers: { Authorization: 'Bearer old-token' },
    });
    assert.equal(response.status, 401);
    assert.equal(invalidated, false);
    assert.equal(storage.store.get(accessTokenKey), 'old-token');
  } finally {
    storage.restore();
  }
});

test('authenticated fetch shares one refresh across concurrent 401 responses', async () => {
  const storage = installStorage({ [accessTokenKey]: 'old-token' });
  let refreshCalls = 0;
  let protectedCalls = 0;
  const originalFetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (request.url === 'http://api/auth/refresh') {
      refreshCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return new Response(JSON.stringify({ access_token: 'new-token' }), { status: 200 });
    }
    protectedCalls += 1;
    return protectedCalls <= 2
      ? new Response('{}', { status: 401 })
      : new Response('{"ok":true}', { status: 200 });
  };
  const guardedFetch = createAuthenticatedFetch('http://api', originalFetch, () => {
    throw new Error('The concurrent refresh should have recovered.');
  });

  try {
    const [first, second] = await Promise.all([
      guardedFetch('http://api/first', { headers: { Authorization: 'Bearer old-token' } }),
      guardedFetch('http://api/second', { headers: { Authorization: 'Bearer old-token' } }),
    ]);
    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(refreshCalls, 1);
  } finally {
    storage.restore();
  }
});
