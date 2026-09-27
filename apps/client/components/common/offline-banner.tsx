'use client';

import Link from 'next/link';
import { CloudOff, RefreshCw } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { Button } from '@/components/ui/button';
import { useOnlineStatus } from '@/hooks/use-online-status';
import { cn } from '@/lib/utils';

type OfflineBannerProps = {
  /** Number of queued local writes, when the outbox is available. */
  queuedCount?: number;
  /** Retry handler for the outbox; hidden when absent. */
  onRetry?: () => void;
  /** Link to the sync centre; hidden when absent. */
  syncHref?: string;
  /** Force visibility regardless of connectivity (prototype previews, tests). */
  forceVisible?: boolean;
  className?: string;
};

/**
 * Persistent, non-dismissable banner shown whenever the device is offline.
 * It states plainly that nothing has reached the control room yet.
 */
export function OfflineBanner({
  queuedCount,
  onRetry,
  syncHref,
  forceVisible = false,
  className,
}: OfflineBannerProps) {
  const t = useT();
  const online = useOnlineStatus();
  if (online && !forceVisible) return null;

  return (
    <output
      aria-live="polite"
      data-offline-banner=""
      className={cn(
        'flex flex-wrap items-start gap-3 rounded-lg border border-[#FDE68A] bg-[#FEF3C7] px-3 py-2.5 text-[#92400E]',
        className,
      )}
    >
      <CloudOff aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{t('offline.title')}</p>
        <p className="mt-0.5 text-xs leading-relaxed">{t('offline.body')}</p>
        {typeof queuedCount === 'number' ? (
          <p className="mt-1 text-xs font-medium">
            {t('offline.queued', { count: queuedCount })}
          </p>
        ) : null}
      </div>
      {onRetry || syncHref ? (
        <div className="flex shrink-0 items-center gap-2">
          {onRetry ? (
            <Button
              size="sm"
              variant="outline"
              className="border-[#FDE68A] bg-white text-[#92400E] hover:bg-[#FFFBEB]"
              onClick={onRetry}
            >
              <RefreshCw aria-hidden="true" />
              {t('offline.retry')}
            </Button>
          ) : null}
          {syncHref ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-[#92400E] hover:bg-[#FDE68A]/50"
              render={<Link href={syncHref} />}
            >
              {t('offline.viewQueue')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </output>
  );
}
