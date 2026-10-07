import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { beforeAll, describe, expect, it, vi } from 'vitest';

import { CATALOGUE_LOADERS } from '@/i18n/index';

import { LANGUAGES, languageInfo } from './languages';
import {
  SUPPORTED_LOCALES,
  catalogueMeta,
  directionOf,
  englishCatalogue,
  flattenCatalogue,
  formatMessage,
  formatNumber,
  isLocale,
  loadCatalogue,
  loadedCatalogue,
  lookupMessage,
  missingKeys,
  orphanKeys,
  setMissingKeyReporter,
  translate,
} from './messages';

const I18N_DIR = join(process.cwd(), 'i18n');

/** Every catalogue file on disk other than English and the registry. */
function catalogueFiles(): string[] {
  return readdirSync(I18N_DIR)
    .filter(
      (name) =>
        name.endsWith('.json') &&
        name !== 'languages.json' &&
        name !== 'en.json',
    )
    .map((name) => name.replace(/\.json$/, ''))
    .sort();
}

/**
 * Strings added after the last translation run. They show in English in every
 * other language until the catalogues are next regenerated. Narrow this pattern
 * as they are translated; nothing outside it may be missing.
 */
const PENDING_TRANSLATION =
  /^(newConsignment\.|map\.risk(Why|Caveats|Model|NotScored|Shadow)|status\.risk\.(critical|unknown)$|alert\.|alerts\.evidence\.(reporter|position|positionNoAccuracy|noPosition|pressedAt|note|openMap|notDispatch|slowTraffic|notClosed)$|alerts\.inspect$|fieldHome\.sos\.|outbox\.type\.sos\.raise$|health\.(outcomes|schedule)\.|health\.suggested$|health\.trainedModel\.|supplyGaps\.|admin\.|mobile\.)/;

/**
 * The argument names a message uses. A plural branch is text, not an argument,
 * so `one {has}` is dropped first: its word is translated like any other.
 */
const placeholders = (s: string) =>
  [
    ...s
      .replace(/(?:=\d+|zero|one|two|few|many|other)\s*\{[^{}]*\}/g, '')
      .matchAll(/\{\s*([a-zA-Z_]+)\s*(?:,|\})/g),
  ]
    .map((m) => m[1])
    .sort();

beforeAll(async () => {
  for (const code of catalogueFiles()) await loadCatalogue(code);
});

describe('formatMessage', () => {
  it('interpolates named values and formats numbers', () => {
    expect(
      formatMessage('en', 'Hello {name}, {count} items', {
        name: 'A',
        count: 1200,
      }),
    ).toBe('Hello A, 1,200 items');
  });

  it('leaves a missing value visible instead of dropping it', () => {
    expect(formatMessage('en', 'Request ID {id}')).toBe('Request ID {id}');
  });

  it('selects exact, one and other plural branches with # substitution', () => {
    const template =
      '{count, plural, =0 {Nothing queued} one {# item waiting} other {# items waiting}}';
    expect(formatMessage('en', template, { count: 0 })).toBe('Nothing queued');
    expect(formatMessage('en', template, { count: 1 })).toBe('1 item waiting');
    expect(formatMessage('en', template, { count: 12 })).toBe(
      '12 items waiting',
    );
  });

  it('handles nested placeholders inside plural branches', () => {
    const template =
      '{count, plural, one {# report from {who}} other {# reports from {who}}}';
    expect(formatMessage('en', template, { count: 2, who: 'Ravi' })).toBe(
      '2 reports from Ravi',
    );
  });

  it('never renders non-Latin digits, in any registered language', () => {
    for (const language of LANGUAGES) {
      expect(formatNumber(language.code, 1234567), language.code).toMatch(
        /^[0-9,.]+$/,
      );
    }
  });
});

describe('language registry', () => {
  it('lists English and each supported language once', () => {
    const codes = LANGUAGES.map((language) => language.code);
    expect(codes[0]).toBe('en');
    expect(new Set(codes).size).toBe(codes.length);
    // English plus the 22 languages of the Eighth Schedule.
    expect(codes).toHaveLength(23);
    expect(codes).toEqual(
      expect.arrayContaining([
        'en',
        'as',
        'bn',
        'brx',
        'doi',
        'gu',
        'hi',
        'mni',
        'ne',
        'ur',
      ]),
    );
    for (const language of LANGUAGES) {
      expect(language.native.trim(), language.code).not.toBe('');
      expect(language.sarvam, language.code).toMatch(/^[a-z]{2,3}-IN$/);
    }
  });

  it('offers exactly English plus the languages that have a catalogue', () => {
    expect(SUPPORTED_LOCALES).toEqual([
      'en',
      ...LANGUAGES.map((language) => language.code).filter((code) =>
        catalogueFiles().includes(code),
      ),
    ]);
    expect(isLocale('en')).toBe(true);
    expect(isLocale('xx')).toBe(false);
  });

  it('every catalogue file is loadable, and only catalogue files are listed', () => {
    expect(Object.keys(CATALOGUE_LOADERS).sort()).toEqual(catalogueFiles());
    for (const code of catalogueFiles()) {
      expect(
        languageInfo(code),
        `${code} is not in the registry`,
      ).toBeDefined();
    }
  });
});

describe('catalogues', () => {
  it('flattens nested keys and skips _meta', () => {
    expect(flattenCatalogue({ _meta: { x: 1 }, a: { b: 'c' } })).toEqual({
      'a.b': 'c',
    });
  });

  it('English is complete: no empty strings', () => {
    const empty = Object.entries(englishCatalogue()).filter(
      ([, v]) => !v.trim(),
    );
    expect(empty).toEqual([]);
  });

  it('every catalogue covers every English key but the pending ones, and adds none of its own', () => {
    for (const code of catalogueFiles()) {
      const unexpected = missingKeys(code).filter(
        (key) => !PENDING_TRANSLATION.test(key),
      );
      expect(unexpected, `${code} missing`).toEqual([]);
      expect(orphanKeys(code), `${code} orphan`).toEqual([]);
    }
  });

  it('only English claims a review; every other catalogue says it has none', () => {
    expect(catalogueMeta('en')?.reviewed).toBe(true);
    for (const code of catalogueFiles()) {
      const meta = catalogueMeta(code);
      expect(meta?.locale, code).toBe(code);
      // A catalogue may only claim review when it names who reviewed it.
      if (meta?.reviewed) expect(meta.reviewedBy, code).toBeTruthy();
    }
  });

  it('every placeholder used in English is also present in every translation', () => {
    const english = englishCatalogue();
    for (const code of catalogueFiles()) {
      const local = loadedCatalogue(code) ?? {};
      for (const [key, source] of Object.entries(english)) {
        // A key that has not been translated yet falls back to English as it is.
        if (local[key] === undefined) continue;
        expect(placeholders(local[key]), `${code}:${key}`).toEqual(
          placeholders(source),
        );
      }
    }
  });

  it('a generated catalogue records its direction, and right-to-left scripts say so', () => {
    for (const code of catalogueFiles()) {
      const raw = JSON.parse(
        readFileSync(join(I18N_DIR, `${code}.json`), 'utf8'),
      ) as {
        _meta: { dir?: string };
      };
      const expected = raw._meta.dir ?? languageInfo(code)?.dir;
      expect(directionOf(code), code).toBe(expected);
    }
    expect(directionOf('en')).toBe('ltr');
  });
});

describe('lookup and fallback', () => {
  it('falls back to English and reports the gap', () => {
    const reported: string[] = [];
    setMissingKeyReporter((key, locale) => reported.push(`${locale}:${key}`));
    expect(lookupMessage('hi', 'does.not.exist')).toBe('does.not.exist');
    expect(reported).toEqual(['hi:does.not.exist', 'en:does.not.exist']);
    setMissingKeyReporter(null);
  });

  it('shows English for a language whose catalogue has not loaded', async () => {
    // A fresh copy of the module has loaded nothing yet.
    vi.resetModules();
    const fresh = await import('./messages');
    expect(fresh.loadedCatalogue('ur')).toBeNull();
    expect(fresh.translate('ur', 'status.passability.closed')).toBe('Closed');
  });

  it('translates with values in the requested locale', () => {
    expect(translate('en', 'offline.queued', { count: 3 })).toBe(
      '3 items waiting to send',
    );
    expect(translate('hi', 'status.passability.closed')).toBe('बंद');
    expect(translate('as', 'status.passability.open')).toBe('খোলা');
  });
});
