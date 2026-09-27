'use client';

import Link from 'next/link';
import { MapPinned } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { useLocale } from '@/components/i18n/locale-provider';
import { NetworkMap } from '@/components/map/network-map';
import { CommandShell } from '@/components/layout/command-shell';
import { DataList } from '@/components/common/data-list';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiClientError } from '@/lib/api/client';
import type { ConnectivitySummaryResponse } from '@/lib/api/contracts';
import {
  useConnectivitySummary,
  useIncidents,
  useNetworkSegments,
  useTrips,
} from '@/lib/api/hooks';
import type { BBox } from '@/lib/api/network';
import { describeFreshness } from '@/lib/i18n/format';

function SummaryCard({
  label,
  value,
  detail,
  href,
  loading,
}: {
  label: string;
  value: string;
  detail: string;
  href?: string;
  loading?: boolean;
}) {
  const body = (
    <CardContent className="min-w-0 pt-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {loading ? (
        <Skeleton className="mt-1 h-8 w-20" />
      ) : (
        <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">
          {value}
        </p>
      )}
      <p className="mt-1 truncate text-xs text-muted-foreground">{detail}</p>
    </CardContent>
  );
  return href ? (
    <Card className="min-w-0 shadow-none transition-colors hover:bg-slate-50">
      <Link
        href={href}
        className="block focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary rounded-xl"
      >
        {body}
      </Link>
    </Card>
  ) : (
    <Card className="min-w-0 shadow-none">{body}</Card>
  );
}

/** The trip list endpoint caps at this; a full page means there may be more. */
const TRIP_PAGE = 200;

/** What the active-deliveries card says. */
function useActiveDeliveries() {
  const active = useTrips('active', TRIP_PAGE);
  const paused = useTrips('paused', TRIP_PAGE);

  const forbidden = [active.error, paused.error].some(
    (error) => error instanceof ApiClientError && error.kind === 'forbidden',
  );
  if (forbidden) return { state: 'out_of_scope' as const };
  if (active.isError || paused.isError) return { state: 'error' as const };
  if (!active.data || !paused.data) return { state: 'loading' as const };

  const moving = active.data.trips.length;
  const halted = paused.data.trips.length;
  return {
    state: 'ready' as const,
    moving,
    halted,
    // A full page cannot say how many more there are, so it says "at least".
    capped: moving >= TRIP_PAGE || halted >= TRIP_PAGE,
  };
}

/** Command overview. Every number on it is backed by an endpoint. */
export function OverviewScreen() {
  const { locale, t } = useLocale();
  const { workspace } = useAuth();
  const districts = workspace?.districts ?? [];
  const primaryDistrict = districts[0]?.id ?? null;

  const summaryQuery = useConnectivitySummary(primaryDistrict);
  const incidentsQuery = useIncidents('submitted', 20);
  const deliveries = useActiveDeliveries();
  const summary = summaryQuery.data;
  const coverageBBox: BBox | null = summary ? facilitiesBBox(summary) : null;
  // Map snapshot: request the district's facility extent (bounded by the API).
  const segmentsQuery = useNetworkSegments(
    coverageBBox
      ? { bbox: coverageBBox, districtId: primaryDistrict, simplifyM: 5 }
      : null,
  );

  const now = new Date();
  const computed = summary
    ? describeFreshness(locale, summary.computed_at, now).relative
    : '';
  const isolated =
    summary?.facility_status.filter((f) => f.status === 'isolated') ?? [];
  const pending = incidentsQuery.data?.incidents ?? [];
  const queueEmpty =
    !summaryQuery.isLoading &&
    !incidentsQuery.isLoading &&
    isolated.length === 0 &&
    pending.length === 0;

  return (
    <CommandShell
      ownHeading
      activeHref="/overview"
      title={t('nav.overview')}
      headerExtra={
        <FreshnessLabel
          asOf={summary?.computed_at ?? null}
          showAbsolute={false}
        />
      }
    >
      <section className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t('overview.title')}
        </h1>
        {!primaryDistrict ? (
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            {t('overview.subtitle')}
          </p>
        ) : null}
      </section>

      {summaryQuery.error ? (
        <ErrorPanel
          className="mb-4"
          requestId={
            summaryQuery.error instanceof ApiClientError
              ? summaryQuery.error.requestId
              : null
          }
          message={
            summaryQuery.error instanceof ApiClientError
              ? summaryQuery.error.message
              : undefined
          }
          onRetry={() => void summaryQuery.refetch()}
        />
      ) : null}

      <section
        aria-label={t('overview.summaries')}
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <SummaryCard
          label={t('overview.reachableFacilities')}
          value={
            summary
              ? t('overview2.reachableOf', {
                  reachable: summary.facilities.reachable,
                  monitored: summary.facilities.monitored,
                })
              : '-'
          }
          detail={
            summary
              ? t('overview2.computedAt', { when: computed })
              : t('overview.reasonNoNetwork')
          }
          href="/map"
          loading={summaryQuery.isLoading}
        />
        <SummaryCard
          label={t('overview.activeDeliveries')}
          value={
            deliveries.state === 'ready'
              ? `${deliveries.moving + deliveries.halted}${deliveries.capped ? '+' : ''}`
              : '-'
          }
          detail={
            deliveries.state === 'ready'
              ? t('overview2.deliveriesDetail', {
                  moving: deliveries.moving,
                  paused: deliveries.halted,
                })
              : deliveries.state === 'out_of_scope'
                ? t('overview2.deliveriesOutOfScope')
                : deliveries.state === 'error'
                  ? t('overview2.deliveriesUnavailable')
                  : ''
          }
          href={deliveries.state === 'out_of_scope' ? undefined : '/fleet'}
          loading={deliveries.state === 'loading'}
        />
        <SummaryCard
          label={t('overview2.blockedSegments')}
          value={
            summary
              ? String(summary.segments.closed + summary.segments.restricted)
              : '-'
          }
          detail={
            summary
              ? t('overview2.blockedDetail', {
                  closed: summary.segments.closed,
                  restricted: summary.segments.restricted,
                })
              : t('overview.reasonNoIncidents')
          }
          href="/map"
          loading={summaryQuery.isLoading}
        />
        <SummaryCard
          label={t('overview2.awaitingReview')}
          value={incidentsQuery.data ? String(incidentsQuery.data.total) : '-'}
          detail={
            incidentsQuery.data
              ? t('overview2.awaitingDetail', {
                  count: incidentsQuery.data.total,
                })
              : t('overview.reasonNoReports')
          }
          href="/incidents"
          loading={incidentsQuery.isLoading}
        />
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="shadow-none">
          <CardHeader className="border-b">
            <CardTitle>{t('overview.networkCard')}</CardTitle>
          </CardHeader>
          <CardContent className="pt-4">
            {summary && summary.coverage_state !== 'none' ? (
              <div className="h-[360px]">
                <NetworkMap
                  segments={segmentsQuery.data?.features ?? []}
                  facilities={summary.facility_status}
                  coverageBBox={
                    segmentsQuery.data?.coverage.bbox
                      ? {
                          minLon: segmentsQuery.data.coverage.bbox.min_lon,
                          minLat: segmentsQuery.data.coverage.bbox.min_lat,
                          maxLon: segmentsQuery.data.coverage.bbox.max_lon,
                          maxLat: segmentsQuery.data.coverage.bbox.max_lat,
                        }
                      : coverageBBox
                  }
                  interactive={false}
                  className="h-full"
                />
              </div>
            ) : (
              <EmptyState title={t('empty.noMap')} />
            )}
            <div className="mt-3 flex justify-end">
              <Button
                size="sm"
                variant="outline"
                nativeButton={false}
                render={<Link href="/map" />}
              >
                <MapPinned aria-hidden="true" />
                {t('overview2.openMap')}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-none">
          <CardHeader className="border-b">
            <CardTitle>{t('overview.queueCard')}</CardTitle>
          </CardHeader>
          <CardContent className="pt-4">
            {queueEmpty ? (
              <EmptyState title={t('empty.noDecisions')} />
            ) : (
              <ol className="space-y-2" data-testid="decision-queue">
                {isolated.map((facility) => (
                  <li
                    key={facility.facility_id}
                    className="rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-3"
                  >
                    <p className="text-sm font-medium">
                      {t('overview2.queueIsolated')}
                    </p>
                    <p className="text-sm">{facility.name}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t('overview2.queueIsolatedBody')}
                    </p>
                  </li>
                ))}
                {pending.map((incident) => (
                  <li
                    key={incident.id}
                    className="rounded-lg border bg-white p-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">
                        {t('overview2.queueReport')}
                      </p>
                      <StatusBadge kind="review" value="reported" />
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t('overview2.queueReportBody', {
                        type: t(`incidentType.${incident.type}`),
                        when: describeFreshness(
                          locale,
                          incident.reported_at,
                          now,
                        ).relative,
                      })}
                    </p>
                  </li>
                ))}
                <li className="pt-1">
                  <Button
                    size="sm"
                    variant="outline"
                    nativeButton={false}
                    render={<Link href="/incidents" />}
                  >
                    {t('overview2.openIncidents')}
                  </Button>
                </li>
              </ol>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="mt-6">
        <Card className="shadow-none">
          <CardHeader className="border-b">
            <CardTitle>{t('overview2.districtTable')}</CardTitle>
          </CardHeader>
          <CardContent className="pt-4">
            <DataList
              columns={[
                {
                  key: 'district',
                  header: t('overview2.colDistrict'),
                  render: (row) => `${row.name} (${row.state_code})`,
                },
                {
                  key: 'reachable',
                  header: t('overview2.colReachable'),
                  numeric: true,
                  render: (row) =>
                    row.id === primaryDistrict && summary
                      ? t('overview2.reachableOf', {
                          reachable: summary.facilities.reachable,
                          monitored: summary.facilities.monitored,
                        })
                      : '-',
                },
                {
                  key: 'closed',
                  header: t('overview2.colClosed'),
                  numeric: true,
                  render: (row) =>
                    row.id === primaryDistrict && summary
                      ? String(summary.segments.closed)
                      : '-',
                },
                {
                  key: 'unknown',
                  header: t('overview2.colUnknown'),
                  numeric: true,
                  render: (row) =>
                    row.id === primaryDistrict && summary
                      ? String(summary.segments.unknown)
                      : '-',
                },
                {
                  key: 'computed',
                  header: t('overview2.colComputed'),
                  render: (row) =>
                    row.id === primaryDistrict && summary ? (
                      <span>
                        {t(
                          `overview2.coverage${summary.coverage_state.charAt(0).toUpperCase()}${summary.coverage_state.slice(1)}`,
                        )}
                        , {computed}
                      </span>
                    ) : (
                      '-'
                    ),
                },
              ]}
              rows={districts}
              getRowId={(row) => row.id}
              titleKey="district"
              allowToggle={false}
              emptyMessage={t('overview.reasonNoNetwork')}
            />
          </CardContent>
        </Card>
      </section>
    </CommandShell>
  );
}

function facilitiesBBox(summary: ConnectivitySummaryResponse): BBox | null {
  const points = summary.facility_status
    .map((facility) => facility.location)
    .filter((location): location is { longitude: number; latitude: number } =>
      Boolean(location),
    );
  if (points.length === 0) return null;
  const lons = points.map((point) => point.longitude);
  const lats = points.map((point) => point.latitude);
  const pad = 0.005;
  return {
    minLon: Math.min(...lons) - pad,
    minLat: Math.min(...lats) - pad,
    maxLon: Math.max(...lons) + pad,
    maxLat: Math.max(...lats) + pad,
  };
}
