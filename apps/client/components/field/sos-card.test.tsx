// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SosCard } from '@/components/field/sos-card';
import { LocaleProvider } from '@/components/i18n/locale-provider';

const enqueueMutation = vi.hoisted(() => vi.fn());
const getMutation = vi.hoisted(() => vi.fn());
const requestSync = vi.hoisted(() => vi.fn());

vi.mock('@/lib/offline/outbox', () => ({ enqueueMutation, getMutation }));
vi.mock('@/lib/offline/sync-request', () => ({ requestSync }));
vi.mock('@/components/auth/auth-provider', () => ({
  useAuth: () => ({ workspace: { identity: { profileId: 'profile-1' } } }),
}));

function geolocation(position: GeolocationPosition | null) {
  Object.defineProperty(navigator, 'geolocation', {
    configurable: true,
    value: {
      getCurrentPosition: (
        ok: PositionCallback,
        fail: PositionErrorCallback,
      ) =>
        position
          ? ok(position)
          : fail({ code: 1, message: 'denied' } as GeolocationPositionError),
    },
  });
}

function card() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <LocaleProvider>
        <SosCard />
      </LocaleProvider>
    </QueryClientProvider>,
  );
}

const queued = {
  mutationId: 'mut-1',
  state: 'queued',
  result: null,
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('SOS on the web field app', () => {
  it('asks first, queues the alert with the fix, and reports delivery', async () => {
    geolocation({
      coords: { latitude: 25.571234, longitude: 91.884567, accuracy: 12.4 },
    } as GeolocationPosition);
    enqueueMutation.mockResolvedValue(queued);
    getMutation.mockResolvedValue({
      ...queued,
      state: 'accepted',
      result: { recipients: 3 },
    });

    card();
    fireEvent.click(screen.getByRole('button', { name: 'SOS' }));
    expect(enqueueMutation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Send SOS' }));

    await waitFor(() => expect(enqueueMutation).toHaveBeenCalledTimes(1));
    const request = enqueueMutation.mock.calls[0][0];
    expect(request.type).toBe('sos.raise');
    expect(request.profileId).toBe('profile-1');
    expect(request.body).toMatchObject({
      latitude: 25.571234,
      longitude: 91.884567,
      accuracy_m: 12.4,
    });
    expect(requestSync).toHaveBeenCalled();
    expect(screen.getByText(/POSITION: 25\.57123, 91\.88457/)).toBeTruthy();
    expect(screen.getByText(/queued/)).toBeTruthy();

    await waitFor(
      () =>
        expect(
          screen.getByText('Control room alerted: 3 people notified.'),
        ).toBeTruthy(),
      { timeout: 4_000 },
    );
  });

  it('says so when the phone cannot tell its position', async () => {
    geolocation(null);
    enqueueMutation.mockResolvedValue(queued);
    getMutation.mockResolvedValue(queued);

    card();
    fireEvent.click(screen.getByRole('button', { name: 'SOS' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send SOS' }));

    await waitFor(() => expect(enqueueMutation).toHaveBeenCalledTimes(1));
    expect(enqueueMutation.mock.calls[0][0].body).toMatchObject({
      latitude: null,
      longitude: null,
      accuracy_m: null,
    });
    expect(screen.getByText(/POSITION: NOT AVAILABLE/)).toBeTruthy();
  });
});
