'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

import { formatAbsoluteDate, formatAbsoluteTime } from '@/lib/i18n/format';
import {
  DEFAULT_LOCALE,
  catalogueMeta,
  directionOf,
  isCatalogueLoaded,
  isLocale,
  loadCatalogue,
  subscribeCatalogues,
  translate,
  type CatalogueMeta,
  type Locale,
  type MessageValues,
} from '@/lib/i18n/messages';
import {
  readLocalValue,
  subscribeLocalValue,
  writeLocalValue,
} from '@/lib/local-store';

export const LOCALE_STORAGE_KEY = 'rasta.locale';

type Translate = (key: string, values?: MessageValues) => string;

type LocaleContextValue = {
  /** The language on screen now. */
  locale: Locale;
  /** The language chosen; differs from `locale` only while its catalogue loads. */
  requestedLocale: Locale;
  dir: 'ltr' | 'rtl';
  meta: CatalogueMeta;
  setLocale: (locale: Locale) => Promise<void>;
  t: Translate;
};

const LocaleContext = createContext<LocaleContextValue | null>(null);

function subscribe(listener: () => void) {
  return subscribeLocalValue(LOCALE_STORAGE_KEY, listener);
}

function readLocale(fallback: Locale): Locale {
  const stored = readLocalValue(LOCALE_STORAGE_KEY);
  return isLocale(stored) ? stored : fallback;
}

/** Locale state for the whole application. */
export function LocaleProvider({
  children,
  initialLocale = DEFAULT_LOCALE,
}: {
  children: ReactNode;
  initialLocale?: Locale;
}) {
  const requestedLocale = useSyncExternalStore(
    subscribe,
    () => readLocale(initialLocale),
    () => initialLocale,
  );
  const ready = useSyncExternalStore(
    subscribeCatalogues,
    () => isCatalogueLoaded(requestedLocale),
    () => requestedLocale === DEFAULT_LOCALE,
  );

  useEffect(() => {
    if (!ready) void loadCatalogue(requestedLocale);
  }, [requestedLocale, ready]);

  const locale = ready ? requestedLocale : DEFAULT_LOCALE;
  const dir = directionOf(locale);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    document.documentElement.lang = locale;
    document.documentElement.dir = dir;
  }, [locale, dir]);

  const setLocale = useCallback(async (next: Locale) => {
    // Fetch first, then switch, so the page changes language in one step.
    await loadCatalogue(next);
    writeLocalValue(LOCALE_STORAGE_KEY, next);
  }, []);

  const t = useCallback<Translate>(
    (key, values) => translate(locale, key, values),
    [locale],
  );

  const value = useMemo(
    () => ({
      locale,
      requestedLocale,
      dir,
      meta:
        catalogueMeta(locale) ??
        (catalogueMeta(DEFAULT_LOCALE) as CatalogueMeta),
      setLocale,
      t,
    }),
    [locale, requestedLocale, dir, setLocale, t],
  );

  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const value = useContext(LocaleContext);
  if (!value) throw new Error('useLocale must be used inside LocaleProvider');
  return value;
}

export function useT(): Translate {
  return useLocale().t;
}

type TimeValue = string | number | Date;

/**
 * Absolute times and dates in the chosen language, in IST with its label, the
 * same everywhere.
 */
export function useFormatTime(): {
  time: (value: TimeValue) => string;
  date: (value: TimeValue) => string;
} {
  const { locale } = useLocale();
  return useMemo(
    () => ({
      time: (value: TimeValue) => formatAbsoluteTime(locale, new Date(value)),
      date: (value: TimeValue) => formatAbsoluteDate(locale, new Date(value)),
    }),
    [locale],
  );
}
