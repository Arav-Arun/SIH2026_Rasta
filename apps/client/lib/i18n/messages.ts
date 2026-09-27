import en from '@/i18n/en.json';
import { CATALOGUE_LOADERS } from '@/i18n/index';

import { LANGUAGES, languageInfo } from './languages';

/** A language code from the registry (`i18n/languages.json`). */
export type Locale = string;
export const DEFAULT_LOCALE: Locale = 'en';

/**
 * The languages this build can actually show, in registry order: English, plus
 * every language whose catalogue exists.
 */
export const SUPPORTED_LOCALES: readonly Locale[] = LANGUAGES.filter(
  (language) =>
    language.code === DEFAULT_LOCALE || language.code in CATALOGUE_LOADERS,
).map((language) => language.code);

/** BCP-47 tag for Intl formatting. */
export function intlLocale(locale: Locale): string {
  return `${locale}-IN-u-nu-latn`;
}

/** All times in the product are displayed in India Standard Time. */
export const DISPLAY_TIME_ZONE = 'Asia/Kolkata';

export type CatalogueMeta = {
  locale: Locale;
  label: string;
  nativeLabel: string;
  reviewed: boolean;
  reviewedBy: string | null;
  notes: string;
  /** Set by the generator from the script the translation came back in. */
  dir?: 'ltr' | 'rtl';
  script?: string;
  /** Present when some or all strings were machine-drafted. */
  machine?: {
    provider: string;
    model: string;
    strings: number;
    generatedAt: string;
  };
};

type RawCatalogue = { _meta: CatalogueMeta } & Record<string, unknown>;

type FlatCatalogue = Record<string, string>;

/** Flatten a nested JSON catalogue into dot-separated keys. */
export function flattenCatalogue(
  value: unknown,
  prefix = '',
  out: FlatCatalogue = {},
): FlatCatalogue {
  if (typeof value === 'string') {
    if (prefix) out[prefix] = value;
    return out;
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (key === '_meta') continue;
      flattenCatalogue(child, prefix ? `${prefix}.${key}` : key, out);
    }
  }
  return out;
}

/* Loaded catalogues. */
const FLAT = new Map<Locale, FlatCatalogue>([
  [DEFAULT_LOCALE, flattenCatalogue(en)],
]);
const META = new Map<Locale, CatalogueMeta>([
  [DEFAULT_LOCALE, (en as unknown as RawCatalogue)._meta],
]);
const PENDING = new Map<Locale, Promise<boolean>>();
const LISTENERS = new Set<() => void>();

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && SUPPORTED_LOCALES.includes(value);
}

export function isCatalogueLoaded(locale: Locale): boolean {
  return FLAT.has(locale);
}

export function catalogueMeta(locale: Locale): CatalogueMeta | null {
  return META.get(locale) ?? null;
}

export function subscribeCatalogues(listener: () => void): () => void {
  LISTENERS.add(listener);
  return () => LISTENERS.delete(listener);
}

/** Loads a language's catalogue once; resolves false if there is none. */
export function loadCatalogue(locale: Locale): Promise<boolean> {
  if (FLAT.has(locale)) return Promise.resolve(true);
  const loader = CATALOGUE_LOADERS[locale];
  if (!loader) return Promise.resolve(false);
  const inFlight = PENDING.get(locale);
  if (inFlight) return inFlight;

  const loading = loader()
    .then((module) => {
      const raw = module.default as RawCatalogue;
      FLAT.set(locale, flattenCatalogue(raw));
      META.set(locale, raw._meta);
      for (const listener of LISTENERS) listener();
      return true;
    })
    .catch(() => false)
    .finally(() => PENDING.delete(locale));
  PENDING.set(locale, loading);
  return loading;
}

/** Text direction: the generated catalogue's own record first, then the registry. */
export function directionOf(locale: Locale): 'ltr' | 'rtl' {
  return META.get(locale)?.dir ?? languageInfo(locale)?.dir ?? 'ltr';
}

export type MessageValues = Record<string, string | number>;

/**
 * Split a comma-separated ICU argument list at the top level only, so plural
 * branches containing commas or nested braces stay intact.
 */
function splitTopLevel(input: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of input) {
    if (ch === '{') depth += 1;
    if (ch === '}') depth -= 1;
    if (ch === ',' && depth === 0) {
      parts.push(current);
      current = '';
      continue;
    }
    current += ch;
  }
  parts.push(current);
  return parts;
}

/** Parse `=0 {..} one {..} other {..}` into a branch map. */
function parsePluralBranches(input: string): Record<string, string> {
  const branches: Record<string, string> = {};
  let i = 0;
  while (i < input.length) {
    while (i < input.length && /\s/.test(input[i])) i += 1;
    let selector = '';
    while (i < input.length && input[i] !== '{' && !/\s/.test(input[i])) {
      selector += input[i];
      i += 1;
    }
    while (i < input.length && /\s/.test(input[i])) i += 1;
    if (input[i] !== '{') break;
    let depth = 0;
    let body = '';
    for (; i < input.length; i += 1) {
      const ch = input[i];
      if (ch === '{') {
        depth += 1;
        if (depth === 1) continue;
      }
      if (ch === '}') {
        depth -= 1;
        if (depth === 0) {
          i += 1;
          break;
        }
      }
      body += ch;
    }
    if (selector) branches[selector] = body;
  }
  return branches;
}

function pluralCategory(locale: Locale, count: number): string {
  try {
    return new Intl.PluralRules(intlLocale(locale)).select(count);
  } catch {
    return count === 1 ? 'one' : 'other';
  }
}

/**
 * Format a message with `{name}` placeholders and ICU plural arguments.
 * Missing values are left visible as `{name}` so a gap is never silent.
 */
export function formatMessage(
  locale: Locale,
  template: string,
  values: MessageValues = {},
): string {
  let out = '';
  let i = 0;
  while (i < template.length) {
    const open = template.indexOf('{', i);
    if (open === -1) {
      out += template.slice(i);
      break;
    }
    out += template.slice(i, open);
    // find the matching close brace
    let depth = 0;
    let close = open;
    for (; close < template.length; close += 1) {
      if (template[close] === '{') depth += 1;
      if (template[close] === '}') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    if (close >= template.length) {
      out += template.slice(open);
      break;
    }
    const inner = template.slice(open + 1, close);
    const [nameRaw, typeRaw, restRaw] = splitTopLevel(inner);
    const name = (nameRaw ?? '').trim();
    const type = (typeRaw ?? '').trim();
    const value = values[name];

    if (type === 'plural') {
      const count = typeof value === 'number' ? value : Number(value);
      const branches = parsePluralBranches(restRaw ?? '');
      const exact = Number.isFinite(count) ? branches[`=${count}`] : undefined;
      const chosen =
        exact ??
        (Number.isFinite(count)
          ? branches[pluralCategory(locale, count)]
          : undefined) ??
        branches.other ??
        '';
      const rendered = chosen.replaceAll(
        '#',
        Number.isFinite(count)
          ? formatNumber(locale, count)
          : String(value ?? ''),
      );
      out += formatMessage(locale, rendered, values);
    } else if (value === undefined || value === null) {
      out += `{${name}}`;
    } else {
      out += typeof value === 'number' ? formatNumber(locale, value) : value;
    }
    i = close + 1;
  }
  return out;
}

/**
 * Numbers are written the same way in every language: Latin digits with Indian
 * grouping (12,34,567).
 */
const NUMBER_LOCALE = 'en-IN-u-nu-latn';

export function formatNumber(
  _locale: Locale,
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  try {
    return new Intl.NumberFormat(NUMBER_LOCALE, options).format(value);
  } catch {
    return String(value);
  }
}

type MissingKeyReporter = (key: string, locale: Locale) => void;

let missingKeyReporter: MissingKeyReporter | null = null;
export function setMissingKeyReporter(reporter: MissingKeyReporter | null) {
  missingKeyReporter = reporter;
}

/**
 * Resolve a key in the requested locale, falling back to English. A key that
 * is missing everywhere renders as the key itself so the gap is visible.
 */
export function lookupMessage(locale: Locale, key: string): string {
  const local = FLAT.get(locale)?.[key];
  if (local !== undefined) return local;
  if (locale !== DEFAULT_LOCALE) missingKeyReporter?.(key, locale);
  const fallback = FLAT.get(DEFAULT_LOCALE)?.[key];
  if (fallback !== undefined) return fallback;
  missingKeyReporter?.(key, DEFAULT_LOCALE);
  return key;
}

export function translate(
  locale: Locale,
  key: string,
  values?: MessageValues,
): string {
  return formatMessage(locale, lookupMessage(locale, key), values);
}

/** The English source catalogue, flattened. */
export function englishCatalogue(): FlatCatalogue {
  return FLAT.get(DEFAULT_LOCALE) as FlatCatalogue;
}

/** A loaded catalogue, flattened; null until `loadCatalogue` has resolved. */
export function loadedCatalogue(locale: Locale): FlatCatalogue | null {
  return FLAT.get(locale) ?? null;
}

/** Keys present in English but missing in a loaded locale. */
export function missingKeys(locale: Locale): string[] {
  const local = FLAT.get(locale) ?? {};
  return Object.keys(englishCatalogue()).filter(
    (key) => local[key] === undefined,
  );
}

/** Keys present in a loaded locale but absent from English (orphans). */
export function orphanKeys(locale: Locale): string[] {
  const english = englishCatalogue();
  return Object.keys(FLAT.get(locale) ?? {}).filter(
    (key) => english[key] === undefined,
  );
}
