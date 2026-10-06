'use client';

import { Activity, BellOff, Database, RefreshCw } from 'lucide-react';

import { CommandShell } from '@/components/layout/command-shell';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { ModeBadge, type DataMode } from '@/components/common/mode-badge';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import {
  useDataHealth,
  usePushStatus,
  useRecomputeRisk,
  useRiskOutcomes,
} from '@/lib/api/hooks';
import type {
  CoverageReport,
  RiskOutcomesResponse,
  SourceHealth,
} from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

/** Where an operator finds out what the system actually knows. */
export function DataHealthScreen() {
  const t = useT();
  const health = useDataHealth();
  const push = usePushStatus();
  const recompute = useRecomputeRisk();

  const data = health.data;

  return (
    <CommandShell
      activeHref="/data-health"
      title={t('health.title')}
      headerExtra={
        data ? (
          <FreshnessLabel asOf={data.server_time} showAbsolute />
        ) : undefined
      }
    >
      <div className="flex flex-col gap-6">
        {health.isError && (
          <ErrorPanel
            title={t('health.error.load')}
            message={
              health.error instanceof Error ? health.error.message : undefined
            }
            onRetry={() => void health.refetch()}
          />
        )}

        {health.isPending && (
          <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
        )}

        {data && (
          <>
            <section className="flex flex-wrap items-center gap-3">
              <ModeBadge dataMode={dataModeFromSources(data.sources)} />
              <span className="text-xs text-muted-foreground">
                {t('health.mode', { mode: t(`mode.${data.app_mode}`) })}
              </span>
            </section>

            <section className="flex min-w-0 flex-col gap-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('health.sourcesHeading')}
              </h2>
              {data.sources.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t('health.noSources')}
                </p>
              ) : (
                <ul className="grid gap-2 md:grid-cols-2">
                  {data.sources.map((source) => (
                    <SourceCard key={source.source} source={source} />
                  ))}
                </ul>
              )}
            </section>

            <section className="flex min-w-0 flex-col gap-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t('health.coverageHeading')}
              </h2>
              <ul className="flex flex-col gap-2">
                {data.coverage.map((district) => (
                  <CoverageCard
                    key={district.district_id}
                    coverage={district}
                    busy={recompute.isPending}
                    onRecompute={() =>
                      recompute.mutate({
                        districtId: district.district_id,
                        idempotencyKey: crypto.randomUUID(),
                      })
                    }
                  />
                ))}
              </ul>
              {recompute.isError && (
                <ErrorPanel
                  title={t('health.error.recompute')}
                  message={
                    recompute.error instanceof Error
                      ? recompute.error.message
                      : undefined
                  }
                />
              )}
              {recompute.data && (
                <p
                  className="text-xs text-muted-foreground"
                  data-recompute-result
                >
                  {t('health.recomputed', {
                    scored: recompute.data.segments_scored,
                    unscored: recompute.data.segments_unscored,
                    model: recompute.data.model_version,
                  })}
                  {(recompute.data.inspection_suggestions ?? []).length > 0
                    ? ` ${t('health.suggested', {
                        count: (recompute.data.inspection_suggestions ?? [])
                          .length,
                      })}`
                    : ''}
                </p>
              )}
            </section>

            <section className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border border-border bg-card p-4">
                <h2 className="flex items-center gap-2 text-sm font-semibold">
                  <Activity className="size-4" aria-hidden />
                  {t('health.queuesHeading')}
                </h2>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                  <Figure
                    label={t('health.outbox')}
                    value={data.queues.unprocessed_outbox}
                  />
                  <Figure
                    label={t('health.ledger')}
                    value={data.queues.idempotency_ledger_rows}
                  />
                  <Figure
                    label={t('health.reporting')}
                    value={data.queues.trips_reporting}
                  />
                  <Figure
                    label={t('health.stale')}
                    value={data.queues.trips_stale}
                  />
                  <Figure
                    label={t('health.silent')}
                    value={data.queues.trips_never_reported}
                  />
                </dl>
              </div>

              <div className="rounded-lg border border-border bg-card p-4">
                <h2 className="flex items-center gap-2 text-sm font-semibold">
                  {push.data?.configured ? (
                    <Database className="size-4" aria-hidden />
                  ) : (
                    <BellOff className="size-4" aria-hidden />
                  )}
                  {t('health.pushHeading')}
                </h2>
                <p className="mt-2 text-sm">
                  {push.data?.configured
                    ? t('health.pushConfigured', {
                        sender: push.data.sender ?? '',
                      })
                    : t('health.pushNotConfigured')}
                </p>
                <div className="mt-3 border-t border-border pt-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {t('health.riskModelHeading')}
                  </h3>
                  <p className="mt-1.5 text-sm">
                    {t('health.riskModel', {
                      // The health payload types this as unknown; a version is a
                      // string or it is not reportable.
                      version:
                        typeof data.risk_model.version === 'string'
                          ? data.risk_model.version
                          : '-',
                    })}
                  </p>
                  {/* Said on the screen, not only in a model card. */}
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('health.riskModelNotTrained')}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {typeof data.risk_model.scheduled_recompute_minutes ===
                    'number'
                      ? t('health.schedule.every', {
                          minutes: data.risk_model.scheduled_recompute_minutes,
                        })
                      : t('health.schedule.manual')}
                  </p>
                  <TrainedModelStatus status={data.risk_model.trained_model} />
                </div>
              </div>
            </section>
          </>
        )}
      </div>
    </CommandShell>
  );
}

/** A trained model configured beside the baseline: running in shadow, or why not. */
export function TrainedModelStatus({ status }: { status: unknown }) {
  const t = useT();
  if (!status || typeof status !== 'object') return null;
  const { state, version, reason, inputs_missing } = status as {
    state?: string;
    version?: string | null;
    reason?: string | null;
    inputs_missing?: string[];
  };
  if (state === 'shadow') {
    return (
      <p className="mt-1 text-xs text-muted-foreground" data-trained-model>
        {t('health.trainedModel.shadow', { version: version ?? '-' })}
        {inputs_missing && inputs_missing.length > 0
          ? ` ${t('health.trainedModel.noInputs', {
              inputs: inputs_missing.join(', '),
            })}`
          : ''}
      </p>
    );
  }
  if (state === 'rejected') {
    return (
      <p className="mt-1 text-xs text-[#92400E]" data-trained-model>
        {t('health.trainedModel.rejected', { reason: reason ?? '' })}
      </p>
    );
  }
  return null;
}

/** The badge says what the sources are, not how the app is deployed. */
export function dataModeFromSources(sources: SourceHealth[]): DataMode | null {
  if (sources.some((source) => source.state === 'live')) return 'live';
  if (
    sources.some(
      (source) => source.state === 'recorded' || source.state === 'stale',
    )
  ) {
    return 'recorded';
  }
  return null;
}

function Figure({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-base font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

const STATE_STYLES: Record<string, string> = {
  live: 'text-[#047857]',
  recorded: 'text-[#1D4ED8]',
  stale: 'text-[#92400E]',
  failed: 'text-[#B03A35]',
  never_run: 'text-muted-foreground',
  disabled: 'text-muted-foreground',
};

function SourceCard({ source }: { source: SourceHealth }) {
  const t = useT();
  const formatTime = useFormatTime();
  return (
    <li
      data-source={source.source}
      data-source-state={source.state}
      className="rounded-lg border border-border bg-card p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-semibold">
          {source.source}
        </p>
        <span
          className={cn(
            'text-xs font-semibold uppercase',
            STATE_STYLES[source.state],
          )}
        >
          {t(`health.sourceState.${source.state}`)}
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {/* Last *successful*, not last attempted: an operator needs to know
            when the data was last real, not when something last tried. */}
        {source.last_success_at
          ? t('health.lastSuccess', {
              when: formatTime.time(source.last_success_at),
            })
          : t('health.neverSucceeded')}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {t('health.records', { count: source.record_count })}
        {source.consecutive_failures > 0
          ? `, ${t('health.consecutiveFailures', {
              count: source.consecutive_failures,
            })}`
          : ''}
      </p>
      {source.error_code && (
        <p className="mt-1 font-mono text-xs text-[#B03A35]">
          {source.error_code}
        </p>
      )}
    </li>
  );
}

function CoverageCard({
  coverage,
  busy,
  onRecompute,
}: {
  coverage: CoverageReport;
  busy: boolean;
  onRecompute: () => void;
}) {
  const t = useT();
  return (
    <li
      data-coverage-district={coverage.district_id}
      className="rounded-lg border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">
            {coverage.district_name ?? coverage.district_id}
          </p>
          {/* Always with its denominator. "32 reachable" alone means nothing. */}
          <p className="mt-1 text-xs text-muted-foreground" data-coverage-state>
            {t('health.observed', {
              observed: coverage.segments_with_observed_state,
              total: coverage.segments_total,
            })}
          </p>
          <p className="text-xs text-muted-foreground" data-coverage-risk>
            {t('health.scored', {
              scored: coverage.segments_with_risk_score,
              total: coverage.segments_total,
            })}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('health.facilities', {
              onGraph: coverage.facilities_on_routing_graph,
              total: coverage.facilities_total,
            })}
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={onRecompute}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          <RefreshCw className="size-3.5" aria-hidden />
          {t('health.recompute')}
        </button>
      </div>
      <p className="mt-2 font-mono text-xs text-muted-foreground">
        {coverage.graph_version ?? t('health.noGraphVersion')}
        {coverage.risk_model_version ? `, ${coverage.risk_model_version}` : ''}
      </p>
      <DistrictOutcomes districtId={coverage.district_id} />
    </li>
  );
}

const OUTCOME_DAYS = 30;

function DistrictOutcomes({ districtId }: { districtId: string }) {
  const t = useT();
  const outcomes = useRiskOutcomes(districtId, OUTCOME_DAYS);
  if (outcomes.isPending) return null;
  if (outcomes.isError) {
    return (
      <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
        {t('health.outcomes.error', {
          reason: outcomes.error instanceof Error ? outcomes.error.message : '',
        })}
      </p>
    );
  }
  return <OutcomeTable report={outcomes.data} days={OUTCOME_DAYS} />;
}

/** Each level the score reached, and how often a confirmed incident followed. */
export function OutcomeTable({
  report,
  days,
}: {
  report: RiskOutcomesResponse;
  days: number;
}) {
  const t = useT();
  // A second version appears once a trained model scores alongside the baseline;
  // then each row says which one it belongs to.
  const versions = new Set(
    report.rows.flatMap((row) =>
      row.model_version ? [row.model_version] : [],
    ),
  );
  const byVersion = versions.size > 1;
  return (
    <div className="mt-3 border-t border-border pt-3" data-risk-outcomes>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('health.outcomes.heading', { days })}
      </h3>
      {report.rows.length === 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t('health.outcomes.none')}
        </p>
      ) : (
        <table className="mt-2 w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr>
              {byVersion ? (
                <th className="py-1 pe-2 font-medium">
                  {t('health.outcomes.model')}
                </th>
              ) : null}
              <th className="py-1 pe-2 font-medium">
                {t('health.outcomes.level')}
              </th>
              <th className="py-1 pe-2 text-end font-medium">
                {t('health.outcomes.roadDays')}
              </th>
              <th className="py-1 text-end font-medium">
                {t('health.outcomes.withIncident')}
              </th>
            </tr>
          </thead>
          <tbody>
            {report.rows.map((row) => (
              <tr
                key={`${row.model_version ?? ''}:${row.level}`}
                className="border-t border-border/60"
              >
                {byVersion ? (
                  <td className="py-1 pe-2 font-mono">
                    {row.model_version ?? ''}
                  </td>
                ) : null}
                <td className="py-1 pe-2">
                  {row.level === 'not_scored'
                    ? t('health.outcomes.notScored')
                    : t(`status.risk.${row.level}`)}
                </td>
                <td className="py-1 pe-2 text-end tabular-nums">
                  {row.road_days}
                </td>
                <td className="py-1 text-end tabular-nums">
                  {row.road_days_with_confirmed_incident}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-1.5 text-xs text-muted-foreground">
        {t('health.outcomes.note')}
      </p>
    </div>
  );
}
