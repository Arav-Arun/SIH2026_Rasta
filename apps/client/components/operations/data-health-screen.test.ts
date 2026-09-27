import { describe, expect, it } from 'vitest';

import type { SourceHealth } from '@/lib/api/contracts';

import { dataModeFromSources } from './data-health-screen';

function source(state: SourceHealth['state']): SourceHealth {
  return {
    source: `source-${state}`,
    source_mode: state === 'live' ? 'live' : 'recorded',
    state,
    last_status: null,
    last_attempt_at: null,
    last_success_at: null,
    freshness_seconds: 0,
    consecutive_failures: 0,
    error_code: null,
    record_count: 0,
  };
}

describe('dataModeFromSources', () => {
  it('claims live only when a source is actually live', () => {
    expect(dataModeFromSources([source('recorded'), source('live')])).toBe(
      'live',
    );
  });

  it('reports recorded documents as recorded, whatever the deployment', () => {
    // A hosted demo running on recorded documents is still recorded data.
    expect(dataModeFromSources([source('recorded'), source('failed')])).toBe(
      'recorded',
    );
    expect(dataModeFromSources([source('stale')])).toBe('recorded');
  });

  it('says nothing is connected when no source has produced anything', () => {
    expect(dataModeFromSources([])).toBeNull();
    expect(
      dataModeFromSources([source('never_run'), source('disabled')]),
    ).toBeNull();
    expect(dataModeFromSources([source('failed')])).toBeNull();
  });
});
