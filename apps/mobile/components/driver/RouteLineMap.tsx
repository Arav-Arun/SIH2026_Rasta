import React, { useMemo } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';

import { Theme } from '../../constants/theme';
import { useT } from '../../contexts/LocaleContext';
import { API_BASE_URL } from '../../services/rastaApi';
import { linesFromGeometry, type RouteLine } from '../../services/routePack';

/** The approved route drawn as a line, from the geometry the server sent. */

let NativeWebView: React.ComponentType<Record<string, unknown>> | null = null;
if (Platform.OS !== 'web') {
  try {
    // Loaded lazily so the web build does not pull in a native-only module.
    // oxlint-disable-next-line typescript/no-require-imports
    NativeWebView = require('react-native-webview').WebView;
  } catch {
    NativeWebView = null;
  }
}

const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTRIBUTION = '© OpenStreetMap contributors';

/** The page's own address. */
const PAGE_BASE_URL = `${API_BASE_URL.replace(/\/+$/, '')}/`;

/** `offlineNote` is shown in place of the map when Leaflet cannot be fetched. */
function html(lines: RouteLine[], offlineNote: string): string {
  // GeoJSON is [lng, lat]; Leaflet wants [lat, lng].
  const latLngs = lines.map((line) => line.map(([lng, lat]) => [lat, lng]));
  return `<!doctype html>
<html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>html,body,#map{margin:0;height:100%;background:${Theme.colors.surfaceElevated}}</style>
</head><body><div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  if (typeof L === 'undefined') {
    document.body.innerHTML = '<p style="font:14px/1.4 sans-serif;color:${Theme.colors.textMuted};padding:16px;margin:0">' + ${JSON.stringify(offlineNote)} + '</p>';
    throw new Error('leaflet unavailable');
  }
  var lines = ${JSON.stringify(latLngs)};
  var map = L.map('map', { zoomControl: false, attributionControl: true });
  L.tileLayer('${OSM_TILE_URL}', { maxZoom: 18, attribution: '${OSM_ATTRIBUTION}' }).addTo(map);
  // The view is set before anything is drawn: a line added to a map that has
  // no view yet is never rendered.
  map.fitBounds(L.latLngBounds([].concat.apply([], lines)), { padding: [24, 24] });
  lines.forEach(function (line) {
    L.polyline(line, { color: '${Theme.colors.brandLight}', weight: 5, opacity: 0.9 }).addTo(map);
  });
  var first = lines[0] && lines[0][0];
  var lastLine = lines[lines.length - 1];
  var last = lastLine && lastLine[lastLine.length - 1];
  if (first) L.circleMarker(first, { radius: 6, color: '${Theme.colors.brand}', fillColor: '#fff', fillOpacity: 1, weight: 3 }).addTo(map);
  if (last) L.circleMarker(last, { radius: 6, color: '${Theme.colors.passable}', fillColor: '#fff', fillOpacity: 1, weight: 3 }).addTo(map);
</script>
</body></html>`;
}

export function RouteLineMap({ geometry }: { geometry: unknown }) {
  const t = useT();
  const lines = useMemo(() => linesFromGeometry(geometry), [geometry]);
  if (lines.length === 0) return null;

  const source = html(lines, t('mobile.map.offline'));

  if (Platform.OS === 'web') {
    return (
      <View style={styles.frame}>
        <iframe
          title={t('mobile.map.title')}
          srcDoc={source}
          style={{ border: 'none', width: '100%', height: '100%' }}
        />
      </View>
    );
  }

  if (!NativeWebView) {
    return (
      <View style={[styles.frame, styles.fallback]}>
        <Text style={styles.fallbackText}>{t('mobile.map.unavailable')}</Text>
      </View>
    );
  }

  const WebView = NativeWebView;
  return (
    <View style={styles.frame}>
      <WebView
        originWhitelist={['*']}
        source={{ html: source, baseUrl: PAGE_BASE_URL }}
        style={styles.web}
        scrollEnabled={false}
        // Nothing in this page needs to reach anything but the tile server.
        javaScriptEnabled
        domStorageEnabled={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: 200,
    borderRadius: Theme.radius.md,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: Theme.colors.border,
    backgroundColor: Theme.colors.surfaceElevated,
  },
  web: { flex: 1, backgroundColor: 'transparent' },
  fallback: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: Theme.spacing.md,
  },
  fallbackText: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
    textAlign: 'center',
    lineHeight: 18,
  },
});
