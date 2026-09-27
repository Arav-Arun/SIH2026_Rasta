import AsyncStorage from '@react-native-async-storage/async-storage';
import { createClient } from '@supabase/supabase-js';
import { AppState, Platform } from 'react-native';

/**
 * The Supabase client, used for exactly two things: signing in, and writing a
 * photo to the storage path the API issued for it.
 */

/** Where `supabase start` serves the local stack. Not a secret. */
const LOCAL_STACK_URL = 'http://127.0.0.1:54321';

const configuredUrl = process.env.EXPO_PUBLIC_SUPABASE_URL?.trim() ?? '';
const configuredKey =
  process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ?? '';

/** False when the build was made without a Supabase project; sign-in says so. */
export const SUPABASE_CONFIGURED = configuredUrl !== '' && configuredKey !== '';

const SUPABASE_URL = configuredUrl || LOCAL_STACK_URL;

/** An upper bound on every Supabase request, photo uploads included. */
const REQUEST_TIMEOUT_MS = 180_000;

function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  if (init?.signal) return fetch(input, init);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  return fetch(input, { ...init, signal: controller.signal }).finally(() =>
    clearTimeout(timer),
  );
}

export const supabase = createClient(
  SUPABASE_URL,
  // createClient refuses an empty key.
  configuredKey || 'not-configured',
  {
    global: { fetch: fetchWithTimeout },
    auth: {
      // React Native has no localStorage, and without a storage adapter the
      // client keeps the session in memory: after the app is closed the phone
      // still shows its person as signed in, but every request is refused as
      // unauthenticated and the outbox cannot send.
      storage: AsyncStorage,
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  },
);

// Timers do not run while the app is in the background, so the token is
// refreshed while the app is in front and refreshing stops when it leaves.
if (Platform.OS !== 'web') {
  AppState.addEventListener('change', (state) => {
    if (state === 'active') void supabase.auth.startAutoRefresh();
    else void supabase.auth.stopAutoRefresh();
  });
}
