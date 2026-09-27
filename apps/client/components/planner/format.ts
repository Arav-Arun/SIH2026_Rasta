import type { RouteAlternative } from '@/lib/api/contracts';

/** Seconds to a compact "1 h 12 min" / "12 min" string. */
export function formatDurationSeconds(seconds: number): {
  hours: number;
  minutes: number;
} {
  const total = Math.max(0, Math.round(seconds / 60));
  return { hours: Math.floor(total / 60), minutes: total % 60 };
}

export function formatDistanceMetres(metres: number): {
  value: string;
  unit: 'km' | 'm';
} {
  if (metres >= 1000) {
    return { value: (metres / 1000).toFixed(1), unit: 'km' };
  }
  return { value: String(Math.round(metres)), unit: 'm' };
}

/** How much of the deadline is left once this route is driven. */
export function deadlineMarginSeconds(
  alternative: Pick<RouteAlternative, 'eta_range_seconds'>,
  departureIso: string | null,
  deadlineIso: string | null,
): number | null {
  if (!deadlineIso) return null;
  const departure = departureIso ? Date.parse(departureIso) : Date.now();
  const deadline = Date.parse(deadlineIso);
  if (Number.isNaN(departure) || Number.isNaN(deadline)) return null;
  const slowest = alternative.eta_range_seconds[1] ?? 0;
  return Math.round((deadline - departure) / 1000 - slowest);
}

type MarginVerdict = 'comfortable' | 'tight' | 'late' | 'unknown';

export function judgeMargin(marginSeconds: number | null): MarginVerdict {
  if (marginSeconds === null) return 'unknown';
  if (marginSeconds < 0) return 'late';
  if (marginSeconds < 1800) return 'tight';
  return 'comfortable';
}

/** Warnings on this route that a person must resolve before it is driven. */
export function reviewWarnings(alternative: RouteAlternative) {
  return alternative.constraint_warnings.filter(
    (warning) =>
      (warning as { requires_review?: boolean }).requires_review === true,
  );
}

export function unknownConstraintCount(alternative: RouteAlternative): number {
  return alternative.constraint_warnings
    .filter(
      (warning) => (warning as { code?: string }).code === 'unknown_constraint',
    )
    .reduce(
      (total, warning) =>
        total +
        ((warning as { segment_ids?: string[] }).segment_ids?.length ?? 0),
      0,
    );
}

/** How many confirmed closures this plan kept the route away from. */
export function avoidedClosureCount(
  exclusions: Record<string, { segment_id: string }[]>,
): number {
  return (exclusions.segment_closed ?? []).length;
}
