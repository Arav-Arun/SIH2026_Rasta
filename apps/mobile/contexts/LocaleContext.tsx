import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  DEFAULT_LOCALE,
  isLocale,
  localeFromDevice,
  translate,
  type Locale,
  type Message,
  type MessageValues,
} from '../services/i18n';

/** The chosen language, kept on this device. */
const KEY = 'rasta.locale.v1';

type Translate = (key: Message, values?: MessageValues) => string;

type LocaleValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
};

const LocaleContext = createContext<LocaleValue>({
  locale: DEFAULT_LOCALE,
  setLocale: () => {},
  t: (key, values) => translate(DEFAULT_LOCALE, key, values),
});

function deviceTag(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().locale;
  } catch {
    return undefined;
  }
}

/** The phone's language if the app has it, until the person picks one. */
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() =>
    localeFromDevice(deviceTag()),
  );

  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(KEY)
      .then((stored) => {
        if (!cancelled && isLocale(stored)) setLocaleState(stored);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    AsyncStorage.setItem(KEY, next).catch(() => {});
  }, []);

  const value = useMemo<LocaleValue>(
    () => ({
      locale,
      setLocale,
      t: (key, values) => translate(locale, key, values),
    }),
    [locale, setLocale],
  );

  return (
    <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
  );
}

export function useLocale(): LocaleValue {
  return useContext(LocaleContext);
}

export function useT(): Translate {
  return useContext(LocaleContext).t;
}
