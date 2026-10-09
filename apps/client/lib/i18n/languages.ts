import registry from '@/i18n/languages.json';

/** A language the product offers. */
type LanguageInfo = {
  /** BCP-47 tag used for `lang`, `Intl` and the catalogue file name. */
  code: string;
  /** The Sarvam Translate code the catalogue was (or will be) drafted with. */
  sarvam: string;
  english: string;
  native: string;
  /** Default direction; a catalogue's `_meta.dir` overrides it once generated. */
  dir: 'ltr' | 'rtl';
};

export const LANGUAGES: readonly LanguageInfo[] =
  registry.languages as LanguageInfo[];

const BY_CODE = new Map(LANGUAGES.map((language) => [language.code, language]));

export function languageInfo(code: string): LanguageInfo | undefined {
  return BY_CODE.get(code);
}
