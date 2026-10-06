// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  AlertEvidence,
  AlertRow,
} from '@/components/alerts/alert-inbox-screen';
import { LocaleProvider } from '@/components/i18n/locale-provider';
import type { AlertRecord } from '@/lib/api/contracts';

const auth = vi.hoisted(() => ({
  workspace: null as null | { capabilities: string[] },
}));

vi.mock('@/components/auth/auth-provider', () => ({
  useAuth: () => auth,
}));

vi.mock('@/lib/api/hooks', () => ({
  useAlerts: vi.fn(),
  useAcknowledgeAlert: vi.fn(),
}));

function evidence(payload: Record<string, unknown>) {
  return render(
    <LocaleProvider>
      <AlertEvidence payload={payload} />
    </LocaleProvider>,
  );
}

describe('SOS alert evidence', () => {
  it('says who, where and how precisely, and that it is not a 112 call', () => {
    evidence({
      reporter_name: 'Driver One',
      captured_at: '2026-10-06T12:00:00Z',
      position_known: true,
      latitude: 25.571234,
      longitude: 91.884567,
      accuracy_m: 9.4,
      note: 'Slope moving',
      calls_emergency_services: false,
    });
    expect(screen.getByText('From Driver One')).toBeTruthy();
    expect(
      screen.getByText('Position 25.57123, 91.88457, within 9 m'),
    ).toBeTruthy();
    expect(screen.getByText('Note: Slope moving')).toBeTruthy();
    expect(screen.getByText(/does not call emergency services/)).toBeTruthy();
    const map = screen.getByRole('link', {
      name: 'Open this position on a map',
    });
    expect(map.getAttribute('href')).toContain('mlat=25.571234');
    expect(map.getAttribute('rel')).toContain('noopener');
  });

  it('says plainly when the phone could not tell its position', () => {
    evidence({
      position_known: false,
      latitude: null,
      longitude: null,
      calls_emergency_services: false,
    });
    expect(
      screen.getByText('The phone could not tell its position'),
    ).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('adds nothing to an alert that is not an SOS', () => {
    const { container } = evidence({ segment_count: 3 });
    expect(screen.getByText('3 road sections affected')).toBeTruthy();
    expect(container.textContent).not.toMatch(/emergency services/);
  });
});

const SEGMENT = '0b6f3c4e-8f1a-4a57-9d1e-2f5c7a9b1c3d';

function slowRoad(): AlertRecord {
  return {
    id: 'alert-1',
    type: 'road_slow_traffic',
    severity: 'warning',
    title_key: 'alert.road_slow_traffic',
    district_id: 'district-1',
    subject_type: 'segment',
    subject_id: SEGMENT,
    payload: {
      segment_id: SEGMENT,
      trips: 3,
      points: 9,
      median_kph: 2.4,
      expected_kph: 40,
      slowness: 0.83,
      calibrated: false,
      suggested_action: 'assign_inspection',
      changes_passability: false,
    },
    valid_from: '2026-10-06T12:00:00Z',
    valid_until: '2026-10-06T14:00:00Z',
    dedupe_key: 'road_slow_traffic:segment:x:0',
    created_at: '2026-10-06T12:00:00Z',
    status: 'delivered',
    delivered_at: '2026-10-06T12:00:00Z',
    acknowledged_at: null,
    valid_now: true,
  };
}

function row(alert: AlertRecord) {
  return render(
    <LocaleProvider>
      <ul>
        <AlertRow alert={alert} busy={false} onAcknowledge={() => {}} />
      </ul>
    </LocaleProvider>,
  );
}

describe('slow-traffic suggestion', () => {
  it('says what the vehicles showed and that nothing was closed', () => {
    evidence(slowRoad().payload as Record<string, unknown>);
    expect(
      screen.getByText(
        '3 vehicles, median 2 km/h where 40 km/h is expected (thresholds not yet calibrated)',
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Nothing was closed/)).toBeTruthy();
  });

  it('takes whoever assigns inspections to the road on the map', () => {
    auth.workspace = { capabilities: ['alert:read', 'inspection:manage'] };
    row(slowRoad());
    const link = screen.getByRole('link', {
      name: 'Open the road to assign an inspection',
    });
    expect(link.getAttribute('href')).toBe(`/map?segment=${SEGMENT}`);
  });

  it('offers no inspection link to someone who cannot assign one', () => {
    auth.workspace = { capabilities: ['alert:read'] };
    row(slowRoad());
    expect(
      screen.queryByRole('link', {
        name: 'Open the road to assign an inspection',
      }),
    ).toBeNull();
    auth.workspace = null;
  });
});
