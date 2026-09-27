import { useEffect } from 'react';
import { AppState } from 'react-native';

import { useSession } from '../../contexts/SessionContext';
import {
  getNetworkMode,
  getOutboxQueue,
  syncOutboxQueue,
} from '../../services/offlineStorage';

/** How often a waiting report is tried again while the app is open. */
const RETRY_EVERY_MS = 60_000;

/**
 * Sends what is waiting in the outbox without anyone having to press a button:
 * when the app opens, when it comes back to the front, and once a minute while
 * it stays open.
 */
export function OutboxRunner() {
  const { session } = useSession();
  const signedIn = session?.mode === 'authenticated';

  useEffect(() => {
    if (!signedIn) return;
    let running = false;

    const pass = async () => {
      if (running) return;
      running = true;
      try {
        if ((await getNetworkMode()) === 'dead_zone') return;
        if ((await getOutboxQueue()).length === 0) return;
        await syncOutboxQueue();
      } catch {
        // Every failure is already recorded on the report it concerns.
      } finally {
        running = false;
      }
    };

    void pass();
    const timer = setInterval(() => void pass(), RETRY_EVERY_MS);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void pass();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [signedIn]);

  return null;
}
