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
import type { Session } from '@supabase/supabase-js';

import { purgeProfileData } from '@/lib/offline/db';
import { clearLocalCaches } from '@/lib/pwa/service-worker';
import { disablePush } from '@/lib/pwa/push';
import { getSupabaseBrowserClient } from '@/lib/auth/supabase';
import { ApiClientError, getCurrentIdentity } from '@/lib/api/client';
import { toServerWorkspace, type ServerWorkspace } from '@/lib/auth/identity';
import {
  forgetVerifiedWorkspace,
  recallVerifiedWorkspace,
  rememberVerifiedWorkspace,
} from '@/lib/auth/offline-identity';

type AuthStatus =
  | 'loading'
  | 'unconfigured'
  | 'unauthenticated'
  | 'authenticated';

type IdentityBootstrapStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  /** The API is unreachable; the workspace is the server's last answer (offline-identity.ts). */
  | 'offline'
  | 'session_expired'
  | 'scope_denied'
  | 'unavailable'
  | 'error';

type AuthContextValue = {
  bootstrapStatus: IdentityBootstrapStatus;
  /** When the server last confirmed the workspace, while working offline. */
  offlineSince: string | null;
  refreshServerIdentity: () => Promise<void>;
  status: AuthStatus;
  session: Session | null;
  workspace: ServerWorkspace | null;
  signInWithPassword: (
    email: string,
    password: string,
  ) => Promise<{ ok: boolean }>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function failureStatus(error: unknown): IdentityBootstrapStatus {
  if (!(error instanceof ApiClientError)) return 'error';
  if (error.kind === 'session') return 'session_expired';
  if (error.kind === 'forbidden') return 'scope_denied';
  if (error.kind === 'network' || error.kind === 'unavailable') {
    return 'unavailable';
  }
  return 'error';
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [session, setSession] = useState<Session | null>(null);
  const [workspace, setWorkspace] = useState<ServerWorkspace | null>(null);
  const [bootstrapStatus, setBootstrapStatus] =
    useState<IdentityBootstrapStatus>('idle');
  const [offlineSince, setOfflineSince] = useState<string | null>(null);
  const bootstrapSequence = useRef(0);
  /** The user id `/v1/me` last validated, so a refresh can be told from a switch. */
  const validatedUserId = useRef<string | null>(null);

  const bootstrapForSession = useCallback(
    async (nextSession: Session | null) => {
      const sequence = ++bootstrapSequence.current;
      if (!nextSession) {
        validatedUserId.current = null;
        setWorkspace(null);
        setBootstrapStatus('idle');
        return;
      }

      // A browser-held Supabase session is not a role grant.
      const sameUser = validatedUserId.current === nextSession.user.id;
      if (!sameUser) {
        setWorkspace(null);
        setBootstrapStatus('loading');
      }

      try {
        const response = await getCurrentIdentity({
          accessToken: nextSession.access_token,
        });
        if (sequence !== bootstrapSequence.current) return;
        validatedUserId.current = nextSession.user.id;
        const verified = toServerWorkspace(response);
        rememberVerifiedWorkspace(nextSession.user.id, verified);
        setWorkspace(verified);
        setOfflineSince(null);
        setBootstrapStatus('ready');
      } catch (error) {
        if (sequence !== bootstrapSequence.current) return;
        const failure = failureStatus(error);
        // Unreachable is not refused: reuse the server's last answer for this
        // user, within its age limit, so a report can be written with no signal.
        const recalled =
          failure === 'unavailable'
            ? recallVerifiedWorkspace(nextSession.user.id)
            : null;
        if (recalled) {
          validatedUserId.current = nextSession.user.id;
          setWorkspace(recalled.workspace);
          setOfflineSince(recalled.verifiedAt);
          setBootstrapStatus('offline');
          return;
        }
        // A refusal ends offline use too: the next offline start must not reuse it.
        if (failure === 'session_expired' || failure === 'scope_denied') {
          forgetVerifiedWorkspace();
        }
        validatedUserId.current = null;
        setWorkspace(null);
        setOfflineSince(null);
        setBootstrapStatus(failure);
      }
    },
    [],
  );

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      queueMicrotask(() => setStatus('unconfigured'));
      return;
    }

    let active = true;
    void client.auth.getSession().then(({ data, error }) => {
      if (!active) return;
      const nextSession = error ? null : data.session;
      setSession(nextSession);
      setStatus(nextSession ? 'authenticated' : 'unauthenticated');
      void bootstrapForSession(nextSession);
    });

    const { data } = client.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setStatus(nextSession ? 'authenticated' : 'unauthenticated');
      void bootstrapForSession(nextSession);
    });

    return () => {
      active = false;
      bootstrapSequence.current += 1;
      data.subscription.unsubscribe();
    };
  }, [bootstrapForSession]);

  // Back online: ask the server again rather than keep the stored answer.
  useEffect(() => {
    if (bootstrapStatus !== 'offline') return;
    const onOnline = () => void bootstrapForSession(session);
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [bootstrapForSession, bootstrapStatus, session]);

  const signInWithPassword = useCallback(
    async (email: string, password: string) => {
      const client = getSupabaseBrowserClient();
      if (!client) return { ok: false };
      const { error } = await client.auth.signInWithPassword({
        email,
        password,
      });
      return { ok: !error };
    },
    [],
  );

  const signOut = useCallback(async () => {
    // Clear this person's local data before the session goes, while their
    // profile id is still known: a shared district phone must not hand the next
    // user the previous one's drafts or queued reports.
    const profileId = workspace?.identity.profileId ?? null;
    try {
      // A shared phone must not keep receiving the previous person's alerts.
      await disablePush(session?.access_token ?? null);
    } catch {
      // Unsubscribing locally is best effort; the server revokes a dead
      // registration the next time its push service reports it gone.
    }
    try {
      await purgeProfileData(profileId);
      // Downloaded packs and cached responses belong to this workspace too.
      await clearLocalCaches();
    } catch {
      // A storage failure must not trap someone in a signed-in session.
    }

    forgetVerifiedWorkspace();
    const client = getSupabaseBrowserClient();
    if (client) await client.auth.signOut({ scope: 'local' });
    setSession(null);
    setStatus(client ? 'unauthenticated' : 'unconfigured');
    await bootstrapForSession(null);
  }, [bootstrapForSession, session, workspace]);

  const refreshServerIdentity = useCallback(async () => {
    await bootstrapForSession(session);
  }, [bootstrapForSession, session]);

  const value = useMemo(
    () => ({
      bootstrapStatus,
      offlineSince,
      refreshServerIdentity,
      session,
      signInWithPassword,
      signOut,
      status,
      workspace,
    }),
    [
      bootstrapStatus,
      offlineSince,
      refreshServerIdentity,
      session,
      signInWithPassword,
      signOut,
      status,
      workspace,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
