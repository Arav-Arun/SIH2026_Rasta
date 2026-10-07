import AsyncStorage from '@react-native-async-storage/async-storage';
import { CrewRole, CrewSession, SessionMode, VehicleType } from '../types';
import { clearDriverData, loadQueue } from './driverStorage';
import { clearReports, countUnsent } from './offlineStorage';
import { getMe } from './rastaApi';
import { clearDraft } from './reportDraft';
import { SUPABASE_CONFIGURED, supabase } from './supabase';
import { restoreTracker, stopTracking } from './tracker';

const STORAGE_KEY_SESSION = '@rasta_crew_session_v1';

interface SignInRequest {
  email: string;
  password: string;
  crewCode: string;
  vehicleType: VehicleType;
}

interface LocalSessionRequest {
  role: CrewRole;
  displayName: string;
  crewCode: string;
  vehicleType: VehicleType;
}

type SignInResult =
  | { ok: true; session: CrewSession }
  | { ok: false; reason: string };

/** Normalises a crew code so a driver and observer typing it differently still pair. */
function normaliseCrewCode(raw: string): string {
  return raw.trim().toUpperCase().replace(/\s+/g, '-');
}

/** The seat an account's server-side role grants on this app, if any. */
function seatFor(roles: string[]): CrewRole | null {
  if (roles.includes('driver')) return 'driver';
  if (roles.includes('field_officer')) return 'observer';
  return null;
}

export async function loadStoredSession(): Promise<CrewSession | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY_SESSION);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CrewSession;
    if (!parsed?.role || !parsed?.crewCode) return null;
    return parsed;
  } catch {
    return null;
  }
}

async function persistSession(session: CrewSession): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY_SESSION, JSON.stringify(session));
}

/** Signs in against the real Supabase project. */
export async function signInWithCredentials(
  request: SignInRequest,
): Promise<SignInResult> {
  const email = request.email.trim();
  const crewCode = normaliseCrewCode(request.crewCode);

  if (!email || !request.password) {
    return { ok: false, reason: 'mobile.session.enterCredentials' };
  }
  if (!SUPABASE_CONFIGURED) {
    return {
      ok: false,
      reason: 'mobile.session.notConfigured',
    };
  }
  if (!crewCode) {
    return { ok: false, reason: 'mobile.session.enterCrewCode' };
  }

  try {
    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password: request.password,
    });

    if (error) {
      return { ok: false, reason: error.message };
    }
    if (!data.user) {
      return { ok: false, reason: 'mobile.session.noUser' };
    }

    // The seat is the account's, as the server grants it, never a choice made
    // on this phone: the API refuses anything the account's role does not allow.
    const me = await getMe();
    const role = me.ok
      ? seatFor(me.data.roles.map((grant) => grant.role))
      : null;
    if (!me.ok || !role) {
      await supabase.auth.signOut().catch(() => undefined);
      return {
        ok: false,
        reason: me.ok ? 'mobile.session.wrongAccount' : me.reason,
      };
    }

    const session: CrewSession = {
      role,
      mode: 'authenticated',
      displayName: me.data.profile.display_name || email.split('@')[0],
      userId: data.user.id,
      email,
      crewCode,
      vehicleType: request.vehicleType,
      signedInAt: new Date().toISOString(),
    };

    await persistSession(session);
    return { ok: true, session };
  } catch (err) {
    const message =
      err instanceof Error ? err.message : 'mobile.session.unreachable';
    return { ok: false, reason: message };
  }
}

/**
 * Starts a local-only session. Everything captured stays on the device and is
 * labelled as such throughout the app, it never reaches the control room.
 */
export async function startLocalSession(
  request: LocalSessionRequest,
): Promise<SignInResult> {
  const crewCode = normaliseCrewCode(request.crewCode);
  const displayName = request.displayName.trim();

  if (!displayName) {
    return { ok: false, reason: 'mobile.session.enterName' };
  }
  if (!crewCode) {
    return { ok: false, reason: 'mobile.session.enterCrewCode' };
  }

  const session: CrewSession = {
    role: request.role,
    mode: 'local_only',
    displayName,
    userId: null,
    email: null,
    crewCode,
    vehicleType: request.vehicleType,
    signedInAt: new Date().toISOString(),
  };

  await persistSession(session);
  return { ok: true, session };
}

/** What signing out now would delete from this phone before it was sent. */
export async function unsentOnThisPhone(): Promise<{
  reports: number;
  positions: number;
}> {
  const [reports, positions] = await Promise.all([countUnsent(), loadQueue()]);
  return { reports, positions: positions.length };
}

/** Ends the session and forgets the person on this phone. */
export async function signOut(mode: SessionMode): Promise<void> {
  await stopTracking();
  await Promise.all([clearDriverData(), clearReports(), clearDraft()]);
  await restoreTracker();
  if (mode === 'authenticated') {
    try {
      await supabase.auth.signOut();
    } catch {
      // Clearing the local session below is what actually ends the session here.
    }
  }
  await AsyncStorage.removeItem(STORAGE_KEY_SESSION);
}
