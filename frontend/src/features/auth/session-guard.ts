export const accessTokenKey = 'kognia.auth.access-token';
export const conversationIdKey = 'kognia.auth.conversation-id';

export type SessionCheck = 'valid' | 'invalid' | 'unavailable';

export function clearSession(): void {
  sessionStorage.removeItem(accessTokenKey);
  sessionStorage.removeItem(conversationIdKey);
}

export async function checkSession(apiUrl: string): Promise<SessionCheck> {
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

  const timer = window.setInterval(() => { void check(); }, intervalMs);
  void check();
  return () => {
    disposed = true;
    window.clearInterval(timer);
  };
}
