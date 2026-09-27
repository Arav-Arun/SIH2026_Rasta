'use client';

import type { ReactNode } from 'react';
import { AlertCircle, RefreshCw, X } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

type ErrorPanelProps = {
  title?: string;
  message?: string;
  /** Server request ID when available so support can trace the failure. */
  requestId?: string | null;
  onRetry?: () => void;
  onDismiss?: () => void;
  /** Preserved user input or extra detail rendered inside the panel. */
  children?: ReactNode;
  className?: string;
};

/**
 * Important errors stay visible (no toast). The panel keeps the user's input
 * beside a retry action and shows the request ID for traceability.
 */
export function ErrorPanel({
  title,
  message,
  requestId,
  onRetry,
  onDismiss,
  children,
  className,
}: ErrorPanelProps) {
  const t = useT();
  return (
    <div
      role="alert"
      className={cn(
        'flex gap-3 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-[#172033]',
        className,
      )}
    >
      <AlertCircle
        aria-hidden="true"
        className="mt-0.5 size-5 shrink-0 text-[#991B1B]"
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-[#991B1B]">
          {title ?? t('error.title')}
        </p>
        <p className="mt-1 text-sm leading-relaxed text-[#526079]">
          {message ?? t('error.body')}
        </p>
        {requestId ? (
          <p className="mt-1 font-mono text-[11px] text-[#526079]">
            {t('error.requestId', { id: requestId })}
          </p>
        ) : null}
        {children ? <div className="mt-3">{children}</div> : null}
        {onRetry ? (
          <div className="mt-3">
            <Button size="sm" variant="outline" onClick={onRetry}>
              <RefreshCw aria-hidden="true" />
              {t('error.retry')}
            </Button>
          </div>
        ) : null}
      </div>
      {onDismiss ? (
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label={t('error.dismiss')}
          onClick={onDismiss}
          className="-mt-1 -me-1 shrink-0"
        >
          <X aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}
