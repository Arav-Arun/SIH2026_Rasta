'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

import { useAuth } from '@/components/auth/auth-provider';
import { outboxCounts } from '@/lib/offline/outbox';
import {
  applyUpdate,
  decideUpdate,
  registerServiceWorker,
  serviceWorkerSupported,
} from '@/lib/pwa/service-worker';

/** Registers the worker and decides when it is safe to offer a reload. */

type UpdateContextValue =
  | { kind: 'unsupported' }
  | { kind: 'idle' }
  | { kind: 'update_waiting'; apply: () => void }
  | { kind: 'update_held'; pending: number };

const UpdateContext = createContext<UpdateContextValue>({ kind: 'idle' });

/** How often to re-check whether the outbox has drained. */
const PENDING_POLL_MS = 15_000;

export function ServiceWorkerProvider({ children }: { children: ReactNode }) {
  const { workspace } = useAuth();
  const profileId = workspace?.identity.profileId ?? null;

  const registration = useRef<ServiceWorkerRegistration | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [pending, setPending] = useState<number | null>(0);
  const supported = serviceWorkerSupported();

  useEffect(() => {
    if (!supported) return;
    let cancelled = false;

    void (async () => {
      const result = await registerServiceWorker();
      if (cancelled || !result) return;
      registration.current = result;
      if (result.waiting) setWaiting(true);

      result.addEventListener('updatefound', () => {
        const installing = result.installing;
        installing?.addEventListener('statechange', () => {
          // `installed` with a controller already present means this is an
          // update rather than a first install.
          if (
            installing.state === 'installed' &&
            navigator.serviceWorker.controller
          ) {
            setWaiting(true);
          }
        });
      });
    })();

    // Reload once the new worker takes over, and only then: doing it when the
    // update arrives would be the interruption this whole provider avoids.
    const onControllerChange = () => window.location.reload();
    navigator.serviceWorker.addEventListener(
      'controllerchange',
      onControllerChange,
    );

    return () => {
      cancelled = true;
      navigator.serviceWorker.removeEventListener(
        'controllerchange',
        onControllerChange,
      );
    };
  }, [supported]);

  useEffect(() => {
    if (!waiting) return;
    let cancelled = false;

    const check = async () => {
      try {
        const counts = await outboxCounts(profileId);
        if (!cancelled) {
          setPending(
            counts.queued +
              counts.uploading +
              counts.conflict +
              counts.needsAction,
          );
        }
      } catch {
        // Unable to read the outbox: assume there is something in it. Holding a
        // prompt is recoverable; discarding somebody's report is not.
        if (!cancelled) setPending(null);
      }
    };
    void check();
    const timer = window.setInterval(() => void check(), PENDING_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [waiting, profileId]);

  const apply = useCallback(() => {
    if (registration.current) applyUpdate(registration.current);
  }, []);

  const value = useMemo<UpdateContextValue>(() => {
    const state = decideUpdate(supported, waiting, pending);
    return state.kind === 'update_waiting'
      ? { kind: 'update_waiting', apply }
      : state;
  }, [supported, waiting, pending, apply]);

  return (
    <UpdateContext.Provider value={value}>{children}</UpdateContext.Provider>
  );
}

export function useServiceWorkerUpdate(): UpdateContextValue {
  return useContext(UpdateContext);
}
