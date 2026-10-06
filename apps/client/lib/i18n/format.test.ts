import { beforeAll, describe, expect, it } from 'vitest';

import {
  describeFreshness,
  formatAbsoluteDate,
  formatAbsoluteTime,
  formatDistance,
  formatDuration,
  formatWeight,
} from './format';
import { loadCatalogue } from './messages';

// Hindi and Assamese load on demand, as they do in the app.
beforeAll(async () => {
  await loadCatalogue('hi');
  await loadCatalogue('as');
});

const NOW = new Date('2026-09-18T09:00:00.000Z');

describe('describeFreshness', () => {
  it('reports never-updated as stale with no absolute time', () => {
    const f = describeFreshness('en', null, NOW);
    expect(f).toMatchObject({ ageMs: null, stale: true, absolute: '' });
    expect(f.relative).toBe('Never updated');
  });

  it('computes relative age and stale flag against the threshold', () => {
    const fresh = describeFreshness('en', '2026-09-18T08:52:00.000Z', NOW);
    expect(fresh.relative).toBe('8 min ago');
    expect(fresh.stale).toBe(false);

    const stale = describeFreshness('en', '2026-09-18T06:00:00.000Z', NOW);
    expect(stale.relative).toBe('3 hours ago');
    expect(stale.stale).toBe(true);

    const days = describeFreshness('en', '2026-09-15T06:00:00.000Z', NOW);
    expect(days.relative).toBe('3 days ago');
  });

  it('shows absolute time in IST with the timezone label', () => {
    // 09:00Z is 14:30 IST
    expect(formatAbsoluteTime('en', NOW)).toMatch(/18 Sep(t)?,? 14:30 IST$/);
  });

  it('says the year when it is not this one', () => {
    expect(
      formatAbsoluteTime('en', new Date('2000-01-01T00:00:00Z'), {}, NOW),
    ).toMatch(/2000/);
    expect(formatAbsoluteTime('en', NOW, {}, NOW)).not.toMatch(/2026/);
  });

  it("dates are the Indian calendar day, not the viewer's", () => {
    // 20:00 UTC is already the next day in India.
    expect(formatAbsoluteDate('en', new Date('2026-09-18T20:00:00Z'))).toMatch(
      /^19 Sep(t)? 2026$/,
    );
  });

  it('translates relative age for other locales', () => {
    expect(
      describeFreshness('hi', '2026-09-18T08:52:00.000Z', NOW).relative,
    ).toBe('8 मिनट पहले');
  });
});

describe('units stay metric and explicit across locales', () => {
  it('distance', () => {
    expect(formatDistance('en', 850)).toBe('850 m');
    expect(formatDistance('en', 4436.195)).toBe('4.4 km');
    expect(formatDistance('en', 12_800)).toBe('13 km');
    expect(formatDistance('hi', 4436.195)).toBe('4.4 किमी');
    expect(formatDistance('en', Number.NaN)).toBe('-');
  });

  it('weight', () => {
    expect(formatWeight('en', 640)).toBe('640 kg');
    expect(formatWeight('en', 9500)).toBe('9.5 t');
    expect(formatWeight('as', 9500)).toBe('9.5 টন');
  });

  it('duration', () => {
    expect(formatDuration('en', 5 * 60_000)).toBe('5 min');
    expect(formatDuration('en', 2 * 3_600_000 + 15 * 60_000)).toBe(
      '2 h 15 min',
    );
    expect(formatDuration('en', 3 * 3_600_000)).toBe('3 h');
    expect(formatDuration('en', -1)).toBe('-');
  });
});
