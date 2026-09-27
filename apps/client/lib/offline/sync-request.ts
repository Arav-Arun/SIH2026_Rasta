/** Asks the sync runner to send the outbox now. */
const SYNC_REQUESTED = 'rasta:sync-requested';

export function requestSync(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(SYNC_REQUESTED));
}

export function onSyncRequested(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(SYNC_REQUESTED, listener);
  return () => window.removeEventListener(SYNC_REQUESTED, listener);
}
