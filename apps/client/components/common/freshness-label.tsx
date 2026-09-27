'use client';

import { useEffect, useState } from 'react';
import { Clock3 } from 'lucide-react';

import { useLocale } from '@/components/i18n/locale-provider';
import {
  DEFAULT_STALE_AFTER_MS,
  describeFreshness,
  parseInstant,
} from '@/lib/i18n/format';
import { cn } from '@/lib/utils';

/** Re-render cadence so "3 min ago" keeps moving without a page refresh. */
const TICK_MS = 30_000;

type FreshnessLabelProps = {
  /** ISO timestamp (or Date) of the last successful update; null = never. */
  asOf: string | Date | null | undefined;
  /** Where the value came from, e.g. "IMD district warning". */
  source?: string;
  /** Age after which the value is marked stale. */
  staleAfterMs?: number;
  /** Fixed clock for deterministic rendering in tests and screenshots. */
  now?: Date;
  className?: string;
  /** Show the absolute time (IST) as well as the relative age. */
  showAbsolute?: boolean;
};

function useClock(fixed?: Date): Date {
  const [tick, setTick] = useState<Date>(() => new Date());
  useEffect(() => {
    if (fixed) return;
    const id = setInterval(() => setTick(new Date()), TICK_MS);
    return () => clearInterval(id);
  }, [fixed]);
  return fixed ?? tick;
}

/**
 * Freshness: relative age, absolute IST time, source and a visible "Stale"
 * marker. It never renders the word "live" for cached data.
 */
export function FreshnessLabel({
  asOf,
  source,
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
  now: fixedNow,
  className,
  showAbsolute = true,
}: FreshnessLabelProps) {
  const { locale, t } = useLocale();
  const now = useClock(fixedNow);
  const freshness = describeFreshness(locale, asOf, now, staleAfterMs);
  const instant = parseInstant(asOf);

  return (
    <span
      data-stale={freshness.stale ? 'true' : 'false'}
      className={cn(
        'inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs',
        freshness.stale ? 'text-[#92400E]' : 'text-muted-foreground',
        className,
      )}
    >
      <Clock3 aria-hidden="true" className="size-3.5 shrink-0" />
      {instant ? (
        <time dateTime={instant.toISOString()}>
          {t('freshness.updated', { relative: freshness.relative })}
        </time>
      ) : (
        <span>{freshness.relative}</span>
      )}
      {instant && showAbsolute ? <span>({freshness.absolute})</span> : null}
      {source ? <span>{t('freshness.source', { source })}</span> : null}
      {freshness.stale ? (
        <span className="font-semibold">{t('freshness.stale')}</span>
      ) : null}
    </span>
  );
}
