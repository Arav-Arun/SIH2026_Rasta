// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from '@/components/i18n/locale-provider';
import { OutcomeTable } from '@/components/operations/data-health-screen';
import type { RiskOutcomesResponse } from '@/lib/api/contracts';

vi.mock('@/lib/api/hooks', () => ({
  useDataHealth: vi.fn(),
  usePushStatus: vi.fn(),
  useRecomputeRisk: vi.fn(),
  useRiskOutcomes: vi.fn(),
}));

function report(rows: RiskOutcomesResponse['rows']): RiskOutcomesResponse {
  return {
    district_id: 'district-1',
    lead_days: 1,
    first_score_day: '2026-09-06',
    last_score_day: '2026-10-05',
    rows,
    confirmed_incident_road_days: rows.reduce(
      (sum, row) => sum + row.road_days_with_confirmed_incident,
      0,
    ),
    notes: [],
  };
}

function table(value: RiskOutcomesResponse) {
  return render(
    <LocaleProvider>
      <OutcomeTable report={value} days={30} />
    </LocaleProvider>,
  );
}

describe('outcome table on the data-health screen', () => {
  it('lists each level with its road-days and next-day incidents', () => {
    table(
      report([
        {
          model_version: 'baseline-v1',
          level: 'high',
          road_days: 12,
          road_days_with_confirmed_incident: 2,
        },
        {
          model_version: 'baseline-v1',
          level: 'low',
          road_days: 840,
          road_days_with_confirmed_incident: 1,
        },
        {
          model_version: null,
          level: 'not_scored',
          road_days: 0,
          road_days_with_confirmed_incident: 1,
        },
      ]),
    );
    expect(
      screen.getByText('Scores against next-day incidents, last 30 days'),
    ).toBeTruthy();
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      'High risk122',
      'Low risk8401',
      'Not scored the day before01',
    ]);
    expect(screen.queryByRole('columnheader', { name: 'Model' })).toBeNull();
    expect(screen.getByText(/not proof that nothing happened/)).toBeTruthy();
  });

  it('names the model on each row once two versions have scored', () => {
    table(
      report([
        {
          model_version: 'baseline-v1',
          level: 'high',
          road_days: 5,
          road_days_with_confirmed_incident: 1,
        },
        {
          model_version: 'trained-v1',
          level: 'high',
          road_days: 4,
          road_days_with_confirmed_incident: 1,
        },
      ]),
    );
    expect(screen.getByRole('columnheader', { name: 'Model' })).toBeTruthy();
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      'baseline-v1High risk51',
      'trained-v1High risk41',
    ]);
  });

  it('says plainly when nothing has been logged yet', () => {
    table(report([]));
    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.getByText(/Nothing logged yet/)).toBeTruthy();
  });
});
