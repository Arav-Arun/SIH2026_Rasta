/** Street-scale geometry for choosing which road a report is about. */

type Point = { latitude: number; longitude: number };

const METRES_PER_DEGREE_LAT = 110_574;
const METRES_PER_DEGREE_LON_AT_EQUATOR = 111_320;

/** The line's vertices as [lon, lat] pairs, from a LineString or MultiLineString. */
function linesOf(geometry: unknown): number[][][] {
  const value = geometry as { type?: string; coordinates?: unknown } | null;
  if (!value || !Array.isArray(value.coordinates)) return [];
  if (value.type === 'LineString') return [value.coordinates as number[][]];
  if (value.type === 'MultiLineString')
    return value.coordinates as number[][][];
  return [];
}

/** Metres from a point to the nearest part of a line; Infinity for no geometry. */
export function distanceToLineMeters(point: Point, geometry: unknown): number {
  const cosLat = Math.cos((point.latitude * Math.PI) / 180);
  const project = ([lon, lat]: number[]) => [
    (lon - point.longitude) * METRES_PER_DEGREE_LON_AT_EQUATOR * cosLat,
    (lat - point.latitude) * METRES_PER_DEGREE_LAT,
  ];
  let best = Number.POSITIVE_INFINITY;
  for (const line of linesOf(geometry)) {
    const projected = line.filter((pair) => pair.length >= 2).map(project);
    for (let index = 0; index < projected.length; index += 1) {
      const [ax, ay] = projected[index];
      const [bx, by] = projected[index + 1] ?? projected[index];
      const dx = bx - ax;
      const dy = by - ay;
      const lengthSquared = dx * dx + dy * dy;
      // Where along a→b the point (the origin) projects, clamped to the segment.
      const along =
        lengthSquared === 0
          ? 0
          : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared));
      best = Math.min(best, Math.hypot(ax + along * dx, ay + along * dy));
    }
  }
  return best;
}

/** The `count` features nearest the point, nearest first, with their distance. */
export function nearestFeatures<T extends { geometry: unknown }>(
  features: T[],
  point: Point,
  count: number,
): { feature: T; metres: number }[] {
  return features
    .map((feature) => ({
      feature,
      metres: distanceToLineMeters(point, feature.geometry),
    }))
    .filter((entry) => Number.isFinite(entry.metres))
    .sort((a, b) => a.metres - b.metres)
    .slice(0, count);
}
