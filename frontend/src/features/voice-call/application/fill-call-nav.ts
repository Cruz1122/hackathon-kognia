import { stopLiveCalls, watchLiveCalls } from '../../calls/list';

export async function fillCallNav(apiUrl: string): Promise<void> {
  watchLiveCalls(apiUrl);
}

export function stopCallNav(): void {
  stopLiveCalls();
}
