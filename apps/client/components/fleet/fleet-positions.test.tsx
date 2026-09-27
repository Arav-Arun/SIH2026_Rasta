// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from '@/components/i18n/locale-provider';
import { FleetPositions } from '@/components/fleet/fleet-positions';
import type {
  FleetLocationsResponse,
  FleetTripLocation,
} from '@/lib/api/contracts';

const getFleetLocations = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/telemetry', () => ({
  getFleetLocations,
  getTripTelemetry: vi.fn(),
}));

vi.mock('@/components/auth/auth-provider', () => ({
  useAuth: () => ({
    session: { access_token: 'test-token' },
    workspace: { organization_id: 'org-1' },
  }),
}));

const NOW = new Date('2026-09-25T10:00:00Z');

function trip(overrides: Partial<FleetTripLocation>): FleetTripLocation {
  return {
    trip_id: 'trip-1',
    consignment_id: 'consignment-1',
    consignment_reference: 'CON-001',
    district_id: 'district-1',
    vehicle_id: 'vehicle-1',
    vehicle_registration: 'ML05AB1234',
    driver_id: 'driver-1',
    driver_name: 'A. Driver',
    status: 'active',
    started_at: '2026-09-25T09:00:00Z',
    location: null,
    reporting_state: 'never_reported',
    ...overrides,
  } as FleetTripLocation;
}

function locationAt(as_of: string, stale: boolean) {
  return {
    trip_id: 'trip-1',
    latitude: 25.5788,
    longitude: 91.8933,
    accuracy_m: '8.5',
    as_of,
    stale_after: '2026-09-25T10:05:00Z',
    is_stale: stale,
    speed_kph: '24',
    heading: null,
  };
}

function respond(trips: FleetTripLocation[]): FleetLocationsResponse {
  return {
    trips,
    live: trips.filter((t) => t.reporting_state === 'live').length,
    stale: trips.filter((t) => t.reporting_state === 'stale').length,
    never_reported: trips.filter((t) => t.reporting_state === 'never_reported')
      .length,
    stale_after_seconds: 300,
    server_time: NOW.toISOString(),
  } as FleetLocationsResponse;
}

function wrap(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LocaleProvider>{node}</LocaleProvider>
    </QueryClientProvider>,
  );
}

describe('FleetPositions', () => {
  it('separates a reporting vehicle from a late one and from a silent one', async () => {
    getFleetLocations.mockResolvedValue(
      respond([
        trip({
          trip_id: 'live-trip',
          reporting_state: 'live',
          location: {
            ...locationAt('2026-09-25T09:59:00Z', false),
            trip_id: 'live-trip',
          },
        }),
        trip({
          trip_id: 'stale-trip',
          consignment_reference: 'CON-002',
          reporting_state: 'stale',
          location: {
            ...locationAt('2026-09-25T09:20:00Z', true),
            trip_id: 'stale-trip',
          },
        }),
        trip({ trip_id: 'silent-trip', consignment_reference: 'CON-003' }),
      ]),
    );

    const { container } = wrap(<FleetPositions />);

    await waitFor(() =>
      expect(container.querySelectorAll('[data-trip-reporting]')).toHaveLength(
        3,
      ),
    );
    expect(
      container
        .querySelector('[data-trip-id="live-trip"]')
        ?.getAttribute('data-trip-reporting'),
    ).toBe('live');
    expect(
      container
        .querySelector('[data-trip-id="stale-trip"]')
        ?.getAttribute('data-trip-reporting'),
    ).toBe('stale');
    expect(
      container
        .querySelector('[data-trip-id="silent-trip"]')
        ?.getAttribute('data-trip-reporting'),
    ).toBe('never_reported');
  });

  it('says a late position is where the vehicle was, not where it is', async () => {
    getFleetLocations.mockResolvedValue(
      respond([
        trip({
          trip_id: 'stale-trip',
          reporting_state: 'stale',
          location: {
            ...locationAt('2026-09-25T09:20:00Z', true),
            trip_id: 'stale-trip',
          },
        }),
      ]),
    );

    wrap(<FleetPositions />);

    await waitFor(() => expect(screen.getByText(/Late/)).toBeTruthy());
    expect(
      screen.getByText(/Treat this position as where the vehicle was/),
    ).toBeTruthy();
  });

  it('shows no coordinates at all for a trip that has never reported', async () => {
    getFleetLocations.mockResolvedValue(respond([trip({})]));

    const { container } = wrap(<FleetPositions />);

    await waitFor(() =>
      expect(
        container.querySelector('[data-trip-reporting="never_reported"]'),
      ).toBeTruthy(),
    );
    // A blank coordinate line would read as "somewhere near zero".
    expect(container.textContent).not.toContain('25.57880');
    expect(screen.getByText(/no position yet/i)).toBeTruthy();
  });

  it('states the mix rather than leaving a dispatcher to count rows', async () => {
    getFleetLocations.mockResolvedValue(
      respond([
        trip({
          trip_id: 'live-trip',
          reporting_state: 'live',
          location: {
            ...locationAt('2026-09-25T09:59:00Z', false),
            trip_id: 'live-trip',
          },
        }),
        trip({ trip_id: 'silent-trip' }),
      ]),
    );

    const { container } = wrap(<FleetPositions />);

    await waitFor(() =>
      expect(container.querySelector('[data-fleet-summary]')).toBeTruthy(),
    );
    expect(
      container.querySelector('[data-fleet-summary]')?.textContent,
    ).toContain('1');
  });
});
