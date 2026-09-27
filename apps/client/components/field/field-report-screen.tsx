'use client';

import Link from 'next/link';
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Camera,
  CheckCircle2,
  CircleAlert,
  Crosshair,
  ListChecks,
  LoaderCircle,
  MapPin,
  RotateCcw,
  Trash2,
} from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { useLocale } from '@/components/i18n/locale-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { ConfirmDialog } from '@/components/common/confirm-dialog';
import { Button } from '@/components/ui/button';
import { useSegmentDetail } from '@/lib/api/hooks';
import { formatAbsoluteTime, formatBytes } from '@/lib/i18n/format';
import {
  EMPTY_DRAFT,
  INCIDENT_TYPES,
  type FieldReportDraft,
  type QueuedReport,
  discardReportDraft,
  dropUnqueuedMedia,
  loadMedia,
  loadReportDraft,
  mediaBlob,
  missingForSubmit,
  queueFieldReport,
  saveReportDraft,
  storeEvidence,
} from '@/lib/offline/field-report';
import { getMutation } from '@/lib/offline/outbox';
import { requestSync } from '@/lib/offline/sync-request';
import type { MutationEnvelope } from '@/lib/offline/types';
import {
  DeviceCapabilityError,
  captureDevicePhoto,
  getCurrentDevicePosition,
} from '@/lib/device';
import { cn } from '@/lib/utils';

const CONTROL =
  'w-full rounded-md border border-border bg-background p-2 text-sm disabled:opacity-60';

/** A representative point on a road: the middle vertex of its line. */
function midpointOf(
  geometry: unknown,
): { latitude: number; longitude: number } | null {
  const coordinates = (geometry as { coordinates?: unknown } | null)
    ?.coordinates;
  if (!Array.isArray(coordinates) || coordinates.length === 0) return null;
  const middle = coordinates[Math.floor(coordinates.length / 2)] as unknown;
  if (!Array.isArray(middle) || middle.length < 2) return null;
  const [longitude, latitude] = middle as [unknown, unknown];
  if (typeof latitude !== 'number' || typeof longitude !== 'number')
    return null;
  return { latitude, longitude };
}

function readSegmentParam(): string | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get('segment');
  return value && /^[0-9a-f-]{36}$/i.test(value) ? value : null;
}

/** A field report filed from the browser. */
export function FieldReportScreen() {
  const { locale, t } = useLocale();
  const { workspace } = useAuth();
  const profileId = workspace?.identity.profileId ?? null;

  const [draft, setDraft] = useState<FieldReportDraft>(EMPTY_DRAFT);
  // The latest draft, for handlers that finish after an await (a camera, a GPS
  // fix): building on the value captured when they started would drop whatever
  // was typed while they waited.
  const draftRef = useRef<FieldReportDraft>(EMPTY_DRAFT);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState<'idle' | 'saved' | 'unavailable'>('idle');
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [photo, setPhoto] = useState<{
    url: string;
    size: number;
    sha256: string;
  } | null>(null);
  const [queued, setQueued] = useState<QueuedReport | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [segmentParam] = useState(readSegmentParam);

  // Restore whatever this person left in the form, on this device.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const restored = (await loadReportDraft(profileId)) ?? EMPTY_DRAFT;
      if (cancelled) return;
      draftRef.current = restored;
      setDraft(restored);
      setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [profileId]);

  /** Applies a change and writes the whole draft to this device. */
  const update = useCallback(
    (change: Partial<FieldReportDraft>) => {
      const next = { ...draftRef.current, ...change };
      draftRef.current = next;
      setDraft(next);
      void saveReportDraft(profileId, next).then(
        (ok) => setSaved(ok ? 'saved' : 'unavailable'),
        () => setSaved('unavailable'),
      );
    },
    [profileId],
  );

  // A map link names the road; the pin starts at its middle, labelled as a pin.
  const segment = useSegmentDetail(loaded ? segmentParam : null);
  const segmentData = segment.data;
  useEffect(() => {
    if (!segmentData || draftRef.current.segmentId === segmentData.segment.id)
      return;
    const point = midpointOf(segmentData.segment.geometry);
    const current = draftRef.current;
    update({
      segmentId: segmentData.segment.id,
      segmentLabel: segmentData.segment.properties.name ?? null,
      ...(current.latitude === null && point
        ? {
            latitude: point.latitude,
            longitude: point.longitude,
            locationSource: 'manual_pin' as const,
            accuracyMetres: null,
            fixCapturedAt: null,
          }
        : {}),
    });
  }, [segmentData, update]);

  // The photo preview is rebuilt from the stored bytes, so it survives a reload.
  useEffect(() => {
    let url: string | null = null;
    let cancelled = false;
    void (async () => {
      if (!draft.mediaId) {
        setPhoto(null);
        return;
      }
      const media = await loadMedia(draft.mediaId);
      if (cancelled || !media) return;
      url = URL.createObjectURL(mediaBlob(media));
      setPhoto({ url, size: media.sizeBytes, sha256: media.sha256 });
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [draft.mediaId]);

  async function captureLocation() {
    setLocating(true);
    setLocationError(null);
    try {
      const fix = await getCurrentDevicePosition();
      update({
        latitude: fix.latitude,
        longitude: fix.longitude,
        locationSource: 'device_gps',
        accuracyMetres: fix.accuracyMetres,
        fixCapturedAt: fix.capturedAt,
      });
    } catch (error) {
      setLocationError(
        error instanceof DeviceCapabilityError
          ? error.message
          : t('report.gpsUnknownError'),
      );
    } finally {
      setLocating(false);
    }
  }

  function setManualCoordinate(field: 'latitude' | 'longitude', raw: string) {
    const value = raw.trim() === '' ? null : Number(raw);
    update({
      [field]: value !== null && Number.isFinite(value) ? value : null,
      // Anything typed is a pin, however precise it looks.
      locationSource: 'manual_pin',
      accuracyMetres: null,
      fixCapturedAt: null,
    });
  }

  async function takePhoto() {
    setPhotoError(null);
    try {
      const captured = await captureDevicePhoto();
      const stored = await storeEvidence({
        blob: captured.blob,
        mimeType: captured.blob.type || captured.format,
        capturedAt: captured.capturedAt,
        profileId,
      });
      if (!stored.ok) {
        setPhotoError(t(`report.photoRefused.${stored.reason}`));
        return;
      }
      const previous = draftRef.current.mediaId;
      update({ mediaId: stored.media.mediaId });
      if (previous) await dropUnqueuedMedia(previous);
    } catch (error) {
      if (
        error instanceof DeviceCapabilityError &&
        error.code === 'request_cancelled'
      )
        return;
      setPhotoError(
        error instanceof Error
          ? error.message
          : t('report.photoRefused.capture_failed'),
      );
    }
  }

  async function removePhoto() {
    const previous = draftRef.current.mediaId;
    update({ mediaId: null });
    if (previous) await dropUnqueuedMedia(previous);
  }

  async function submit() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await queueFieldReport({
        profileId,
        draft: draftRef.current,
      });
      setQueued(result);
      draftRef.current = EMPTY_DRAFT;
      setDraft(EMPTY_DRAFT);
      setSaved('idle');
      requestSync();
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : t('report.queueFailed'),
      );
    } finally {
      setSubmitting(false);
    }
  }

  const missing = missingForSubmit(draft);
  const missingText = missing
    .map((item) => t(`report.missing.${item}`))
    .join(', ');

  return (
    <CommandShell
      ownHeading
      activeHref="/field/report"
      title={t('report.title')}
    >
      <div className="flex max-w-2xl flex-col gap-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('report.title')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('report.lede')}
          </p>
        </div>

        {queued ? (
          <QueuedReportStatus
            queued={queued}
            onFileAnother={() => setQueued(null)}
          />
        ) : !loaded ? (
          <p className="text-sm text-muted-foreground">{t('list.loading')}</p>
        ) : (
          <form
            className="flex flex-col gap-5"
            onSubmit={(event) => {
              event.preventDefault();
              if (missing.length === 0 && !submitting) void submit();
            }}
          >
            <Section title={t('report.typeLabel')}>
              <label className="flex flex-col gap-1.5">
                <span className="sr-only">{t('report.typeLabel')}</span>
                <select
                  className={CONTROL}
                  value={draft.type}
                  onChange={(event) =>
                    update({
                      type: event.target.value as FieldReportDraft['type'],
                    })
                  }
                  data-report-type
                >
                  <option value="">{t('report.typePlaceholder')}</option>
                  {INCIDENT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {t(`incidentType.${type}`)}
                    </option>
                  ))}
                </select>
              </label>
            </Section>

            <Section title={t('report.locationHeading')}>
              {draft.segmentId ? (
                <p className="flex items-start gap-2 text-sm">
                  <MapPin
                    className="mt-0.5 size-4 shrink-0 text-muted-foreground"
                    aria-hidden
                  />
                  {t('report.fromMap', {
                    road: draft.segmentLabel ?? t('report.unnamedRoad'),
                  })}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void captureLocation()}
                  disabled={locating}
                  data-use-gps
                >
                  {locating ? (
                    <LoaderCircle className="animate-spin" aria-hidden />
                  ) : (
                    <Crosshair aria-hidden />
                  )}
                  {locating ? t('report.locating') : t('report.useGps')}
                </Button>
                {draft.locationSource ? (
                  <span
                    className="text-xs text-muted-foreground"
                    data-location-source={draft.locationSource}
                  >
                    {draft.locationSource === 'device_gps' &&
                    draft.fixCapturedAt
                      ? t('report.gpsFix', {
                          accuracy: Math.round(draft.accuracyMetres ?? 0),
                          time: formatAbsoluteTime(
                            locale,
                            new Date(draft.fixCapturedAt),
                          ),
                        })
                      : t('report.pinRecorded')}
                  </span>
                ) : null}
              </div>

              {locationError ? (
                <p className="text-sm text-destructive" role="alert">
                  {t('report.gpsFailed', { reason: locationError })}
                </p>
              ) : null}

              <div className="grid gap-3 sm:grid-cols-2">
                <CoordinateInput
                  label={t('report.latitude')}
                  value={draft.latitude}
                  min={-90}
                  max={90}
                  onChange={(raw) => setManualCoordinate('latitude', raw)}
                />
                <CoordinateInput
                  label={t('report.longitude')}
                  value={draft.longitude}
                  min={-180}
                  max={180}
                  onChange={(raw) => setManualCoordinate('longitude', raw)}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {t('report.manualHint')}
              </p>
            </Section>

            <Section title={t('report.photoHeading')}>
              {photo ? (
                <div className="flex flex-wrap items-start gap-3">
                  {/* A local object URL of the stored bytes; nothing is fetched. */}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={photo.url}
                    alt={t('report.photoAlt')}
                    className="h-28 w-40 rounded-md border object-cover"
                  />
                  <div className="flex min-w-0 flex-col gap-2 text-xs text-muted-foreground">
                    <span data-photo-size>
                      {t('report.photoMeta', {
                        size: formatBytes(photo.size),
                        checksum: photo.sha256.slice(0, 12),
                      })}
                    </span>
                    <span>{t('report.photoKept')}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="self-start"
                      onClick={() => void removePhoto()}
                    >
                      <Trash2 aria-hidden />
                      {t('report.removePhoto')}
                    </Button>
                  </div>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  className="self-start"
                  onClick={() => void takePhoto()}
                  data-add-photo
                >
                  <Camera aria-hidden />
                  {t('report.addPhoto')}
                </Button>
              )}
              {photoError ? (
                <p className="text-sm text-destructive" role="alert">
                  {photoError}
                </p>
              ) : null}
            </Section>

            <Section title={t('report.noteLabel')}>
              <NoteInput
                value={draft.note}
                onChange={(note) => update({ note })}
              />
            </Section>

            <div className="flex flex-col gap-3 border-t pt-4">
              <p
                className="text-xs text-muted-foreground"
                aria-live="polite"
                data-draft-saved={saved}
              >
                {saved === 'saved'
                  ? t('report.savedLocally')
                  : saved === 'unavailable'
                    ? t('report.notSaved')
                    : t('report.submitHint')}
              </p>
              {missing.length > 0 ? (
                <p className="text-sm" id="report-missing">
                  {t('report.stillNeeded', { items: missingText })}
                </p>
              ) : null}
              {submitError ? (
                <p className="text-sm text-destructive" role="alert">
                  {submitError}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="submit"
                  disabled={missing.length > 0 || submitting}
                  aria-describedby={
                    missing.length > 0 ? 'report-missing' : undefined
                  }
                  data-queue-report
                >
                  {submitting ? (
                    <LoaderCircle className="animate-spin" aria-hidden />
                  ) : null}
                  {t('report.submit')}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setConfirmDiscard(true)}
                >
                  <RotateCcw aria-hidden />
                  {t('report.discard')}
                </Button>
              </div>
            </div>
          </form>
        )}
      </div>

      <ConfirmDialog
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
        description={t('report.discardConfirm')}
        destructive
        onConfirm={async () => {
          await discardReportDraft(profileId);
          draftRef.current = EMPTY_DRAFT;
          setDraft(EMPTY_DRAFT);
          setSaved('idle');
          setLocationError(null);
          setPhotoError(null);
        }}
      />
    </CommandShell>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-3 rounded-lg border bg-card p-4">
      <legend className="px-1 text-sm font-semibold">{title}</legend>
      {children}
    </fieldset>
  );
}

function CoordinateInput({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  onChange: (raw: string) => void;
}) {
  const id = useId();
  // Keep what the person is typing ("25.", "-") instead of snapping it to a
  // number, but follow a value set from elsewhere (a GPS fix, the map).
  const [text, setText] = useState(value === null ? '' : String(value));
  const [shown, setShown] = useState(value);
  if (shown !== value) {
    setShown(value);
    const parsed = text.trim() === '' ? null : Number(text);
    if (parsed !== value) setText(value === null ? '' : String(value));
  }
  return (
    <label
      htmlFor={id}
      className="flex min-w-0 flex-col gap-1.5 text-xs font-medium"
    >
      {label}
      <input
        id={id}
        className={CONTROL}
        inputMode="decimal"
        type="number"
        step="any"
        min={min}
        max={max}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          onChange(event.target.value);
        }}
      />
    </label>
  );
}

function NoteInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useLocale();
  const id = useId();
  const countId = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="sr-only">
        {t('report.noteLabel')}
      </label>
      <textarea
        id={id}
        rows={4}
        maxLength={4000}
        value={value}
        placeholder={t('report.notePlaceholder')}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={countId}
        className={CONTROL}
        data-report-note
      />
      <span id={countId} className="self-end text-xs text-muted-foreground">
        {t('report.noteCount', { count: value.length })}
      </span>
    </div>
  );
}

type StepState = 'waiting' | 'sending' | 'done' | 'attention';

function stepState(row: MutationEnvelope | null): StepState {
  if (!row) return 'waiting';
  if (row.state === 'accepted') return 'done';
  if (row.state === 'uploading') return 'sending';
  if (row.state === 'conflict' || row.state === 'needs_action')
    return 'attention';
  return 'waiting';
}

/**
 * What has actually happened to the report, read from the outbox rather than
 * assumed.
 */
function QueuedReportStatus({
  queued,
  onFileAnother,
}: {
  queued: QueuedReport;
  onFileAnother: () => void;
}) {
  const { t } = useLocale();
  const ids = [
    queued.createMutationId,
    queued.uploadMutationId,
    queued.completeMutationId,
  ];

  const rows = useQuery({
    queryKey: ['outbox', 'report-status', ...ids],
    queryFn: async () =>
      Promise.all(
        ids.map((id) => (id ? getMutation(id) : Promise.resolve(null))),
      ),
    refetchInterval: (query) => {
      const data = query.state.data as (MutationEnvelope | null)[] | undefined;
      const settled = data?.every(
        (row, index) =>
          ids[index] === null ||
          (row && ['accepted', 'conflict', 'needs_action'].includes(row.state)),
      );
      return settled ? false : 1500;
    },
  });

  const [create, upload, complete] = rows.data ?? [null, null, null];
  const incidentId =
    (create?.result?.incident as { id?: string } | undefined)?.id ?? null;
  const verification =
    (complete?.result?.verification as
      | { status?: string; reason?: string | null }
      | undefined) ?? null;

  const steps = useMemo(
    () =>
      [
        { key: 'report', row: create, present: true },
        {
          key: 'photo',
          row: upload,
          present: queued.uploadMutationId !== null,
        },
        {
          key: 'check',
          row: complete,
          present: queued.completeMutationId !== null,
        },
      ].filter((step) => step.present),
    [
      create,
      upload,
      complete,
      queued.uploadMutationId,
      queued.completeMutationId,
    ],
  );

  return (
    <section
      className="flex flex-col gap-4 rounded-lg border bg-card p-4"
      aria-labelledby="queued-report-title"
      data-queued-report
    >
      <div className="flex items-start gap-3">
        <CheckCircle2
          className="mt-0.5 size-5 shrink-0 text-emerald-700"
          aria-hidden
        />
        <div>
          <h2 id="queued-report-title" className="text-base font-semibold">
            {t('report.queuedTitle')}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('report.queuedBody')}
          </p>
        </div>
      </div>

      <ol className="flex flex-col gap-2" aria-live="polite">
        {steps.map((step) => {
          const state = stepState(step.row);
          let detail = t(`report.state.${state}`);
          if (step.key === 'report' && state === 'done' && incidentId) {
            detail = t('report.receivedAs', { id: incidentId.slice(0, 8) });
          }
          if (
            step.key === 'check' &&
            state === 'done' &&
            verification?.status
          ) {
            detail =
              verification.status === 'verified' ||
              verification.status === 'already_verified'
                ? t('report.check.verified')
                : t('report.check.notVerified', {
                    reason: verification.reason ?? verification.status,
                  });
          }
          if (state === 'attention' && step.row?.lastError)
            detail = step.row.lastError;
          return (
            <li
              key={step.key}
              className="flex items-start justify-between gap-3 rounded-md border p-3 text-sm"
              data-report-step={step.key}
              data-step-state={state}
            >
              <span className="font-medium">
                {t(`report.step.${step.key}`)}
              </span>
              <span
                className={cn(
                  'flex items-center gap-1.5 text-end text-xs',
                  state === 'attention'
                    ? 'text-destructive'
                    : 'text-muted-foreground',
                )}
              >
                {state === 'attention' ? (
                  <CircleAlert className="size-3.5" aria-hidden />
                ) : null}
                {detail}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={onFileAnother}>
          {t('report.fileAnother')}
        </Button>
        <Button
          type="button"
          variant="outline"
          nativeButton={false}
          render={<Link href="/sync" />}
        >
          <ListChecks aria-hidden />
          {t('report.openQueue')}
        </Button>
      </div>
    </section>
  );
}
