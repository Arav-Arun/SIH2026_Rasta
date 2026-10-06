import { describe, expect, it } from 'vitest';

import { distanceToLineMeters, nearestFeatures } from './geo';

const POINT = { latitude: 25.5704, longitude: 91.8919 };
// About 111 m per 0.001° of latitude here.
const lineThrough = {
  type: 'LineString',
  coordinates: [
    [91.8914, 25.5704],
    [91.8924, 25.5704],
  ],
};
const lineNorth = {
  type: 'LineString',
  coordinates: [
    [91.8914, 25.5714],
    [91.8924, 25.5714],
  ],
};

describe('street-scale distances', () => {
  it('is zero on the line and about 111 m for 0.001° of latitude', () => {
    expect(distanceToLineMeters(POINT, lineThrough)).toBeLessThan(0.5);
    expect(distanceToLineMeters(POINT, lineNorth)).toBeGreaterThan(105);
    expect(distanceToLineMeters(POINT, lineNorth)).toBeLessThan(115);
  });

  it('measures to the nearest end when the point is beyond the segment', () => {
    const east = {
      type: 'LineString',
      coordinates: [
        [91.8929, 25.5704],
        [91.8939, 25.5704],
      ],
    };
    // 0.001° of longitude at 25.57° N is about 100 m.
    expect(distanceToLineMeters(POINT, east)).toBeGreaterThan(95);
    expect(distanceToLineMeters(POINT, east)).toBeLessThan(105);
  });

  it('reads multi-part lines and ignores anything without geometry', () => {
    const multi = {
      type: 'MultiLineString',
      coordinates: [lineNorth.coordinates, lineThrough.coordinates],
    };
    expect(distanceToLineMeters(POINT, multi)).toBeLessThan(0.5);
    expect(distanceToLineMeters(POINT, null)).toBe(Number.POSITIVE_INFINITY);
  });

  it('puts the road the report is on first, whatever order the server sent', () => {
    const features = [
      { id: 'north', geometry: lineNorth },
      { id: 'none', geometry: null },
      { id: 'through', geometry: lineThrough },
    ];
    const nearest = nearestFeatures(features, POINT, 5);
    expect(nearest.map((entry) => entry.feature.id)).toEqual([
      'through',
      'north',
    ]);
  });
});
