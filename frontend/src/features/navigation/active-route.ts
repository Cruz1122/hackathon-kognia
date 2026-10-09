export type AppRoute = 'analytics' | 'calls' | 'dev' | 'specifications';

/** Map a browser path to the header item that should read as current. */
export function appRouteFromPath(path: string): AppRoute | null {
  const normalized = path.length > 1 && path.endsWith('/') ? path.slice(0, -1) : path;
  if (normalized === '/specifications') return 'specifications';
  if (normalized === '/dev' || normalized.startsWith('/dev/')) return 'dev';
  if (normalized === '/calls' || normalized.startsWith('/calls/')) return 'calls';
  if (normalized === '/analytics' || normalized.startsWith('/analytics/')) return 'analytics';
  return null;
}
