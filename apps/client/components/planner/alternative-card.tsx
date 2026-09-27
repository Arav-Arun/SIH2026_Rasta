'use client';

import {
  AlertTriangle,
  Ban,
  CircleHelp,
  Clock3,
  Route as RouteIcon,
} from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import type { RouteAlternative } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import { RANK_COLORS } from './route-map';
import {
  deadlineMarginSeconds,
  formatDistanceMetres,
  formatDurationSeconds,
  judgeMargin,
  unknownConstraintCount,
} from './format';

type AlternativeCardProps = {
  alternative: RouteAlternative;
  avoidedClosures: number;
  departureIso: string | null;
  deadlineIso: string | null;
  selected: boolean;
  onSelect: () => void;
  onApprove: () => void;
  approveDisabled: boolean;
};

function useDuration() {
  const t = useT();
  return (seconds: number) => {
    const { hours, minutes } = formatDurationSeconds(seconds);
    return hours > 0
      ? `${t('units.hours', { count: hours })} ${t('units.minutes', { count: minutes })}`
      : t('units.minutes', { count: minutes });
  };
}

/** One option, with the facts a dispatcher needs to defend choosing it. */
export function AlternativeCard({
  alternative,
  avoidedClosures,
  departureIso,
  deadlineIso,
  selected,
  onSelect,
  onApprove,
  approveDisabled,
}: AlternativeCardProps) {
  const t = useT();
  const duration = useDuration();

  const distance = formatDistanceMetres(alternative.distance_m);
  const [low, high] = alternative.eta_range_seconds;
  const margin = deadlineMarginSeconds(alternative, departureIso, deadlineIso);
  const verdict = judgeMargin(margin);
  const unknownLimits = unknownConstraintCount(alternative);
  const color = RANK_COLORS[(alternative.rank - 1) % RANK_COLORS.length];

  const risk = alternative.risk_summary as {
    max_score?: number | null;
    segments_without_a_risk_score?: number;
  };

  return (
    <li
      data-alternative-rank={alternative.rank}
      data-alternative-category={alternative.category}
      data-selected={selected ? 'true' : 'false'}
      className={cn(
        'rounded-lg border bg-card p-3',
        selected ? 'border-primary ring-1 ring-primary/30' : 'border-border',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden
            className="mt-0.5 h-3 w-3 shrink-0 rounded-full"
            style={{ backgroundColor: color }}
          />
          <p className="truncate text-sm font-semibold">
            {t(`planner.category.${alternative.category}`)}
          </p>
        </div>
        {alternative.requires_review && (
          <span className="shrink-0 rounded-md bg-[#FEF3C7] px-2 py-0.5 text-xs font-medium text-[#92400E]">
            {t('planner.needsReview')}
          </span>
        )}
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">
            {t('planner.distance')}
          </dt>
          <dd className="font-medium tabular-nums">
            {t(`units.${distance.unit}`, { value: distance.value })}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-muted-foreground">{t('planner.eta')}</dt>
          {/* A range, never a single figure: the speeds behind it are
              configured rather than measured. */}
          <dd className="flex items-center gap-1 font-medium tabular-nums">
            <Clock3
              className="size-3.5 shrink-0 text-muted-foreground"
              aria-hidden
            />
            {t('planner.etaRange', {
              low: duration(low),
              high: duration(high),
            })}
          </dd>
        </div>
        <div className="col-span-2 min-w-0">
          <dt className="text-xs text-muted-foreground">
            {t('planner.margin')}
          </dt>
          <dd
            data-margin={verdict}
            className={cn(
              'font-medium',
              verdict === 'late' && 'text-[#991B1B]',
              verdict === 'tight' && 'text-[#92400E]',
            )}
          >
            {margin === null
              ? t('planner.marginNone')
              : margin < 0
                ? t('planner.marginLate', { duration: duration(-margin) })
                : t('planner.marginSpare', { duration: duration(margin) })}
          </dd>
        </div>
      </dl>

      <ul className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
        <li className="flex items-start gap-1.5">
          <Ban className="mt-px size-3.5 shrink-0" aria-hidden />
          {t('planner.avoidedClosures', { count: avoidedClosures })}
        </li>
        <li className="flex items-start gap-1.5">
          <CircleHelp className="mt-px size-3.5 shrink-0" aria-hidden />
          {t('planner.unknownConstraints', { count: unknownLimits })}
        </li>
        <li className="flex items-start gap-1.5">
          <RouteIcon className="mt-px size-3.5 shrink-0" aria-hidden />
          {/* A null max score means nothing was measured, not that risk is low. */}
          {risk.max_score == null
            ? t('planner.riskUnavailable')
            : t('planner.riskMax', { score: risk.max_score })}
          {risk.segments_without_a_risk_score
            ? `, ${t('planner.riskUnscored', { count: risk.segments_without_a_risk_score })}`
            : ''}
        </li>
      </ul>

      {alternative.constraint_warnings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {alternative.constraint_warnings.map((warning) => {
            const code = (warning as { code: string }).code;
            const needsReview = (warning as { requires_review?: boolean })
              .requires_review;
            return (
              <li
                key={code}
                data-warning={code}
                className={cn(
                  'flex items-start gap-1.5 rounded-md px-2 py-1 text-xs',
                  needsReview
                    ? 'bg-[#FEF3C7] text-[#92400E]'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                <AlertTriangle
                  className="mt-px size-3.5 shrink-0"
                  aria-hidden
                />
                {t(`planner.warning.${code}`)}
              </li>
            );
          })}
        </ul>
      )}

      {alternative.reasons.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          {alternative.reasons.join(' ')}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onSelect}
          disabled={selected}
          className="rounded-md border border-border px-2.5 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          {selected ? t('planner.selected') : t('planner.select')}
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={approveDisabled}
          className="rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
        >
          {t('planner.approve')}
        </button>
      </div>
    </li>
  );
}
