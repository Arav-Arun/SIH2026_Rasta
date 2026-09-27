import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { CrewSession } from '../types';
import { loadStoredSession, signOut as endSession } from '../services/session';

interface SessionContextValue {
  /** False until the stored session has been read from disk. */
  ready: boolean;
  session: CrewSession | null;
  adoptSession: (session: CrewSession) => void;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<CrewSession | null>(null);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const stored = await loadStoredSession();
      if (cancelled) return;
      setSession(stored);
      setReady(true);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const adoptSession = useCallback((next: CrewSession) => {
    setSession(next);
  }, []);

  const signOutCallback = useCallback(async () => {
    const mode = session?.mode ?? 'local_only';
    await endSession(mode);
    setSession(null);
  }, [session]);

  const value = useMemo<SessionContextValue>(
    () => ({ ready, session, adoptSession, signOut: signOutCallback }),
    [ready, session, adoptSession, signOutCallback],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error('useSession must be used inside a SessionProvider');
  }
  return ctx;
}

/** Throws when called outside a signed-in screen, use inside the (tabs) group. */
export function useCrew(): CrewSession {
  const { session } = useSession();
  if (!session) {
    throw new Error('useCrew called without an active session');
  }
  return session;
}
