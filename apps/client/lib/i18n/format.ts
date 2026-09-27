import {
  DISPLAY_TIME_ZONE,
  intlLocale,
  formatNumber,
  translate,
  type Locale,
} from './messages';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Default age after which a value is treated as stale unless overridden. */
export const DEFAULT_STALE_AFTER_MS = 15 * MINUTE;

type Freshness = {
  ageMs: number | null;
  relative: string;
  absolute: string;
  stale: boolean;
};

export function parseInstant(value: string | number | Date | null | undefined) {
  if (value === null || value === undefined) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Human relative age; never says "live" for cached data. */
function formatRelativeAge(locale: Locale, ageMs: number): string {
  if (ageMs < MINUTE) return translate(locale, 'freshness.justNow');
  if (ageMs < HOUR) {
    return translate(locale, 'freshness.minutesAgo', {
      count: Math.floor(ageMs / MINUTE),
    });
  }
  if (ageMs < DAY) {
    return translate(locale, 'freshness.hoursAgo', {
      count: Math.floor(ageMs / HOUR),
    });
  }
  return translate(locale, 'freshness.daysAgo', {
    count: Math.floor(ageMs / DAY),
  });
}

/** The calendar year in India, which is the year the display is in. */
function istYear(date: Date): string {
  return new Intl.DateTimeFormat('en', {
    timeZone: DISPLAY_TIME_ZONE,
    year: 'numeric',
  }).format(date);
}

/**
 * Absolute time in IST with an explicit timezone label. The year is shown when
 * it is not this year: "01 Jan, 05:30" for a run in 2000 reads as this January.
 */
export function formatAbsoluteTime(
  locale: Locale,
  date: Date,
  options: Intl.DateTimeFormatOptions = {},
  now: Date = new Date(),
): string {
  try {
    const formatted = new Intl.DateTimeFormat(intlLocale(locale), {
      timeZone: DISPLAY_TIME_ZONE,
      day: '2-digit',
      month: 'short',
      ...(istYear(date) === istYear(now) ? {} : { year: 'numeric' as const }),
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      ...options,
    }).format(date);
    return `${formatted} ${translate(locale, 'freshness.timezone')}`;
  } catch {
    return `${date.toISOString()} UTC`;
  }
}

/** A calendar date as India sees it, in the chosen language. */
export function formatAbsoluteDate(locale: Locale, date: Date): string {
  try {
    return new Intl.DateTimeFormat(intlLocale(locale), {
      timeZone: DISPLAY_TIME_ZONE,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(date);
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

export function describeFreshness(
  locale: Locale,
  asOf: string | number | Date | null | undefined,
  now: Date,
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
): Freshness {
  const date = parseInstant(asOf);
  if (!date) {
    return {
      ageMs: null,
      relative: translate(locale, 'freshness.never'),
      absolute: '',
      stale: true,
    };
  }
  const ageMs = Math.max(0, now.getTime() - date.getTime());
  return {
    ageMs,
    relative: formatRelativeAge(locale, ageMs),
    absolute: formatAbsoluteTime(locale, date),
    stale: ageMs > staleAfterMs,
  };
}

/** Distances are always shown in metric units regardless of locale. */
export function formatDistance(locale: Locale, metres: number): string {
  if (!Number.isFinite(metres)) return '-';
  if (metres < 1000) {
    return translate(locale, 'units.m', {
      value: formatNumber(locale, Math.round(metres)),
    });
  }
  return translate(locale, 'units.km', {
    value: formatNumber(locale, metres / 1000, {
      maximumFractionDigits: metres < 10_000 ? 1 : 0,
    }),
  });
}

/** Weights always state the unit explicitly. */
export function formatWeight(locale: Locale, kilograms: number): string {
  if (!Number.isFinite(kilograms)) return '-';
  if (kilograms >= 1000) {
    return translate(locale, 'units.t', {
      value: formatNumber(locale, kilograms / 1000, {
        maximumFractionDigits: 1,
      }),
    });
  }
  return translate(locale, 'units.kg', {
    value: formatNumber(locale, Math.round(kilograms)),
  });
}

export function formatDuration(locale: Locale, ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '-';
  if (ms < HOUR) {
    return translate(locale, 'units.minutes', {
      count: Math.max(1, Math.round(ms / MINUTE)),
    });
  }
  const hours = Math.floor(ms / HOUR);
  const minutes = Math.round((ms % HOUR) / MINUTE);
  const h = translate(locale, 'units.hours', { count: hours });
  return minutes
    ? `${h} ${translate(locale, 'units.minutes', { count: minutes })}`
    : h;
}

/** Rounding a small file to "0 KB" reads as empty, so bytes are kept below 1 KB. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
