'use client';

import { LayoutList, Map as MapIcon } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useAuth } from '@/components/auth/auth-provider';
import { useLocale } from '@/components/i18n/locale-provider';
import { EvidenceDrawer } from '@/components/map/evidence-drawer';
import {
  DEFAULT_LAYERS,
  NetworkMap,
  type MapLayerVisibility,
} from '@/components/map/network-map';
import {
  PASSABILITY_COLORS,
  PASSABILITY_ORDER,
} from '@/components/map/passability-style';
import { CommandShell } from '@/components/layout/command-shell';
import { DataList, type DataListColumn } from '@/components/common/data-list';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { StatusBadge } from '@/components/common/status-badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { ApiClientError } from '@/lib/api/client';
import type { Passability, SegmentFeature } from '@/lib/api/contracts';
import {
  useConnectivitySummary,
  useNetworkSegments,
  useSegmentDetail,
} from '@/lib/api/hooks';
import { padBBox, type BBox } from '@/lib/api/network';
import { formatDistance } from '@/lib/i18n/format';

const VIEWPORT_DEBOUNCE_MS = 300;

function roundBBox(bbox: BBox, digits = 3): BBox {
  const f = 10 ** digits;
  return {
    minLon: Math.floor(bbox.minLon * f) / f,
    minLat: Math.floor(bbox.minLat * f) / f,
    maxLon: Math.ceil(bbox.maxLon * f) / f,
    maxLat: Math.ceil(bbox.maxLat * f) / f,
  };
}

function LegendSwatch({ status }: { status: Passability }) {
  const color = PASSABILITY_COLORS[status];
  const dash =
    status === 'restricted'
      ? '6 4'
      : status === 'unknown'
        ? '1.5 4'
        : undefined;
  return (
    <svg aria-hidden="true" viewBox="0 0 40 10" className="h-2.5 w-10 shrink-0">
      {status === 'closed' ? (
        <>
          <line x1={0} y1={5} x2={40} y2={5} stroke={color} strokeWidth={4} />
          {[6, 14, 22, 30].map((x) => (
            <line
              key={x}
              x1={x}
              y1={0}
              x2={x + 6}
              y2={10}
              stroke="#fff"
              strokeWidth={1.5}
            />
          ))}
        </>
      ) : (
        <line
          x1={0}
          y1={5}
          x2={40}
          y2={5}
          stroke={color}
          strokeWidth={3}
          strokeDasharray={dash}
          strokeLinecap={dash ? 'butt' : 'round'}
        />
      )}
    </svg>
  );
}

/** The road a link asked for, as in /map?segment=<id> from an alert. */
function readSegmentParam(): string | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get('segment');
  return value && /^[0-9a-f-]{36}$/i.test(value) ? value : null;
}

/** Accessibility map with evidence drawer and list alternative. */
export function AccessibilityMapScreen() {
  const { locale, t } = useLocale();
  const { workspace } = useAuth();
  const districts = workspace?.districts ?? [];

  const [districtId, setDistrictId] = useState<string | null>(null);
  const [visibleStatuses, setVisibleStatuses] = useState<Set<Passability>>(
    () => new Set(PASSABILITY_ORDER),
  );
  const [layers, setLayers] = useState<MapLayerVisibility>(DEFAULT_LAYERS);
  const [view, setView] = useState<'map' | 'list'>('map');
  const [selectedId, setSelectedId] = useState<string | null>(readSegmentParam);
  const [fitId, setFitId] = useState<string | null>(readSegmentParam);
  const [viewport, setViewport] = useState<BBox | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onViewportChange = useCallback((bbox: BBox) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(
      () => setViewport(roundBBox(padBBox(bbox))),
      VIEWPORT_DEBOUNCE_MS,
    );
  }, []);
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  const segmentsQuery = useNetworkSegments(
    viewport ? { bbox: viewport, districtId, simplifyM: null } : null,
  );
  const summaryDistrict = districtId ?? districts[0]?.id ?? null;
  const summaryQuery = useConnectivitySummary(summaryDistrict);
  const detailQuery = useSegmentDetail(selectedId);

  const allFeatures = useMemo(
    () => segmentsQuery.data?.features ?? [],
    [segmentsQuery.data],
  );
  const features = useMemo(
    () =>
      allFeatures.filter((feature) =>
        visibleStatuses.has(feature.properties.passability),
      ),
    [allFeatures, visibleStatuses],
  );
  const coverage = segmentsQuery.data?.coverage;
  const coverageBBox: BBox | null = coverage?.bbox
    ? {
        minLon: coverage.bbox.min_lon,
        minLat: coverage.bbox.min_lat,
        maxLon: coverage.bbox.max_lon,
        maxLat: coverage.bbox.max_lat,
      }
    : null;
  const facilities = summaryQuery.data?.facility_status ?? [];

  const select = (id: string | null, fit = false) => {
    setSelectedId(id);
    setFitId(fit ? id : null);
  };

  const toggleStatus = (status: Passability) =>
    setVisibleStatuses((current) => {
      const next = new Set(current);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });

  const columns: DataListColumn<SegmentFeature>[] = [
    {
      key: 'name',
      header: t('map.selectedRoad'),
      render: (row) => row.properties.name ?? t('map.unnamedRoad'),
    },
    {
      key: 'class',
      header: t('map.roadClass'),
      render: (row) => row.properties.road_class,
    },
    {
      key: 'length',
      header: t('map.length'),
      numeric: true,
      render: (row) => formatDistance(locale, row.properties.length_m),
    },
    {
      key: 'status',
      header: t('status.passability.label'),
      render: (row) => (
        <StatusBadge kind="passability" value={row.properties.passability} />
      ),
    },
    {
      key: 'freshness',
      header: t('freshness.updated', { relative: '' }).trim(),
      render: (row) => (
        <FreshnessLabel
          asOf={row.properties.state_as_of ?? null}
          showAbsolute={false}
          staleAfterMs={24 * 3_600_000}
        />
      ),
    },
  ];

  const scopeEmpty = segmentsQuery.data && coverage?.segment_count === 0;
  const error = segmentsQuery.error;

  return (
    <CommandShell
      activeHref="/map"
      title={t('map.title')}
      headerExtra={
        <FreshnessLabel
          asOf={segmentsQuery.data?.as_of ?? null}
          showAbsolute={false}
        />
      }
      fill
    >
      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-4 border-b bg-white px-4 py-3 md:px-6">
        <div className="grid gap-1">
          <Label htmlFor="district-filter">{t('map.district')}</Label>
          <NativeSelect
            id="district-filter"
            value={districtId ?? ''}
            onChange={(event) => setDistrictId(event.target.value || null)}
          >
            <NativeSelectOption value="">
              {t('map.allDistricts')}
            </NativeSelectOption>
            {districts.map((district) => (
              <NativeSelectOption key={district.id} value={district.id}>
                {district.name} ({district.state_code})
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>

        <fieldset className="grid gap-1">
          <legend className="text-sm font-medium">
            {t('map.statusFilter')}
          </legend>
          <div className="flex flex-wrap gap-3">
            {PASSABILITY_ORDER.map((status) => (
              <label key={status} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={visibleStatuses.has(status)}
                  onCheckedChange={() => toggleStatus(status)}
                  aria-label={t(`status.passability.${status}`)}
                />
                <LegendSwatch status={status} />
                {t(`status.passability.${status}`)}
                <span className="text-xs text-muted-foreground tabular-nums">
                  (
                  {
                    allFeatures.filter(
                      (f) => f.properties.passability === status,
                    ).length
                  }
                  )
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="grid gap-1">
          <legend className="text-sm font-medium">{t('map.layers')}</legend>
          <div className="flex flex-wrap gap-3">
            {(
              [
                ['basemap', 'map.layerBasemap'],
                ['facilities', 'map.layerFacilities'],
                ['bridges', 'map.layerBridges'],
                ['coverage', 'map.layerCoverage'],
              ] as const
            ).map(([key, labelKey]) => (
              <label key={key} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={layers[key]}
                  onCheckedChange={(checked) =>
                    setLayers((current) => ({
                      ...current,
                      [key]: Boolean(checked),
                    }))
                  }
                />
                {t(labelKey)}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="ms-auto flex items-center gap-2">
          <Button
            size="sm"
            variant={view === 'map' ? 'secondary' : 'outline'}
            aria-pressed={view === 'map'}
            onClick={() => setView('map')}
          >
            <MapIcon aria-hidden="true" />
            {t('map.viewMap')}
          </Button>
          <Button
            size="sm"
            variant={view === 'list' ? 'secondary' : 'outline'}
            aria-pressed={view === 'list'}
            onClick={() => setView('list')}
          >
            <LayoutList aria-hidden="true" />
            {t('map.viewList')}
          </Button>
        </div>
      </div>

      {/* Workspace */}
      <div className="grid min-h-0 flex-1 gap-4 p-4 md:p-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-h-[420px] min-w-0 flex-col gap-3">
          {error ? (
            <ErrorPanel
              requestId={
                error instanceof ApiClientError ? error.requestId : null
              }
              message={
                error instanceof ApiClientError ? error.message : undefined
              }
              onRetry={() => void segmentsQuery.refetch()}
            />
          ) : null}
          {scopeEmpty ? <EmptyState title={t('map.emptyScope')} /> : null}

          <div
            className={
              view === 'map' && !scopeEmpty
                ? 'min-h-[420px] min-w-0 flex-1'
                : 'hidden'
            }
          >
            <NetworkMap
              segments={features}
              facilities={facilities}
              coverageBBox={coverageBBox}
              selectedSegmentId={selectedId}
              onSelectSegment={(id) => select(id)}
              onViewportChange={onViewportChange}
              fitToSegmentId={fitId}
              layers={layers}
              className="h-full"
            />
          </div>

          {view === 'list' && !scopeEmpty ? (
            <DataList
              columns={columns}
              rows={features}
              getRowId={(row) => row.id}
              titleKey="name"
              view="table"
              allowToggle={false}
              caption={t('map.listCaption')}
              emptyMessage={
                segmentsQuery.isLoading ? t('map.loading') : undefined
              }
              renderActions={(row) => (
                <Button
                  size="xs"
                  variant={row.id === selectedId ? 'secondary' : 'outline'}
                  aria-pressed={row.id === selectedId}
                  onClick={() => select(row.id, true)}
                >
                  {t('map.select')}
                </Button>
              )}
            />
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span data-testid="segments-in-view">
                {segmentsQuery.data
                  ? t('map.segmentsInView', {
                      shown: features.length,
                      total: coverage?.segment_count ?? 0,
                    })
                  : t('map.loading')}
              </span>
              {coverage?.truncated ? (
                <span className="text-[#92400E]">
                  {t('map.truncated', { shown: coverage.returned })}
                </span>
              ) : null}
              {coverage ? (
                <span>
                  {t('map.coverage', {
                    count: coverage.segment_count,
                    districts: coverage.district_ids.length,
                  })}
                </span>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {/* The legend is one flex item in the row above, so it needs to
                  wrap internally or it runs off the side at phone widths. */}
              <span
                data-testid="map-legend"
                className="inline-flex flex-wrap items-center gap-x-2 gap-y-1"
              >
                <span className="text-muted-foreground">
                  {t('map.legend')}:
                </span>
                {PASSABILITY_ORDER.map((status) => (
                  <span key={status} className="inline-flex items-center gap-1">
                    <LegendSwatch status={status} />
                    {t(
                      `map.legend${status.charAt(0).toUpperCase()}${status.slice(1)}`,
                    )}
                  </span>
                ))}
              </span>
              {segmentsQuery.data ? (
                <span>{segmentsQuery.data.attribution}</span>
              ) : null}
            </div>
          </div>
        </div>

        <EvidenceDrawer
          segmentId={selectedId}
          detail={detailQuery.data}
          isLoading={detailQuery.isLoading}
          error={detailQuery.error}
          onRetry={() => void detailQuery.refetch()}
          onClose={() => select(null)}
          className="min-h-[420px] xl:sticky xl:top-4 xl:max-h-[calc(100svh-6rem)]"
        />
      </div>
    </CommandShell>
  );
}
