/**
 * The app's languages, from the web client's catalogues (apps/client/i18n).
 *
 * English is the source and the fallback. The others ship are the languages of
 * the North-East that have a catalogue, plus Hindi: each is a machine draft
 * until a native speaker reviews it, which the picker says. A key a catalogue
 * lacks shows in English; a key missing everywhere shows as the key itself, so
 * a gap is visible rather than blank.
 *
 * The formatter is the web client's, ported: `{name}` placeholders and ICU
 * plurals (`{count, plural, one {# road} other {# roads}}`).
 */

import en from '../../client/i18n/en.json';
import as from '../../client/i18n/as.json';
import bn from '../../client/i18n/bn.json';
import brx from '../../client/i18n/brx.json';
import hi from '../../client/i18n/hi.json';
import mni from '../../client/i18n/mni.json';
import ne from '../../client/i18n/ne.json';

export type Locale = 'en' | 'as' | 'bn' | 'brx' | 'hi' | 'mni' | 'ne';
export type MessageValues = Record<string, string | number>;

export type CatalogueMeta = {
  locale: string;
  label: string;
  nativeLabel: string;
  reviewed: boolean;
};

type Catalogue = { _meta: CatalogueMeta } & Record<string, unknown>;

const SOURCES: Record<Locale, unknown> = { en, as, bn, brx, hi, mni, ne };

/** In the order the picker shows them: English, then by English name. */
export const LOCALES: readonly Locale[] = [
  'en',
  'as',
  'bn',
  'brx',
  'hi',
  'mni',
  'ne',
];

export const DEFAULT_LOCALE: Locale = 'en';

type Flat = Record<string, string>;

function flatten(value: unknown, prefix = '', out: Flat = {}): Flat {
  if (typeof value === 'string') {
    if (prefix) out[prefix] = value;
    return out;
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      if (key === '_meta') continue;
      flatten(child, prefix ? `${prefix}.${key}` : key, out);
    }
  }
  return out;
}

const FLAT = new Map<Locale, Flat>();

function flat(locale: Locale): Flat {
  let catalogue = FLAT.get(locale);
  if (!catalogue) {
    catalogue = flatten(SOURCES[locale]);
    FLAT.set(locale, catalogue);
  }
  return catalogue;
}

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as string[]).includes(value);
}

/** Whether this language's catalogue has any of the app's own text yet. */
export function hasAppStrings(locale: Locale): boolean {
  return Object.keys(flat(locale)).some((key) => key.startsWith('mobile.'));
}

export function catalogueMeta(locale: Locale): CatalogueMeta {
  return (SOURCES[locale] as Catalogue)._meta;
}

/** A device language this app has, from a tag such as "hi-IN"; else English. */
export function localeFromDevice(tag: string | undefined | null): Locale {
  const base = (tag ?? '').toLowerCase().split(/[-_]/)[0];
  return isLocale(base) ? base : DEFAULT_LOCALE;
}

/** Numbers in Latin digits with Indian grouping, as on the web. */
export function formatNumber(value: number): string {
  try {
    return new Intl.NumberFormat('en-IN-u-nu-latn').format(value);
  } catch {
    return String(value);
  }
}

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

function pluralBranches(input: string): Record<string, string> {
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
    return new Intl.PluralRules(locale).select(count);
  } catch {
    return count === 1 ? 'one' : 'other';
  }
}

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
    const [nameRaw, typeRaw, restRaw] = splitTopLevel(
      template.slice(open + 1, close),
    );
    const name = (nameRaw ?? '').trim();
    const value = values[name];
    if ((typeRaw ?? '').trim() === 'plural') {
      const count = typeof value === 'number' ? value : Number(value);
      const branches = pluralBranches(restRaw ?? '');
      const chosen =
        (Number.isFinite(count) ? branches[`=${count}`] : undefined) ??
        (Number.isFinite(count)
          ? branches[pluralCategory(locale, count)]
          : undefined) ??
        branches.other ??
        '';
      out += formatMessage(
        locale,
        chosen
          .split('#')
          .join(
            Number.isFinite(count) ? formatNumber(count) : String(value ?? ''),
          ),
        values,
      );
    } else if (value === undefined || value === null) {
      out += `{${name}}`;
    } else {
      out += typeof value === 'number' ? formatNumber(value) : value;
    }
    i = close + 1;
  }
  return out;
}

/**
 * What a service hands a screen to say: a catalogue key, a key with values, or
 * text from elsewhere (a server's own message), which is shown as it is.
 */
export type Message = string | { key: string; values?: MessageValues };

export function message(key: string, values?: MessageValues): Message {
  return values ? { key, values } : key;
}

function isKey(value: string): boolean {
  return flat(DEFAULT_LOCALE)[value] !== undefined;
}

export function translate(
  locale: Locale,
  key: Message,
  values?: MessageValues,
): string {
  if (typeof key !== 'string') {
    return translate(locale, key.key, { ...key.values, ...values });
  }
  // A value may itself be a key, such as the reason inside "Not sent yet: ...".
  const resolved: MessageValues = {};
  for (const [name, value] of Object.entries(values ?? {})) {
    resolved[name] =
      typeof value === 'string' && isKey(value)
        ? translate(locale, value)
        : value;
  }
  const template = flat(locale)[key] ?? flat(DEFAULT_LOCALE)[key] ?? key;
  return formatMessage(locale, template, resolved);
}
