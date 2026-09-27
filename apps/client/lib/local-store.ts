/**
 * Minimal per-key string store backed by localStorage with an in-memory
 * fallback.
 */

type Listener = () => void;

const listeners = new Map<string, Set<Listener>>();
const memory = new Map<string, string>();

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function notify(key: string) {
  listeners.get(key)?.forEach((listener) => listener());
}

export function readLocalValue(key: string): string | null {
  const store = storage();
  if (store) {
    try {
      const value = store.getItem(key);
      if (value !== null) return value;
    } catch {
      // fall through to memory
    }
  }
  return memory.get(key) ?? null;
}

export function writeLocalValue(key: string, value: string | null) {
  if (value === null || value === '') {
    memory.delete(key);
  } else {
    memory.set(key, value);
  }
  const store = storage();
  if (store) {
    try {
      if (value === null || value === '') store.removeItem(key);
      else store.setItem(key, value);
    } catch {
      // Best-effort persistence; the in-memory copy still serves this session.
    }
  }
  notify(key);
}

export function subscribeLocalValue(key: string, listener: Listener) {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(listener);

  const onStorage = (event: StorageEvent) => {
    if (event.key === key || event.key === null) listener();
  };
  globalThis.addEventListener?.('storage', onStorage);

  return () => {
    set?.delete(listener);
    globalThis.removeEventListener?.('storage', onStorage);
  };
}
