import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * GHSA-jrc7-96c5-q579 is an XSS bypass in MapLibre's HTML sanitiser, fixed only
 * in the 6.x line.
 */
function sourceFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (entry === 'node_modules' || entry.startsWith('.')) continue;
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path))
        out.push(path);
    }
  };
  walk(root);
  return out;
}

describe('MapLibre is never handed HTML', () => {
  const files = [
    ...sourceFiles('components'),
    ...sourceFiles('lib'),
    ...sourceFiles('app'),
  ];
  const mapFiles = files.filter((file) =>
    readFileSync(file, 'utf8').includes("'maplibre-gl'"),
  );

  it('finds the map components', () => {
    expect(mapFiles.length).toBeGreaterThanOrEqual(2);
  });

  it('no code sets popup or marker HTML', () => {
    const offenders = files.filter((file) =>
      /\.setHTML\s*\(|customAttribution/.test(readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('every attribution string is a constant from source', () => {
    const offenders: string[] = [];
    for (const file of mapFiles) {
      for (const match of readFileSync(file, 'utf8').matchAll(
        /attribution:\s*([^,\n}]+)/g,
      )) {
        if (match[1].trim() !== 'OSM_ATTRIBUTION')
          offenders.push(`${file}: ${match[1].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
