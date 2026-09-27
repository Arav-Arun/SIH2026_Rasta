import * as Crypto from 'expo-crypto';

/**
 * A random RFC 4122 UUID, used for record ids and idempotency keys.
 * React Native has no global `crypto`, so this goes through expo-crypto.
 */
export function newUuid(): string {
  return Crypto.randomUUID();
}
