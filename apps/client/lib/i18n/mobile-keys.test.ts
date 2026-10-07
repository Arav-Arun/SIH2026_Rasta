import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import en from '@/i18n/en.json';

import { flattenCatalogue } from './messages';

/**
 * The Android app draws its text from this catalogue. A key it asks for that is
 * missing here would show on screen as the key itself, so every one is checked.
 */
const MOBILE_ROOT = fileURLToPath(new URL('../../../mobile/', import.meta.url));
const SKIP = new Set(['node_modules', '.expo', 'android', 'ios', 'dist']);

function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return SKIP.has(entry.name) ? [] : sources(path);
    return /\.tsx?$/.test(entry.name) && !entry.name.endsWith('.d.ts')
      ? [path]
      : [];
  });
}

const catalogue = flattenCatalogue(en);
const known = Object.keys(catalogue);
const topLevel = new Set(Object.keys(en).filter((key) => key !== '_meta'));
const files = sources(MOBILE_ROOT);

/** Keys built from a value at run time: every value the app can pass. */
const BUILT_FROM_A_VALUE: Record<string, string[]> = {
  'mobile.category.': [
    'landslide',
    'boulder_fall',
    'bridge_overwash',
    'road_crack',
    'flash_flood',
    'heavy_jam',
  ],
  'mobile.severity.': ['low', 'moderate', 'high', 'critical'],
  'mobile.captureTab.level.': ['moderate', 'high', 'critical'],
  'mobile.seat.': ['driver', 'observer'],
  'mobile.vehicle.': [
    'suv_4x4',
    'commercial_6w',
    'heavy_freight_12w',
    'car_sedan',
  ],
  'mobile.trips.status.': [
    'planned',
    'awaiting_driver',
    'active',
    'paused',
    'completed',
    'failed',
    'cancelled',
  ],
  'mobile.tasks.status.': [
    'assigned',
    'accepted',
    'in_progress',
    'submitted',
    'reviewed',
    'cancelled',
    'overdue',
  ],
  'mobile.tasks.target.': ['incident', 'segment', 'bridge', 'facility'],
  'mobile.evidence.status.': [
    'none',
    'not_sent',
    'failed',
    'uploaded',
    'verified',
    'rejected',
  ],
  'mobile.outboxTab.photo.': [
    'none',
    'not_sent',
    'failed',
    'uploaded',
    'verified',
    'rejected',
  ],
  'mobile.sos.facility.': ['reachable', 'isolated', 'unknown_coverage'],
  'mobile.delivery.status.': ['delivered', 'partially_delivered', 'failed'],
  'alerts.severity.': ['critical', 'warning', 'info'],
};

const ROUTE_STATES = [
  'confirmed_current',
  'cached_unverified',
  'cached_offline',
  'replaced',
  'withdrawn',
  'none',
];
const ABSENCES = ['no_trip', 'no_route_plan', 'plan_unreadable'];

describe('the Android app’s text', () => {
  it('reads the app’s sources', () => {
    expect(files.length).toBeGreaterThan(30);
  });

  it('asks only for keys the English catalogue has', () => {
    const missing: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(
        /(['"`])([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)+)\1/g,
      )) {
        const key = match[2];
        if (topLevel.has(key.split('.')[0]) && !(key in catalogue)) {
          missing.push(`${file.replace(MOBILE_ROOT, '')}: ${key}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it('has every key built from a value at run time', () => {
    const missing = Object.entries(BUILT_FROM_A_VALUE).flatMap(
      ([prefix, values]) =>
        values
          .map((value) => `${prefix}${value}`)
          .filter((k) => !(k in catalogue)),
    );
    for (const state of ROUTE_STATES) {
      missing.push(
        ...[`mobile.routes.fresh.${state}.label`].filter(
          (k) => !(k in catalogue),
        ),
      );
      if (state !== 'none') {
        const body = `mobile.routes.fresh.${state}.body`;
        if (!(body in catalogue)) missing.push(body);
      }
    }
    for (const kind of ABSENCES) {
      missing.push(
        ...[
          `mobile.routes.absence.${kind}.title`,
          `mobile.routes.absence.${kind}.body`,
        ].filter((k) => !(k in catalogue)),
      );
    }
    expect(missing).toEqual([]);
  });

  it('builds run-time keys only from prefixes that exist', () => {
    const unknown: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, 'utf8');
      for (const match of text.matchAll(
        /`([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)*\.)\$\{/g,
      )) {
        const prefix = match[1];
        if (
          topLevel.has(prefix.split('.')[0]) &&
          !known.some((key) => key.startsWith(prefix))
        ) {
          unknown.push(`${file.replace(MOBILE_ROOT, '')}: ${prefix}`);
        }
      }
    }
    expect(unknown).toEqual([]);
  });
});
