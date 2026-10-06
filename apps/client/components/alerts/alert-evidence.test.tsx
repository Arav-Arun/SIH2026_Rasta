// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AlertEvidence } from '@/components/alerts/alert-inbox-screen';
import { LocaleProvider } from '@/components/i18n/locale-provider';

vi.mock('@/components/auth/auth-provider', () => ({
  useAuth: () => ({ workspace: null }),
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
