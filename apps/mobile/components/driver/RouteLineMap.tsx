import { useMemo, useState } from 'react';
import {
  Image,
  PixelRatio,
  StyleSheet,
  Text,
  View,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Circle, Polyline } from 'react-native-svg';

import { Theme } from '../../constants/theme';
import { useT } from '../../contexts/LocaleContext';
import { fitLines } from '../../services/mapTiles';
import { linesFromGeometry } from '../../services/routePack';

/**
 * The approved route drawn over OpenStreetMap tiles. The line comes from the
 * route pack, so it is drawn even when the tiles cannot load.
 */

const HEIGHT = 200;
const FRAME = { height: HEIGHT, padding: 24 };
const TILE_URL = 'https://tile.openstreetmap.org';
/** OpenStreetMap's tile policy asks every app to identify itself. */
const TILE_HEADERS = {
  'User-Agent': 'RASTA/1.0 (+https://github.com/Arav-Arun/SIH2026_Rasta)',
};
/** Dense screens get half-size tiles from one zoom deeper, so the map stays sharp. */
const SHARP = PixelRatio.get() >= 2;

export function RouteLineMap({ geometry }: { geometry: unknown }) {
  const t = useT();
  const lines = useMemo(() => linesFromGeometry(geometry), [geometry]);
  const [width, setWidth] = useState(0);
  const [tilesFailed, setTilesFailed] = useState(false);
  const view = useMemo(
    () =>
      width > 0 && lines.length > 0
        ? fitLines(lines, { ...FRAME, width }, SHARP)
        : null,
    [lines, width],
  );
  if (lines.length === 0) return null;

  const onLayout = (event: LayoutChangeEvent) =>
    setWidth(Math.round(event.nativeEvent.layout.width));
  const first = view?.lines[0]?.[0];
  const lastLine = view?.lines[view.lines.length - 1];
  const last = lastLine?.[lastLine.length - 1];

  return (
    <View
      style={styles.frame}
      onLayout={onLayout}
      accessible
      accessibilityLabel={t('mobile.map.title')}
    >
      {view?.tiles.map((tile) => (
        <Image
          key={tile.key}
          source={{
            uri: `${TILE_URL}/${tile.zoom}/${tile.x}/${tile.y}.png`,
            headers: TILE_HEADERS,
          }}
          style={[
            styles.tile,
            {
              left: tile.left,
              top: tile.top,
              width: view.tileSize,
              height: view.tileSize,
            },
          ]}
          onError={() => setTilesFailed(true)}
        />
      ))}
      {view ? (
        <Svg width={width} height={HEIGHT} style={StyleSheet.absoluteFill}>
          {view.lines.map((line, index) => (
            <Polyline
              key={index}
              points={line.map((p) => `${p.x},${p.y}`).join(' ')}
              fill="none"
              stroke={Theme.colors.brandLight}
              strokeWidth={5}
              strokeOpacity={0.9}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
          {first ? (
            <Circle
              cx={first.x}
              cy={first.y}
              r={6}
              fill="#fff"
              stroke={Theme.colors.brand}
              strokeWidth={3}
            />
          ) : null}
          {last ? (
            <Circle
              cx={last.x}
              cy={last.y}
              r={6}
              fill="#fff"
              stroke={Theme.colors.passable}
              strokeWidth={3}
            />
          ) : null}
        </Svg>
      ) : null}
      {tilesFailed ? (
        <Text style={styles.offline}>{t('mobile.map.offline')}</Text>
      ) : null}
      <Text style={styles.attribution}>© OpenStreetMap contributors</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    height: HEIGHT,
    borderRadius: Theme.radius.md,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: Theme.colors.border,
    backgroundColor: Theme.colors.surfaceElevated,
  },
  tile: { position: 'absolute' },
  offline: {
    position: 'absolute',
    left: Theme.spacing.sm,
    right: Theme.spacing.sm,
    top: Theme.spacing.sm,
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
    lineHeight: 16,
  },
  attribution: {
    position: 'absolute',
    right: 0,
    bottom: 0,
    paddingHorizontal: 4,
    fontSize: 10,
    color: Theme.colors.textMuted,
    backgroundColor: 'rgba(255,255,255,0.8)',
  },
});
