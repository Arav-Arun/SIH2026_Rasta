// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SupplyGaps } from '@/components/deliveries/supply-gaps-panel';
import { LocaleProvider } from '@/components/i18n/locale-provider';
import type { GapRequest, SupplyGapsResponse } from '@/lib/api/contracts';

vi.mock('@/lib/api/hooks', () => ({ useSupplyGaps: vi.fn() }));

function gap(changes: Partial<GapRequest>): GapRequest {
  return {
    request_id: 'r-1',
    facility_id: 'f-1',
    facility_name: 'Mawlai PHC',
    district_id: 'd-1',
    priority: 'high',
    needed_by: '2026-10-06T14:00:00Z',
    status: 'open',
    state: 'at_risk',
    reason: 'nothing_on_the_way',
    expected_arrival: null,
    consignments_on_the_way: 0,
    consignments_arrived: 0,
    consignments_failed: 0,
    on_the_way: [],
    shortfalls: [],
    ...changes,
  };
}

function show(requests: GapRequest[]) {
  const counts: Record<string, number> = {
    overdue: 0,
    at_risk: 0,
    unknown: 0,
    waiting: 0,
    on_track: 0,
    no_deadline: 0,
  };
  for (const item of requests) counts[item.state] += 1;
  const report: SupplyGapsResponse = {
    as_of: '2026-10-06T12:00:00Z',
    district_id: null,
    at_risk_window_hours: 24,
    counts,
    requests,
    notes: [],
  };
  return render(
    <LocaleProvider>
      <SupplyGaps report={report} />
    </LocaleProvider>,
  );
}

describe('supply gaps on the deliveries screen', () => {
  it('says why each unmet request is at risk, and what came up short', () => {
    show([
      gap({ request_id: 'r-1', state: 'overdue', reason: 'deadline_passed' }),
      gap({
        request_id: 'r-2',
        facility_name: 'Laitumkhrah CHC',
        state: 'at_risk',
        reason: 'arrives_after_deadline',
        expected_arrival: '2026-10-06T15:00:00Z',
        on_the_way: [{ commodity: 'rice', unit: 'kg', quantity: 100 }],
        shortfalls: [
          {
            commodity: 'tarpaulin',
            unit: 'piece',
            dispatched: 20,
            received: 12,
            short: 8,
          },
        ],
      }),
    ]);
    expect(screen.getByText('Overdue: 1')).toBeTruthy();
    expect(screen.getByText('At risk: 1')).toBeTruthy();
    expect(screen.queryByText(/On track:/)).toBeNull();
    expect(screen.getByText(/that has passed/)).toBeTruthy();
    expect(screen.getByText(/after the deadline of/)).toBeTruthy();
    expect(screen.getByText('On the way: 100 kg rice')).toBeTruthy();
    expect(
      screen.getByText('Short: 12 of 20 piece tarpaulin received'),
    ).toBeTruthy();
    expect(screen.getByText(/not a predicted stockout/)).toBeTruthy();
  });

  it('says plainly when every request is met', () => {
    show([]);
    expect(screen.getByText('No unmet requests.')).toBeTruthy();
  });
});
