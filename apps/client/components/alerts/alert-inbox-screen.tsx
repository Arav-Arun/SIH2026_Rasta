'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  BellRing,
  CheckCheck,
  ClipboardCheck,
  MapPin,
  Route,
} from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { useAcknowledgeAlert, useAlerts } from '@/lib/api/hooks';
import { newUuid } from '@/lib/ids';
import type { AlertRecord, AlertSeverity } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import { PushCard } from './push-card';

/** The alert inbox. */
export function AlertInboxScreen() {
  const t = useT();
  const [unacknowledgedOnly, setUnacknowledgedOnly] = useState(false);
  const alerts = useAlerts({ unacknowledged: unacknowledgedOnly });
  const acknowledge = useAcknowledgeAlert();

  const rows = alerts.data?.alerts ?? [];

  return (
    <CommandShell
      activeHref="/alerts"
      title={t('alerts.title')}
      headerExtra={
        alerts.data ? <FreshnessLabel asOf={alerts.data.as_of} /> : undefined
      }
    >
      <div className="flex flex-col gap-4">
        <PushCard />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground" data-alert-summary>
            {t('alerts.summary', {
              unacknowledged: alerts.data?.unacknowledged ?? 0,
              total: alerts.data?.total ?? 0,
            })}
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 rounded border-border"
              checked={unacknowledgedOnly}
              onChange={(event) => setUnacknowledgedOnly(event.target.checked)}
            />
            {t('alerts.filterUnacknowledged')}
          </label>
        </div>

        {alerts.isError && (
          <ErrorPanel
            title={t('alerts.error.load')}
            message={
              alerts.error instanceof Error ? alerts.error.message : undefined
            }
            onRetry={() => void alerts.refetch()}
          />
        )}

        {acknowledge.isError && (
          <ErrorPanel
            title={t('alerts.error.acknowledge')}
            message={
              acknowledge.error instanceof Error
                ? acknowledge.error.message
                : undefined
            }
          />
        )}

        {alerts.isPending ? (
          <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
        ) : rows.length === 0 ? (
          <EmptyState title={t('alerts.empty.title')} />
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((alert) => (
              <AlertRow
                key={alert.id}
                alert={alert}
                busy={acknowledge.isPending}
                onAcknowledge={() =>
                  acknowledge.mutate({
                    alertId: alert.id,
                    idempotencyKey: newUuid(),
                  })
                }
              />
            ))}
          </ul>
        )}
      </div>
    </CommandShell>
  );
}

const SEVERITY_STYLES: Record<AlertSeverity, string> = {
  critical: 'border-l-[#B03A35] bg-[#FEF2F2]',
  warning: 'border-l-[#B45309] bg-[#FFFBEB]',
  info: 'border-l-[#1D4ED8] bg-[#EFF6FF]',
};

export function AlertRow({
  alert,
  busy,
  onAcknowledge,
}: {
  alert: AlertRecord;
  busy: boolean;
  onAcknowledge: () => void;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  const { workspace } = useAuth();
  const acknowledged = alert.status === 'acknowledged';
  const payload = (alert.payload ?? {}) as Record<string, unknown>;
  // A withdrawn route needs a new one; take whoever may plan straight there.
  const canReplan =
    alert.type === 'trip_route_invalidated' &&
    alert.subject_type === 'trip' &&
    Boolean(alert.subject_id) &&
    (workspace?.capabilities ?? []).includes('route:plan');
  // A road worth a look: whoever assigns inspections goes to it on the map.
  const canInspect =
    alert.type === 'road_slow_traffic' &&
    alert.subject_type === 'segment' &&
    Boolean(alert.subject_id) &&
    (workspace?.capabilities ?? []).includes('inspection:manage');

  return (
    <li
      data-alert-id={alert.id}
      data-alert-severity={alert.severity}
      data-alert-status={alert.status}
      data-alert-valid={alert.valid_now}
      className={cn(
        'rounded-lg border border-border border-s-4 p-4',
        SEVERITY_STYLES[alert.severity],
        acknowledged && 'opacity-70',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold">
            <BellRing className="size-4 shrink-0" aria-hidden />
            {t(alert.title_key)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t(`alerts.severity.${alert.severity}`)},{' '}
            {formatTime.time(alert.valid_from)}
            {alert.valid_until
              ? `, ${t('alerts.validUntil', {
                  when: formatTime.time(alert.valid_until),
                })}`
              : ''}
          </p>
        </div>
        {acknowledged ? (
          <span className="flex shrink-0 items-center gap-1.5 text-xs font-medium text-[#047857]">
            <CheckCheck className="size-4" aria-hidden />
            {t('alerts.acknowledged')}
          </span>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={onAcknowledge}
            className="shrink-0 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          >
            {t('alerts.acknowledge')}
          </button>
        )}
      </div>

      {/* An expired alert is still shown. Hiding it would make an inbox that
          aged out look like an inbox with nothing in it. */}
      {!alert.valid_now && (
        <p className="mt-2 text-xs font-medium text-[#92400E]">
          {t('alerts.expired')}
        </p>
      )}

      <AlertEvidence payload={payload} />

      {canReplan && (
        <Link
          href={`/planner?trip=${encodeURIComponent(alert.subject_id as string)}`}
          data-replan-trip={alert.subject_id}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium"
        >
          <Route className="size-4" aria-hidden />
          {t('alerts.replan')}
        </Link>
      )}
      {canInspect && (
        <Link
          href={`/map?segment=${encodeURIComponent(alert.subject_id as string)}`}
          data-inspect-segment={alert.subject_id}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium"
        >
          <ClipboardCheck className="size-4" aria-hidden />
          {t('alerts.inspect')}
        </Link>
      )}
    </li>
  );
}

/**
 * Every alert names the records behind it, so a recipient can go and look
 * rather than take the alert's word for it.
 */
export function AlertEvidence({
  payload,
}: {
  payload: Record<string, unknown>;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  const entries: string[] = [];

  // An SOS: who, when, and where the phone last knew it was.
  const reporter = payload.reporter_name;
  if (typeof reporter === 'string' && reporter) {
    entries.push(t('alerts.evidence.reporter', { name: reporter }));
  }
  const pressedAt = payload.captured_at;
  if (typeof pressedAt === 'string' && pressedAt) {
    entries.push(
      t('alerts.evidence.pressedAt', { time: formatTime.time(pressedAt) }),
    );
  }
  const latitude = payload.latitude;
  const longitude = payload.longitude;
  const position =
    typeof latitude === 'number' && typeof longitude === 'number'
      ? { latitude, longitude }
      : null;
  if (position) {
    const accuracy = payload.accuracy_m;
    const values = {
      latitude: position.latitude.toFixed(5),
      longitude: position.longitude.toFixed(5),
    };
    entries.push(
      typeof accuracy === 'number'
        ? t('alerts.evidence.position', {
            ...values,
            accuracy: Math.round(accuracy),
          })
        : t('alerts.evidence.positionNoAccuracy', values),
    );
  } else if (payload.position_known === false) {
    entries.push(t('alerts.evidence.noPosition'));
  }
  const note = payload.note;
  if (typeof note === 'string' && note) {
    entries.push(t('alerts.evidence.note', { note }));
  }
  const helpRequest = payload.calls_emergency_services === false;

  const segmentCount = payload.segment_count;
  if (typeof segmentCount === 'number') {
    entries.push(t('alerts.evidence.segments', { count: segmentCount }));
  }
  const reference = payload.consignment_reference;
  if (typeof reference === 'string' && reference) {
    entries.push(t('alerts.evidence.consignment', { reference }));
  }
  const blocked = payload.blocked_segment_ids;
  if (Array.isArray(blocked) && blocked.length > 0) {
    entries.push(t('alerts.evidence.blocked', { count: blocked.length }));
  }
  const facility = payload.facility_name ?? payload.facility_id;
  if (typeof facility === 'string' && facility) {
    entries.push(t('alerts.evidence.facility', { facility }));
  }
  const reasons = payload.reason_codes;
  if (Array.isArray(reasons) && reasons.length > 0) {
    entries.push(t('alerts.evidence.reasons', { reasons: reasons.join(', ') }));
  }

  // Slow vehicles on a road: what the probes saw, and that nothing was closed.
  const median = payload.median_kph;
  const expected = payload.expected_kph;
  const trips = payload.trips;
  if (
    typeof median === 'number' &&
    typeof expected === 'number' &&
    typeof trips === 'number'
  ) {
    entries.push(
      t('alerts.evidence.slowTraffic', {
        trips,
        median: Math.round(median),
        expected: Math.round(expected),
      }),
    );
  }
  const notClosed =
    payload.suggested_action === 'assign_inspection' &&
    payload.changes_passability === false;

  const notReplaced = payload.route_replaced_automatically === false;

  if (entries.length === 0 && !notReplaced && !helpRequest && !notClosed)
    return null;

  return (
    <div className="mt-3 border-t border-border/60 pt-2">
      {entries.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
          {entries.map((entry) => (
            <li key={entry}>{entry}</li>
          ))}
        </ul>
      )}
      {/* Said out loud because the alternative is a driver assuming a new route
          appeared by itself. */}
      {notReplaced && (
        <p className="mt-1.5 text-xs font-medium">{t('alerts.notReplaced')}</p>
      )}
      {position && (
        <a
          href={`https://www.openstreetmap.org/?mlat=${position.latitude}&mlon=${position.longitude}#map=17/${position.latitude}/${position.longitude}`}
          target="_blank"
          rel="noopener noreferrer"
          data-sos-position
          className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium underline underline-offset-2"
        >
          <MapPin className="size-4" aria-hidden />
          {t('alerts.evidence.openMap')}
        </a>
      )}
      {notClosed && (
        <p className="mt-1.5 text-xs font-medium">
          {t('alerts.evidence.notClosed')}
        </p>
      )}
      {/* Said on every SOS, so nobody reads the control room's copy as a call
          for an ambulance having been made. */}
      {helpRequest && (
        <p className="mt-1.5 text-xs font-medium">
          {t('alerts.evidence.notDispatch')}
        </p>
      )}
    </div>
  );
}
