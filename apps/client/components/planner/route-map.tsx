'use client';

import type { FeatureCollection, LineString, MultiLineString } from 'geojson';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { useEffect, useRef, useState } from 'react';

import type { RouteAlternative } from '@/lib/api/contracts';
import { cn } from '@/lib/utils';

import 'maplibre-gl/dist/maplibre-gl.css';

const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>';

const SOURCE_ID = 'route-alternatives';
const CASING_LAYER = 'route-casing';
const LINE_LAYER = 'route-line';

/** Rank-coloured so a card and its line are the same thing. */
const RANK_COLORS = ['#1D4ED8', '#7C3AED', '#0F766E'];
const UNSELECTED_OPACITY = 0.35;

type RouteMapProps = {
  alternatives: RouteAlternative[];
  selectedId: string | null;
  onSelect?: (alternativeId: string) => void;
  className?: string;
};

function toCollection(
  alternatives: RouteAlternative[],
  selectedId: string | null,
): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: alternatives
      // A route with no geometry is not drawn as a straight line between its
      // endpoints. A line the vehicle cannot drive is worse than no line.
      .filter((item) => item.geometry != null)
      .map((item) => ({
        type: 'Feature' as const,
        id: item.rank,
        geometry: item.geometry as unknown as LineString | MultiLineString,
        properties: {
          alternative_id: item.id,
          rank: item.rank,
          category: item.category,
          color: RANK_COLORS[(item.rank - 1) % RANK_COLORS.length],
          selected: item.id === selectedId,
        },
      })),
  };
}

function boundsOf(
  collection: FeatureCollection,
): maplibregl.LngLatBounds | null {
  const bounds = new maplibregl.LngLatBounds();
  let seen = false;
  for (const feature of collection.features) {
    const geometry = feature.geometry as LineString | MultiLineString;
    const lines =
      geometry.type === 'LineString'
        ? [geometry.coordinates]
        : (geometry.coordinates as number[][][]);
    for (const line of lines) {
      for (const point of line) {
        bounds.extend([point[0], point[1]]);
        seen = true;
      }
    }
  }
  return seen ? bounds : null;
}

/** Shows the proposed routes over the OSM basemap and nothing else. */
export function RouteMap({
  alternatives,
  selectedId,
  onSelect,
  className,
}: RouteMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const onSelectRef = useRef(onSelect);
  const [ready, setReady] = useState(false);
  const fittedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: [OSM_TILE_URL],
            tileSize: 256,
            attribution: OSM_ATTRIBUTION,
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
      center: [91.8875, 25.5725],
      zoom: 12,
      attributionControl: false,
      maxZoom: 19,
      minZoom: 8,
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
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      'top-right',
    );

    map.on('load', () => {
      map.addSource(SOURCE_ID, {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      map.addLayer({
        id: CASING_LAYER,
        type: 'line',
        source: SOURCE_ID,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#FFFFFF',
          'line-width': ['case', ['get', 'selected'], 9, 6],
          'line-opacity': [
            'case',
            ['get', 'selected'],
            0.95,
            UNSELECTED_OPACITY,
          ],
        },
      });
      map.addLayer({
        id: LINE_LAYER,
        type: 'line',
        source: SOURCE_ID,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['get', 'selected'], 5, 3],
          'line-opacity': ['case', ['get', 'selected'], 1, UNSELECTED_OPACITY],
        },
      });
      setReady(true);
    });

    map.on('click', LINE_LAYER, (event) => {
      const id = event.features?.[0]?.properties?.alternative_id as
        | string
        | undefined;
      if (id) onSelectRef.current?.(id);
    });
    map.on('mouseenter', LINE_LAYER, () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', LINE_LAYER, () => {
      map.getCanvas().style.cursor = '';
    });

    // MapLibre does not observe its container, so a pane that changes width
    // leaves the canvas at its old size until something forces a resize.
    const observer = new ResizeObserver(() => mapRef.current?.resize());
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const collection = toCollection(alternatives, selectedId);
    (map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined)?.setData(
      collection,
    );

    // Fit once per distinct set of routes: refitting on every selection would
    // fight the dispatcher's own panning.
    const key = alternatives.map((item) => item.id).join('|');
    if (key && key !== fittedKeyRef.current) {
      const bounds = boundsOf(collection);
      if (bounds) {
        map.fitBounds(bounds, { padding: 56, duration: 400, maxZoom: 16 });
        fittedKeyRef.current = key;
      }
    }
    if (!key) fittedKeyRef.current = null;
  }, [alternatives, selectedId, ready]);

  const drawn = alternatives.filter((item) => item.geometry != null).length;

  return (
    <div
      ref={containerRef}
      data-testid="route-map"
      data-ready={ready ? 'true' : 'false'}
      data-route-count={drawn}
      className={cn(
        'h-full min-h-[22rem] w-full rounded-lg border border-border',
        className,
      )}
    />
  );
}

export { RANK_COLORS };
