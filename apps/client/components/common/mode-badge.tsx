'use client';

import { Database, FlaskConical, Radio, Video } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { cn } from '@/lib/utils';

/** What the data on screen actually is. Reported by the backend, never guessed by the client. */
export type DataMode = 'synthetic' | 'recorded' | 'live';

const MODES = {
  synthetic: {
    icon: FlaskConical,
    key: 'mode.synthetic',
    className: 'font-semibold uppercase tracking-wide text-[#92400E]',
  },
  recorded: { icon: Video, key: 'mode.recorded', className: 'text-[#1E3A8A]' },
  live: { icon: Radio, key: 'mode.live', className: 'text-[#166534]' },
  none: {
    icon: Database,
    key: 'mode.noData',
    className: 'text-muted-foreground',
  },
} as const;

/**
 * Where the data on screen comes from. A simulated scenario is always marked;
 * `null` means no source is connected at all.
 */
export function ModeBadge({
  dataMode,
  className,
}: {
  dataMode: DataMode | null;
  className?: string;
}) {
  const t = useT();
  const mode = MODES[dataMode ?? 'none'];
  const Icon = mode.icon;

  return (
    <span
      data-data-mode={dataMode ?? 'none'}
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap',
        mode.className,
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      <span className="sr-only">{t('mode.label')}: </span>
      {t(mode.key)}
    </span>
  );
}
