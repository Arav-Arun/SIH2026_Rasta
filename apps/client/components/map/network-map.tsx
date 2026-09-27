'use client';

import type { FeatureCollection, Geometry } from 'geojson';
import maplibregl, {
  type LngLatBoundsLike,
  type Map as MapLibreMap,
  type MapLayerMouseEvent,
} from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import type { BBox } from '@/lib/api/network';
import type { ConnectivityFacility, SegmentFeature } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import {
  FACILITY_STATUS_COLORS,
  HATCH_IMAGE_ID,
  PASSABILITY_COLORS,
  PASSABILITY_DASH,
  PASSABILITY_ORDER,
  SEGMENT_LAYER_IDS,
  buildHatchImage,
  passabilityLayerId,
} from './passability-style';

import 'maplibre-gl/dist/maplibre-gl.css';

/** Interactive OSM raster tiles are permitted with attribution; never bulk-fetched. */
const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>';

export type MapLayerVisibility = {
  basemap: boolean;
  facilities: boolean;
  bridges: boolean;
  coverage: boolean;
};

export const DEFAULT_LAYERS: MapLayerVisibility = {
  basemap: true,
  facilities: true,
  bridges: false,
  coverage: true,
};

type NetworkMapProps = {
  segments: SegmentFeature[];
  facilities?: ConnectivityFacility[];
  /** Extent of everything in scope; used for the initial fit and the coverage outline. */
  coverageBBox?: BBox | null;
  selectedSegmentId?: string | null;
  onSelectSegment?: (segmentId: string | null) => void;
  /** Called after the user finishes moving the map; used to request the viewport. */
  onViewportChange?: (bbox: BBox) => void;
  /** Segment to fit the camera to (set when a list row is chosen). */
  fitToSegmentId?: string | null;
  layers?: MapLayerVisibility;
  interactive?: boolean;
  className?: string;
};

function boundsOf(bbox: BBox): LngLatBoundsLike {
  return [
    [bbox.minLon, bbox.minLat],
    [bbox.maxLon, bbox.maxLat],
  ];
}

function coverageOutline(bbox: BBox): FeatureCollection {
  const { minLon, minLat, maxLon, maxLat } = bbox;
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: {},
        geometry: {
          type: 'LineString',
          coordinates: [
            [minLon, minLat],
            [maxLon, minLat],
            [maxLon, maxLat],
            [minLon, maxLat],
            [minLon, minLat],
          ],
        },
      },
    ],
  };
}

function facilityCollection(
  facilities: ConnectivityFacility[],
): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: facilities
      .filter((facility) => facility.location)
      .map((facility) => ({
        type: 'Feature',
        properties: {
          facility_id: facility.facility_id,
          name: facility.name,
          type: facility.type,
          status: facility.status,
          color: FACILITY_STATUS_COLORS[facility.status],
        },
        geometry: {
          type: 'Point',
          coordinates: [
            facility.location!.longitude,
            facility.location!.latitude,
          ],
        },
      })),
  };
}

function segmentCollection(segments: SegmentFeature[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: segments.map((segment) => ({
      type: 'Feature',
      id: segment.id,
      properties: {
        segment_id: segment.id,
        passability: segment.properties.passability,
        bridge: segment.properties.bridge ? 1 : 0,
        name: segment.properties.name ?? '',
      },
      geometry: segment.geometry as unknown as Geometry,
    })),
  };
}

function addLayers(map: MapLibreMap) {
  map.addSource('osm', {
    type: 'raster',
    tiles: [OSM_TILE_URL],
    tileSize: 256,
    maxzoom: 19,
    attribution: OSM_ATTRIBUTION,
  });
  map.addLayer({
    id: 'osm-basemap',
    type: 'raster',
    source: 'osm',
    paint: { 'raster-opacity': 0.55, 'raster-saturation': -0.6 },
  });

  map.addSource('coverage', {
    type: 'geojson',
    data: coverageOutline({ minLon: 0, minLat: 0, maxLon: 0, maxLat: 0 }),
  });
  map.addLayer({
    id: 'coverage-outline',
    type: 'line',
    source: 'coverage',
    paint: {
      'line-color': '#1D4ED8',
      'line-width': 1.5,
      'line-dasharray': [2, 2],
      'line-opacity': 0.7,
    },
  });

  map.addSource('segments', { type: 'geojson', data: segmentCollection([]) });
  // Casing so thin lines stay legible on the basemap.
  map.addLayer({
    id: 'segments-casing',
    type: 'line',
    source: 'segments',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#FFFFFF',
      'line-width': ['interpolate', ['linear'], ['zoom'], 12, 3, 16, 7],
      'line-opacity': 0.8,
    },
  });
  for (const status of [...PASSABILITY_ORDER].reverse()) {
    const dash = PASSABILITY_DASH[status];
    map.addLayer({
      id: passabilityLayerId(status),
      type: 'line',
      source: 'segments',
      filter: ['==', ['get', 'passability'], status],
      layout: { 'line-cap': dash ? 'butt' : 'round', 'line-join': 'round' },
      paint: {
        'line-color': PASSABILITY_COLORS[status],
        'line-width': [
          'interpolate',
          ['linear'],
          ['zoom'],
          12,
          1.5,
          16,
          status === 'closed' ? 5 : 4,
        ],
        ...(dash ? { 'line-dasharray': dash } : {}),
        ...(status === 'closed' ? { 'line-pattern': HATCH_IMAGE_ID } : {}),
      },
    });
  }
  map.addLayer({
    id: 'segments-bridges',
    type: 'line',
    source: 'segments',
    filter: ['==', ['get', 'bridge'], 1],
    layout: { visibility: 'none' },
    paint: { 'line-color': '#1E3A8A', 'line-width': 8, 'line-opacity': 0.35 },
  });
  map.addLayer({
    id: 'segments-selected',
    type: 'line',
    source: 'segments',
    filter: ['==', ['get', 'segment_id'], ''],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#1D4ED8', 'line-width': 10, 'line-opacity': 0.35 },
  });

  map.addSource('facilities', {
    type: 'geojson',
    data: facilityCollection([]),
  });
  map.addLayer({
    id: 'facilities-points',
    type: 'circle',
    source: 'facilities',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 12, 4, 16, 8],
      'circle-color': ['get', 'color'],
      'circle-stroke-color': '#FFFFFF',
      'circle-stroke-width': 1.5,
    },
  });
  map.addLayer({
    id: 'facilities-labels',
    type: 'symbol',
    source: 'facilities',
    minzoom: 14,
    layout: {
      'text-field': ['get', 'name'],
      'text-size': 11,
      'text-offset': [0, 1.1],
      'text-anchor': 'top',
      'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
    },
    paint: {
      'text-color': '#172033',
      'text-halo-color': '#FFFFFF',
      'text-halo-width': 1.2,
    },
  });
}

/**
 * MapLibre view of the scoped road network. Data arrives as props; the map
 * never fetches on its own and never moves the camera on a data refresh.
 */
export function NetworkMap({
  segments,
  facilities = [],
  coverageBBox = null,
  selectedSegmentId = null,
  onSelectSegment,
  onViewportChange,
  fitToSegmentId = null,
  layers = DEFAULT_LAYERS,
  interactive = true,
  className,
}: NetworkMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const fittedRef = useRef(false);
  const onSelectRef = useRef(onSelectSegment);
  const onViewportRef = useRef(onViewportChange);
  useEffect(() => {
    onSelectRef.current = onSelectSegment;
    onViewportRef.current = onViewportChange;
  }, [onSelectSegment, onViewportChange]);

  // --- create the map once ---------------------------------------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [],
        glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
      },
      center: [91.8875, 25.5725],
      zoom: 13,
      attributionControl: false,
      interactive,
      maxZoom: 19,
      minZoom: 9,
    });
    mapRef.current = map;
    map.addControl(
      new maplibregl.AttributionControl({ compact: false }),
      'bottom-right',
    );
    map.addControl(
      new maplibregl.ScaleControl({ unit: 'metric' }),
      'bottom-left',
    );
    if (interactive)
      map.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        'top-right',
      );

    const emitViewport = () => {
      const bounds = map.getBounds();
      onViewportRef.current?.({
        minLon: bounds.getWest(),
        minLat: bounds.getSouth(),
        maxLon: bounds.getEast(),
        maxLat: bounds.getNorth(),
      });
    };
    const onClick = (event: MapLayerMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, {
        layers: SEGMENT_LAYER_IDS,
      });
      const id = features[0]?.properties?.segment_id as string | undefined;
      onSelectRef.current?.(id ?? null);
    };

    map.on('load', () => {
      const hatch = buildHatchImage();
      map.addImage(HATCH_IMAGE_ID, hatch, { pixelRatio: 1 });
      addLayers(map);
      setReady(true);
      emitViewport();
    });
    map.on('moveend', emitViewport);
    if (interactive) {
      map.on('click', onClick);
      for (const layerId of SEGMENT_LAYER_IDS) {
        map.on('mouseenter', layerId, () => {
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', layerId, () => {
          map.getCanvas().style.cursor = '';
        });
      }
    }

    return () => {
      map.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, [interactive]);

  // MapLibre sizes its canvas once and does not watch its container, so a
  // layout change (a phone rotating, the sidebar collapsing, the window being
  // resized) leaves the canvas at its old width and pushes the page wider than
  // the viewport.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(() => {
      mapRef.current?.resize();
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // --- data --------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (
      map.getSource('segments') as maplibregl.GeoJSONSource | undefined
    )?.setData(segmentCollection(segments));
  }, [segments, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (
      map.getSource('facilities') as maplibregl.GeoJSONSource | undefined
    )?.setData(facilityCollection(facilities));
  }, [facilities, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !coverageBBox) return;
    (
      map.getSource('coverage') as maplibregl.GeoJSONSource | undefined
    )?.setData(coverageOutline(coverageBBox));
    if (!fittedRef.current) {
      fittedRef.current = true;
      map.fitBounds(boundsOf(coverageBBox), { padding: 24, duration: 0 });
    }
  }, [coverageBBox, ready]);

  // --- selection / fit ------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setFilter('segments-selected', [
      '==',
      ['get', 'segment_id'],
      selectedSegmentId ?? '',
    ]);
  }, [selectedSegmentId, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !fitToSegmentId) return;
    const segment = segments.find(
      (candidate) => candidate.id === fitToSegmentId,
    );
    const coordinates = (
      segment?.geometry as { coordinates?: number[][] } | undefined
    )?.coordinates;
    if (!coordinates?.length) return;
    const lons = coordinates.map((point) => point[0]);
    const lats = coordinates.map((point) => point[1]);
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 80, maxZoom: 17, duration: 350 },
    );
  }, [fitToSegmentId, segments, ready]);

  // --- layer visibility -------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const set = (id: string, visible: boolean) =>
      map.getLayer(id) &&
      map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    set('osm-basemap', layers.basemap);
    set('facilities-points', layers.facilities);
    set('facilities-labels', layers.facilities);
    set('segments-bridges', layers.bridges);
    set('coverage-outline', layers.coverage);
  }, [layers, ready]);

  return (
    <div
      ref={containerRef}
      data-testid="network-map"
      data-ready={ready ? 'true' : 'false'}
      data-segment-count={segments.length}
      className={cn(
        'relative h-full min-h-[360px] w-full overflow-hidden rounded-xl border bg-slate-100',
        className,
      )}
      role="application"
      aria-label="Road network map"
    />
  );
}
