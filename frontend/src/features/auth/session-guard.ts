export const accessTokenKey = 'kognia.auth.access-token';
export const conversationIdKey = 'kognia.auth.conversation-id';

export type SessionCheck = 'valid' | 'invalid' | 'unavailable';

/** Refresh the access token when this many seconds (or fewer) remain. */
const refreshThresholdSeconds = 300;

export function clearSession(): void {
  sessionStorage.removeItem(accessTokenKey);
  sessionStorage.removeItem(conversationIdKey);
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

/**
 * Exchange the current access token for a fresh one.
 *
 * Returns false only when the backend rejects the token (session is gone).
 * Network or unexpected errors return true so a transient problem never
 * logs the user out.
 */
export async function refreshAccessToken(apiUrl: string): Promise<boolean> {
  const token = sessionStorage.getItem(accessTokenKey)?.trim();
  if (!token) return false;
  try {
    const response = await fetch(`${apiUrl}/auth/refresh`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
    if (response.status === 401 || response.status === 403) return false;
    if (!response.ok) return true;
    const data = (await response.json()) as { access_token?: unknown };
    const refreshed = typeof data.access_token === 'string' ? data.access_token.trim() : '';
    if (refreshed) sessionStorage.setItem(accessTokenKey, refreshed);
    return true;
  } catch {
    return true;
  }
}

/** Refresh proactively when the stored token is close to expiring. */
export async function ensureFreshToken(apiUrl: string): Promise<void> {
  const token = sessionStorage.getItem(accessTokenKey)?.trim();
  if (!token || !tokenNeedsRefresh(token)) return;
  await refreshAccessToken(apiUrl);
}

export async function checkSession(apiUrl: string): Promise<SessionCheck> {
  await ensureFreshToken(apiUrl);
  const token = sessionStorage.getItem(accessTokenKey)?.trim();
  const conversationId = sessionStorage.getItem(conversationIdKey)?.trim();
  if (!token || !conversationId) return 'invalid';

  try {
    const headers = { Authorization: `Bearer ${token}` };
    const [me, conversation] = await Promise.all([
      fetch(`${apiUrl}/auth/me`, { headers, cache: 'no-store' }),
      fetch(`${apiUrl}/conversations/${encodeURIComponent(conversationId)}`, { headers, cache: 'no-store' }),
    ]);
    if (me.ok && conversation.ok) return 'valid';
    if ([me, conversation].some((response) => response.status === 401 || response.status === 403)) return 'invalid';
    return 'unavailable';
  } catch {
    return 'unavailable';
  }
}

export function redirectToLogin(): void {
  clearSession();
  if (window.location.pathname !== '/') window.location.replace('/');
}

export function installSessionGuard(apiUrl: string, intervalMs = 15000): () => void {
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
  return () => {
    disposed = true;
    window.clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisibilityChange);
  };
}
