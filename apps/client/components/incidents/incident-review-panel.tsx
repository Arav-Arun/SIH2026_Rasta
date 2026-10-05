'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  FileCheck2,
  MapPin,
  Undo2,
  X,
} from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { useT } from '@/components/i18n/locale-provider';
import { useReviewIncident, useSegmentsNearPoint } from '@/lib/api/hooks';
import type {
  IncidentReviewDecision,
  IncidentReviewResponse,
  IncidentSummary,
  Passability,
} from '@/lib/api/contracts';
import { nearestFeatures } from '@/lib/geo';
import { formatBytes } from '@/lib/i18n/format';
import { newUuid } from '@/lib/ids';
import { cn } from '@/lib/utils';

import { EvidenceThumbnail } from './evidence-thumbnail';

/** Decisions a dispatcher can record. */
const DECISIONS: {
  value: IncidentReviewDecision;
  labelKey: string;
  changesRoadState: boolean;
  destructive?: boolean;
}[] = [
  {
    value: 'confirm_closure',
    labelKey: 'incidents.decision.confirmClosure',
    changesRoadState: true,
  },
  {
    value: 'confirm_restriction',
    labelKey: 'incidents.decision.confirmRestriction',
    changesRoadState: true,
  },
  {
    value: 'reopen',
    labelKey: 'incidents.decision.reopen',
    changesRoadState: true,
  },
  {
    value: 'request_clarification',
    labelKey: 'incidents.decision.requestClarification',
    changesRoadState: false,
  },
  {
    value: 'reject',
    labelKey: 'incidents.decision.reject',
    changesRoadState: false,
    destructive: true,
  },
];

type IncidentReviewPanelProps = {
  incident: IncidentSummary;
  detailPending: boolean;
  onDecided: () => void;
};

export function IncidentReviewPanel({
  incident,
  detailPending,
  onDecided,
}: IncidentReviewPanelProps) {
  const t = useT();
  const review = useReviewIncident(incident.id);

  const [decision, setDecision] = useState<IncidentReviewDecision | null>(null);
  const [reason, setReason] = useState('');
  const [segmentIds, setSegmentIds] = useState<string[]>([]);
  const [result, setResult] = useState<IncidentReviewResponse | null>(null);

  const attachments = incident.attachments ?? [];
  const linkedSegments = incident.segments ?? [];
  const decided =
    incident.status !== 'submitted' && incident.status !== 'under_review';

  const activeDecision = useMemo(
    () => DECISIONS.find((item) => item.value === decision) ?? null,
    [decision],
  );

  const needsSegments = activeDecision?.changesRoadState ?? false;
  const canSubmit =
    decision !== null &&
    reason.trim().length > 0 &&
    (!needsSegments || segmentIds.length > 0) &&
    !review.isPending;

  function toggleSegment(segmentId: string) {
    setSegmentIds((current) =>
      current.includes(segmentId)
        ? current.filter((id) => id !== segmentId)
        : [...current, segmentId],
    );
  }

  function submit() {
    if (!canSubmit || decision === null) return;

    review.mutate(
      {
        body: {
          decision,
          reason: reason.trim(),
          expected_version: incident.version,
          affected_segment_ids: needsSegments ? segmentIds : [],
        },
        // Minted per attempt: a retry of this same click replays server-side
        // instead of recording the decision twice.
        idempotencyKey: newUuid(),
      },
      { onSuccess: setResult },
    );
  }

  if (result) {
    return <ReviewOutcome result={result} onDone={onDecided} />;
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">
            {t(`incidentType.${incident.type}`)}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t('incidents.reportedLabel')}{' '}
            <FreshnessLabel asOf={incident.reported_at} />
          </p>
        </div>
        <StatusBadge kind="review" value={incident.status} />
      </header>

      {incident.note ? (
        <p className="text-sm">{incident.note}</p>
      ) : (
        <p className="text-sm italic text-muted-foreground">
          {t('incidents.noNote')}
        </p>
      )}

      <PositionRow incident={incident} />

      <EvidenceList attachments={attachments} />

      {detailPending && (
        <p className="text-xs text-muted-foreground">
          {t('incidents.loadingDetail')}
        </p>
      )}

      {decided ? (
        <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">
          {t('incidents.alreadyDecided')}
        </p>
      ) : (
        <>
          <fieldset className="flex flex-col gap-2">
            <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('incidents.decisionLabel')}
            </legend>
            <div className="flex flex-wrap gap-2">
              {DECISIONS.map((option) => {
                const active = decision === option.value;
                return (
                  <label
                    key={option.value}
                    className={cn(
                      'cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium transition-colors',
                      active
                        ? option.destructive
                          ? 'border-destructive bg-destructive/10 text-destructive'
                          : 'border-primary bg-primary/10 text-primary'
                        : 'border-border hover:bg-accent',
                    )}
                  >
                    <input
                      type="radio"
                      name="incident-decision"
                      value={option.value}
                      checked={active}
                      onChange={() => setDecision(option.value)}
                      className="sr-only"
                    />
                    {t(option.labelKey)}
                  </label>
                );
              })}
            </div>
          </fieldset>

          {needsSegments && (
            <SegmentChoices
              incident={incident}
              linkedSegments={linkedSegments}
              selected={segmentIds}
              onToggle={toggleSegment}
            />
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('incidents.reasonLabel')}
            </span>
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={3}
              className="rounded-md border border-border bg-background p-2 text-sm"
              placeholder={t('incidents.reasonPlaceholder')}
            />
            <span className="text-xs text-muted-foreground">
              {t('incidents.reasonHelp')}
            </span>
          </label>

          {review.isError && (
            <ErrorPanel
              title={t('incidents.error.review')}
              message={
                review.error instanceof Error ? review.error.message : undefined
              }
              onRetry={submit}
            />
          )}

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
            >
              {review.isPending
                ? t('incidents.submitting')
                : t('incidents.submitDecision')}
            </button>
            {needsSegments && segmentIds.length === 0 && (
              <span className="text-xs text-muted-foreground">
                {t('incidents.needSegment')}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function PositionRow({ incident }: { incident: IncidentSummary }) {
  const t = useT();

  if (!incident.location) {
    return (
      <p className="inline-flex items-center gap-1.5 rounded-md bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
        {t('incidents.noPositionWarning')}
      </p>
    );
  }

  return (
    <p className="inline-flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <MapPin className="size-3.5" aria-hidden />
        <span className="font-mono">
          {incident.location.latitude.toFixed(5)},{' '}
          {incident.location.longitude.toFixed(5)}
        </span>
      </span>
      {incident.accuracy_m !== null && incident.accuracy_m !== undefined && (
        <span>±{Math.round(incident.accuracy_m)} m</span>
      )}
      {incident.location_source && <span>{incident.location_source}</span>}
    </p>
  );
}

function EvidenceList({
  attachments,
}: {
  attachments: NonNullable<IncidentSummary['attachments']>;
}) {
  const t = useT();
  const [openUrl, setOpenUrl] = useState<string | null>(null);

  if (attachments.length === 0) {
    return (
      <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        {t('incidents.noEvidenceWarning')}
      </p>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('incidents.evidenceLabel')}
      </h3>

      <ul className="grid grid-cols-3 gap-2">
        {attachments.map((attachment) => (
          <li key={attachment.id} className="flex flex-col gap-1">
            <EvidenceThumbnail attachment={attachment} onOpen={setOpenUrl} />
            <div className="flex items-center justify-between gap-1">
              <StatusBadge kind="review" value={attachment.upload_status} />
              {attachment.size_bytes ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {formatBytes(attachment.size_bytes)}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ul>

      <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <FileCheck2 className="size-3.5 shrink-0" aria-hidden />
        {t('incidents.evidenceHelp')}
      </p>

      {openUrl && (
        <EvidenceLightbox url={openUrl} onClose={() => setOpenUrl(null)} />
      )}
    </section>
  );
}

/** Full-size view of one photo, for reading a road surface properly. */
function EvidenceLightbox({
  url,
  onClose,
}: {
  url: string;
  onClose: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || dialog.open) return;
    dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      aria-label={t('incidents.evidenceAlt')}
      onClose={onClose}
      className="m-auto max-h-[90vh] max-w-[90vw] rounded-lg bg-transparent p-0 backdrop:bg-black/70"
    >
      <div className="relative">
        <button
          type="button"
          onClick={() => ref.current?.close()}
          aria-label={t('incidents.evidenceClose')}
          className="absolute end-2 top-2 rounded-md bg-background/90 p-2"
        >
          <X className="size-4" aria-hidden />
        </button>
        {/* A low-resolution source is scaled up so it can be looked at; the
            browser's soft upscaling makes the lack of detail obvious rather
            than implying the photo is sharper than it is.
            next/image is not usable here: the signed URL is short-lived and
            the object is private, so the optimiser cannot fetch it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={t('incidents.evidenceAlt')}
          className="max-h-[90vh] max-w-[90vw] rounded-lg bg-black object-contain"
          style={{
            minWidth: 'min(320px, 90vw)',
            minHeight: 'min(240px, 60vh)',
          }}
        />
      </div>
    </dialog>
  );
}

/** How many roads to offer: the report is on one of the nearest few. */
const NEARBY_CHOICES = 8;

function SegmentChoices({
  incident,
  linkedSegments,
  selected,
  onToggle,
}: {
  incident: IncidentSummary;
  linkedSegments: NonNullable<IncidentSummary['segments']>;
  selected: string[];
  onToggle: (segmentId: string) => void;
}) {
  const t = useT();
  const nearby = useSegmentsNearPoint(
    linkedSegments.length === 0 ? incident.location : null,
    incident.district_id,
  );

  // Nearest first.
  const options: {
    id: string;
    name?: string | null;
    roadClass?: string | null;
    metres?: number;
  }[] =
    linkedSegments.length > 0
      ? linkedSegments.map((segment) => ({
          id: segment.segment_id,
          name: segment.name,
          roadClass: segment.road_class,
        }))
      : incident.location
        ? nearestFeatures(
            nearby.data?.features ?? [],
            incident.location,
            NEARBY_CHOICES,
          ).map(({ feature, metres }) => ({
            id: feature.properties.segment_id,
            name: feature.properties.name,
            roadClass: feature.properties.road_class,
            metres,
          }))
        : [];

  if (!incident.location && linkedSegments.length === 0) {
    return (
      <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        {t('incidents.noSegments')}
      </p>
    );
  }

  if (nearby.isPending && linkedSegments.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {t('incidents.loadingSegments')}
      </p>
    );
  }

  if (options.length === 0) {
    return (
      <p className="rounded-md bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        {t('incidents.noSegmentsNearby')}
      </p>
    );
  }

  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('incidents.segmentsLabel')}
      </legend>
      <ul className="flex max-h-56 flex-col gap-1.5 overflow-y-auto pe-1">
        {options.map((option) => (
          <li key={option.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border p-2 text-sm hover:bg-accent">
              <input
                type="checkbox"
                checked={selected.includes(option.id)}
                onChange={() => onToggle(option.id)}
                data-segment-id={option.id}
                className="size-4"
              />
              <span className="truncate">
                {option.name ?? t('incidents.unnamedSegment')}
              </span>
              {(option.roadClass || option.metres !== undefined) && (
                <span className="ms-auto shrink-0 text-xs text-muted-foreground">
                  {[
                    option.roadClass,
                    option.metres !== undefined
                      ? t('incidents.segmentDistance', {
                          metres: Math.round(option.metres),
                        })
                      : null,
                  ]
                    .filter(Boolean)
                    .join(', ')}
                </span>
              )}
            </label>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        {linkedSegments.length > 0
          ? t('incidents.segmentsHelp')
          : t('incidents.segmentsNearbyHelp')}
      </p>
    </fieldset>
  );
}

const PASSABILITY_VALUES: readonly Passability[] = [
  'open',
  'restricted',
  'closed',
  'unknown',
];

function asPassability(value: string): Passability {
  return (PASSABILITY_VALUES as readonly string[]).includes(value)
    ? (value as Passability)
    : 'unknown';
}

/** What the decision actually did to the network, stated segment by segment. */
function ReviewOutcome({
  result,
  onDone,
}: {
  result: IncidentReviewResponse;
  onDone: () => void;
}) {
  const t = useT();
  const changed = result.segment_changes.filter((change) => change.changed);
  const unchanged = result.segment_changes.filter((change) => !change.changed);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-emerald-600/40 bg-emerald-50 p-4 dark:bg-emerald-950/30">
      <h2 className="inline-flex items-center gap-2 text-base font-semibold">
        <Check className="size-4" aria-hidden />
        {t('incidents.outcome.title')}
      </h2>

      {result.replayed && (
        <p className="text-xs text-muted-foreground">
          {t('incidents.outcome.replayed')}
        </p>
      )}

      {changed.length > 0 ? (
        <ul className="flex flex-col gap-1.5 text-sm">
          {changed.map((change) => (
            <li key={change.segment_id} className="flex items-center gap-2">
              <StatusBadge
                kind="passability"
                value={asPassability(change.previous_passability)}
              />
              <span aria-hidden>→</span>
              <StatusBadge
                kind="passability"
                value={asPassability(change.passability)}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm">{t('incidents.outcome.noChange')}</p>
      )}

      {unchanged.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
          {unchanged.map((change) => (
            <li key={change.segment_id}>
              {t('incidents.outcome.skipped', { outcome: change.outcome })}
            </li>
          ))}
        </ul>
      )}

      {result.network_version && (
        <p className="font-mono text-xs text-muted-foreground">
          {t('incidents.outcome.networkVersion')}: {result.network_version}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        {t('incidents.outcome.audit', {
          audit: String(result.audit_event_ids.length),
          outbox: String(result.outbox_event_ids.length),
        })}
      </p>

      <button
        type="button"
        onClick={onDone}
        className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium"
      >
        <Undo2 className="size-3.5" aria-hidden />
        {t('incidents.outcome.backToQueue')}
      </button>
    </div>
  );
}
