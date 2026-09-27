'use client';

import Link from 'next/link';
import { ClipboardCheck, FilePlus2, Route, X } from 'lucide-react';

import { useLocale } from '@/components/i18n/locale-provider';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ApiClientError } from '@/lib/api/client';
import type { SegmentDetailResponse } from '@/lib/api/contracts';
import type { Locale } from '@/lib/i18n/messages';
import {
  describeFreshness,
  formatAbsoluteTime,
  formatDistance,
  formatWeight,
} from '@/lib/i18n/format';
import { cn } from '@/lib/utils';

type EvidenceDrawerProps = {
  segmentId: string | null;
  detail: SegmentDetailResponse | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  onClose: () => void;
  className?: string;
};

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,38%)_minmax(0,1fr)] gap-x-3 py-1.5 text-sm">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 tabular-nums">{children}</dd>
    </div>
  );
}

/** 360 px evidence drawer for the selected road. */
export function EvidenceDrawer({
  segmentId,
  detail,
  isLoading,
  error,
  onRetry,
  onClose,
  className,
}: EvidenceDrawerProps) {
  const { locale, t } = useLocale();

  return (
    <aside
      aria-label={t('map.selectedRoad')}
      data-testid="evidence-drawer"
      className={cn(
        'flex h-full min-h-0 flex-col rounded-xl border bg-white',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">
            {t('map.selectedRoad')}
          </p>
          <h2 className="truncate text-base font-semibold">
            {detail
              ? (detail.segment.properties.name ?? t('map.unnamedRoad'))
              : segmentId
                ? '…'
                : '-'}
          </h2>
        </div>
        {segmentId ? (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={t('map.close')}
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </Button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {!segmentId ? (
          <EmptyState
            className="min-h-[220px] border-0 bg-transparent"
            title={t('map.noSelection')}
          />
        ) : error ? (
          <ErrorPanel
            requestId={error instanceof ApiClientError ? error.requestId : null}
            message={
              error instanceof ApiClientError ? error.message : undefined
            }
            onRetry={onRetry}
          />
        ) : isLoading || !detail ? (
          <div className="space-y-3" aria-busy="true">
            <Skeleton className="h-6 w-32" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <DrawerBody detail={detail} locale={locale} t={t} />
        )}
      </div>
    </aside>
  );
}

function DrawerBody({
  detail,
  locale,
  t,
}: {
  detail: SegmentDetailResponse;
  locale: Locale;
  t: (key: string, values?: Record<string, string | number>) => string;
}) {
  const props = detail.segment.properties;
  const summary = props.source_summary as Record<string, unknown>;
  const lastDecision = (summary.last_decision ?? null) as {
    impact?: string;
    reviewed_at?: string;
    reason?: string;
    incident_type?: string;
  } | null;
  const observations = detail.observations;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge kind="passability" value={props.passability} />
        <StatusBadge
          kind="risk"
          value={
            detail.risk.available ? (props.risk_level as never) : 'unavailable'
          }
        />
      </div>
      <FreshnessLabel
        asOf={props.state_as_of ?? null}
        source={typeof summary.source === 'string' ? summary.source : undefined}
        staleAfterMs={24 * 60 * 60 * 1000}
      />

      <dl className="divide-y rounded-lg border px-3">
        <Row label={t('map.roadClass')}>{props.road_class}</Row>
        <Row label={t('map.length')}>
          {formatDistance(locale, props.length_m)}
        </Row>
        <Row label={t('map.direction')}>
          <span className="font-mono text-xs">
            {props.from_node_id.replace('osm-node-', '')} →{' '}
            {props.to_node_id.replace('osm-node-', '')}
          </span>
        </Row>
        <Row label={t('map.bridge')}>
          {props.bridge ? (
            <span>
              {t('map.bridgeLimit')}:{' '}
              {props.bridge.max_weight_t == null ? (
                <span className="text-[#92400E]">
                  {t('map.bridgeLimitUnknown')}
                </span>
              ) : (
                formatWeight(locale, props.bridge.max_weight_t * 1000)
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">{t('map.noBridge')}</span>
          )}
        </Row>
      </dl>

      <Tabs defaultValue="evidence">
        <TabsList className="w-full">
          <TabsTrigger value="evidence">{t('map.tabEvidence')}</TabsTrigger>
          <TabsTrigger value="risk">{t('map.tabRisk')}</TabsTrigger>
          <TabsTrigger value="history">{t('map.tabHistory')}</TabsTrigger>
        </TabsList>

        <TabsContent value="evidence" className="space-y-3 pt-3 text-sm">
          <div>
            <p className="text-xs font-medium text-muted-foreground">
              {t('map.basis')}
            </p>
            <p className="mt-0.5 leading-relaxed">
              {typeof summary.passability_basis === 'string'
                ? summary.passability_basis
                : '-'}
            </p>
          </div>
          {lastDecision ? (
            <div className="rounded-lg border bg-slate-50 p-3">
              <p className="text-xs font-medium text-muted-foreground">
                {t('map.lastDecision')}
              </p>
              <p className="mt-1 font-medium capitalize">
                {lastDecision.impact}
              </p>
              {lastDecision.reviewed_at ? (
                <p className="text-xs text-muted-foreground">
                  {t('map.decisionBy', {
                    when: describeFreshness(
                      locale,
                      lastDecision.reviewed_at,
                      new Date(),
                    ).relative,
                  })}{' '}
                  (
                  {formatAbsoluteTime(
                    locale,
                    new Date(lastDecision.reviewed_at),
                  )}
                  )
                </p>
              ) : null}
              {lastDecision.reason ? (
                <p className="mt-2 text-sm">
                  <span className="text-xs text-muted-foreground">
                    {t('map.reason')}:{' '}
                  </span>
                  {lastDecision.reason}
                </p>
              ) : null}
            </div>
          ) : null}
        </TabsContent>

        <TabsContent value="risk" className="pt-3">
          {detail.risk.available ? (
            <p className="text-sm">
              {t('status.risk.label')}: {props.risk_level} ({props.risk_score})
            </p>
          ) : (
            <div className="rounded-lg border border-dashed bg-slate-50 p-3 text-sm">
              <p className="font-medium">{t('map.riskUnavailable')}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {t('map.riskUnavailableBody')}
              </p>
              {detail.risk.reason ? (
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  {detail.risk.reason}
                </p>
              ) : null}
            </div>
          )}
        </TabsContent>

        <TabsContent value="history" className="pt-3">
          {observations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('map.noObservations')}
            </p>
          ) : (
            <ol className="space-y-2">
              {observations.map((observation) => (
                <li
                  key={observation.id}
                  className="rounded-lg border p-2.5 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {observation.passability ? (
                      <StatusBadge
                        kind="passability"
                        value={observation.passability}
                      />
                    ) : null}
                    <span className="text-xs text-muted-foreground">
                      {observation.kind}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('map.observedAt')}{' '}
                    {formatAbsoluteTime(
                      locale,
                      new Date(observation.observed_at),
                    )}{' '}
                    ({observation.source_mode})
                  </p>
                </li>
              ))}
            </ol>
          )}
        </TabsContent>
      </Tabs>

      {detail.allowed_actions.length > 0 ? (
        <div className="border-t pt-3">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            {t('map.actions')}
          </p>
          <div className="flex flex-wrap gap-2">
            {detail.allowed_actions.includes('assign_inspection') ? (
              <Button
                size="sm"
                variant="outline"
                nativeButton={false}
                render={<Link href="/inspections" />}
              >
                <ClipboardCheck aria-hidden="true" />
                {t('map.actionInspect')}
              </Button>
            ) : null}
            {detail.allowed_actions.includes('report_observation') ? (
              <Button
                size="sm"
                variant="outline"
                render={
                  <Link
                    href={`/field/report?segment=${encodeURIComponent(detail.segment.id)}`}
                  />
                }
              >
                <FilePlus2 aria-hidden="true" />
                {t('map.actionReport')}
              </Button>
            ) : null}
            {detail.allowed_actions.includes('plan_route') ? (
              <Button
                size="sm"
                variant="outline"
                nativeButton={false}
                render={<Link href="/planner" />}
              >
                <Route aria-hidden="true" />
                {t('map.actionPlan')}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
