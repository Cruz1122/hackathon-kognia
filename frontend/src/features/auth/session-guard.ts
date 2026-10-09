export const accessTokenKey = 'kognia.auth.access-token';
export const conversationIdKey = 'kognia.auth.conversation-id';

export type SessionCheck = 'valid' | 'invalid' | 'unavailable';
type RefreshStatus = 'refreshed' | 'invalid' | 'unavailable';

/** Refresh the access token when this many seconds (or fewer) remain. */
const refreshThresholdSeconds = 300;

let refreshInFlight: Promise<RefreshStatus> | null = null;
type SessionGuardRuntime = {
  apiUrl: string;
  stop: () => void;
};

export function clearSession(): void {
  sessionStorage.removeItem(accessTokenKey);
  sessionStorage.removeItem(conversationIdKey);
}

function storedAccessToken(): string {
  return sessionStorage.getItem(accessTokenKey)?.trim() ?? '';
}

function decodeJwtExpiry(token: string): number | null {
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
    const claims = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof claims.exp === 'number' ? claims.exp : null;
  } catch {
    return null;
  }
}

/** True when the token expires within the refresh window or already expired. */
export function tokenNeedsRefresh(token: string, nowMs: number = Date.now()): boolean {
  const expiresAt = decodeJwtExpiry(token);
  if (expiresAt === null) return false;
  return expiresAt - nowMs / 1000 <= refreshThresholdSeconds;
}

async function requestTokenRefresh(apiUrl: string, fetchImpl: typeof fetch): Promise<RefreshStatus> {
  const token = storedAccessToken();
  if (!token) return 'invalid';
  try {
    const response = await fetchImpl(`${apiUrl}/auth/refresh`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
    if (response.status === 401 || response.status === 403) return 'invalid';
    if (!response.ok) return 'unavailable';
    const data = (await response.json()) as { access_token?: unknown };
    const refreshed = typeof data.access_token === 'string' ? data.access_token.trim() : '';
    if (!refreshed) return 'unavailable';
    sessionStorage.setItem(accessTokenKey, refreshed);
    return 'refreshed';
  } catch {
    return 'unavailable';
  }
}

async function refreshSession(apiUrl: string, fetchImpl: typeof fetch = fetch): Promise<RefreshStatus> {
  if (refreshInFlight) return refreshInFlight;
  const request = requestTokenRefresh(apiUrl, fetchImpl);
  let tracked: Promise<RefreshStatus>;
  tracked = request.finally(() => {
    if (refreshInFlight === tracked) refreshInFlight = null;
  });
  refreshInFlight = tracked;
  return tracked;
}

/**
 * Exchange the current access token for a fresh one.
 *
 * The boolean API is kept for existing callers: a rejected token is false,
 * while a temporary transport/backend failure remains true so the passive
 * session guard does not log the user out on a network blip.
 */
export async function refreshAccessToken(apiUrl: string): Promise<boolean> {
  return (await refreshSession(apiUrl)) !== 'invalid';
}

/** Refresh proactively when the stored token is close to expiring. */
export async function ensureFreshToken(apiUrl: string): Promise<RefreshStatus | 'not-needed'> {
  const token = storedAccessToken();
  if (!token || !tokenNeedsRefresh(token)) return 'not-needed';
  return refreshSession(apiUrl);
}

export async function checkSession(apiUrl: string): Promise<SessionCheck> {
  const refreshStatus = await ensureFreshToken(apiUrl);
  if (refreshStatus === 'invalid') return 'invalid';
  const token = storedAccessToken();
  const conversationId = sessionStorage.getItem(conversationIdKey)?.trim();
  if (!token || !conversationId) return 'invalid';

  try {
    const headers = { Authorization: `Bearer ${token}` };
    const [me, conversation] = await Promise.all([
      fetch(`${apiUrl}/auth/me`, { headers, cache: 'no-store' }),
      fetch(`${apiUrl}/conversations/${encodeURIComponent(conversationId)}`, { headers, cache: 'no-store' }),
    ]);
    if (me.ok && conversation.ok) return 'valid';
    if ([me, conversation].some((response) => response.status === 401)) return 'invalid';
    return 'unavailable';
  } catch {
    return 'unavailable';
  }
}

function apiBase(apiUrl: string): string {
  return apiUrl.replace(/\/+$/, '');
}

function isApiRequest(requestUrl: string, apiUrl: string): boolean {
  const base = apiBase(apiUrl);
  return !base || requestUrl === base || requestUrl.startsWith(`${base}/`);
}

function isRefreshRequest(requestUrl: string, apiUrl: string): boolean {
  return requestUrl === `${apiBase(apiUrl)}/auth/refresh`;
}

function hasBearerAuthorization(request: Request): boolean {
  return /^bearer\s+/i.test(request.headers.get('Authorization') ?? '');
}

function requestWithToken(request: Request, token: string): Request {
  const headers = new Headers(request.headers);
  headers.set('Authorization', `Bearer ${token}`);
  return new Request(request, { headers });
}

/**
 * Add the auth recovery boundary to fetch calls made by the app.
 *
 * Only API requests that already carry a Bearer header are retried. The
 * refresh endpoint is explicitly excluded to avoid a refresh loop. A request
 * body remains replayable because the first attempt uses a cloned Request.
 */
export function createAuthenticatedFetch(
  apiUrl: string,
  fetchImpl: typeof fetch = fetch,
  onSessionInvalid: () => void = redirectToLogin,
): typeof fetch {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    const response = await fetchImpl(request.clone());
    if (
      response.status !== 401
      || !isApiRequest(request.url, apiUrl)
      || isRefreshRequest(request.url, apiUrl)
      || !hasBearerAuthorization(request)
    ) {
      return response;
    }

    const refreshStatus = await refreshSession(apiUrl, fetchImpl);
    const refreshedToken = storedAccessToken();
    if (refreshStatus === 'invalid' || (refreshStatus === 'refreshed' && !refreshedToken)) {
      onSessionInvalid();
      return response;
    }
    if (refreshStatus !== 'refreshed') return response;

    const retry = await fetchImpl(requestWithToken(request, refreshedToken));
    if (retry.status === 401) onSessionInvalid();
    return retry;
  };
}

/** Install the recovery boundary once for the current browser document. */
export function installAuthFetch(apiUrl: string): void {
  const runtime = window as Window & { __kogniaAuthFetchInstalled?: boolean };
  if (runtime.__kogniaAuthFetchInstalled) return;
  const originalFetch = window.fetch.bind(window);
  window.fetch = createAuthenticatedFetch(apiUrl, originalFetch);
  runtime.__kogniaAuthFetchInstalled = true;
}

export function redirectToLogin(): void {
  clearSession();
  window.location.replace('/');
}

export function installSessionGuard(apiUrl: string, intervalMs = 15000): () => void {
  const runtime = window as Window & { __kogniaSessionGuard?: SessionGuardRuntime };
  if (runtime.__kogniaSessionGuard?.apiUrl === apiUrl) return runtime.__kogniaSessionGuard.stop;
  runtime.__kogniaSessionGuard?.stop();

  let disposed = false;
  let checking = false;

  const check = async (): Promise<void> => {
    if (disposed || checking) return;
    checking = true;
    const state = await checkSession(apiUrl);
    checking = false;
    if (!disposed && state === 'invalid') redirectToLogin();
  };

  const onVisibilityChange = (): void => {
    if (!document.hidden) void check();
  };

  const timer = window.setInterval(() => { void check(); }, intervalMs);
  document.addEventListener('visibilitychange', onVisibilityChange);
  void check();
  const stop = (): void => {
    disposed = true;
    window.clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    if (runtime.__kogniaSessionGuard?.stop === stop) delete runtime.__kogniaSessionGuard;
  };
  runtime.__kogniaSessionGuard = { apiUrl, stop };
  return stop;
}
