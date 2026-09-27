import type { Passability } from '@/lib/api/contracts';

/**
 * Road status colours follow the design tokens and are paired with a line
 * pattern so colour is never the only carrier: open solid, restricted dashed,
 * closed hatched, unknown dotted.
 */
export const PASSABILITY_COLORS: Record<Passability, string> = {
  open: '#166534',
  restricted: '#92400E',
  closed: '#991B1B',
  unknown: '#475569',
};

export const PASSABILITY_ORDER: Passability[] = [
  'closed',
  'restricted',
  'unknown',
  'open',
];

/** MapLibre `line-dasharray` (in line widths); null means solid. */
export const PASSABILITY_DASH: Record<Passability, number[] | null> = {
  open: null,
  restricted: [3, 2],
  closed: null,
  unknown: [0.6, 2.2],
};

export const FACILITY_STATUS_COLORS = {
  reachable: '#166534',
  isolated: '#991B1B',
  unknown_coverage: '#475569',
} as const;
export const HATCH_IMAGE_ID = 'rasta-hatch-closed';

/**
 * A small diagonal hatch tile used as `line-pattern` for closed roads. Built
 * at runtime so no binary asset ships with the app.
 */
export function buildHatchImage(size = 8): {
  width: number;
  height: number;
  data: Uint8ClampedArray;
} {
  const data = new Uint8ClampedArray(size * size * 4);
  const [r, g, b] = [0x99, 0x1b, 0x1b];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const onStripe = (x + y) % 4 < 2;
      const index = (y * size + x) * 4;
      data[index] = r;
      data[index + 1] = g;
      data[index + 2] = b;
      data[index + 3] = onStripe ? 255 : 70;
    }
  }
  return { width: size, height: size, data };
}

export function passabilityLayerId(status: Passability): string {
  return `segments-${status}`;
}

export const SEGMENT_LAYER_IDS = PASSABILITY_ORDER.map(passabilityLayerId);
