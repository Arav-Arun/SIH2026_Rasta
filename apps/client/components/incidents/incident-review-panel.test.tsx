// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from '@/components/i18n/locale-provider';
import { IncidentReviewPanel } from '@/components/incidents/incident-review-panel';
import type { IncidentSummary } from '@/lib/api/contracts';

const reviewIncident = vi.hoisted(() => vi.fn());

// The panel talks to the API through the shared hook; stubbing the request
// keeps the test about reviewer behaviour, not transport.
vi.mock('@/lib/api/incidents', () => ({
  reviewIncident,
  getIncidentDetail: vi.fn(),
}));

vi.mock('@/components/auth/auth-provider', () => ({
  useAuth: () => ({
    session: { access_token: 'test-token' },
    workspace: { organization_id: 'org-1' },
  }),
}));

function baseIncident(
  overrides: Partial<IncidentSummary> = {},
): IncidentSummary {
  return {
    id: 'incident-1',
    district_id: 'district-1',
    type: 'landslide_debris',
    status: 'submitted',
    location: { latitude: 25.5788, longitude: 91.8933 },
    accuracy_m: 8,
    captured_at: '2026-09-23T04:00:00Z',
    reported_at: '2026-09-23T04:01:00Z',
    reporter_profile_id: 'profile-1',
    note: 'Debris across both lanes.',
    source_mode: 'recorded',
    version: 3,
    created_at: '2026-09-23T04:01:00Z',
    updated_at: '2026-09-23T04:01:00Z',
    attachments: [],
    segments: [
      {
        segment_id: '11111111-1111-4111-8111-111111111111',
        impact: 'monitor',
        name: 'NH-6 km 142',
        road_class: 'primary',
        reviewed_by_profile_id: null,
        reviewed_at: null,
      },
    ],
    ...overrides,
  } as IncidentSummary;
}

function wrap(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LocaleProvider>{ui}</LocaleProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  reviewIncident.mockReset();
  reviewIncident.mockResolvedValue({
    incident: baseIncident({ status: 'confirmed', version: 4 }),
    segment_changes: [],
    audit_event_ids: ['audit-1'],
    outbox_event_ids: ['outbox-1'],
    network_version: 'osm-shillong.20260923T040500000000Z',
    replayed: false,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('IncidentReviewPanel', () => {
  it('will not submit a state-changing decision without a segment and a reason', () => {
    wrap(
      <IncidentReviewPanel
        incident={baseIncident()}
        detailPending={false}
        onDecided={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('Confirm closure'));

    const submit = screen.getByRole('button', { name: 'Record decision' });
    expect(submit).toBeDisabled();
    expect(screen.getByText('Tick at least one segment.')).toBeTruthy();
  });

  it('sends the on-screen version so a stale decision is refused by the server', async () => {
    wrap(
      <IncidentReviewPanel
        incident={baseIncident({ version: 7 })}
        detailPending={false}
        onDecided={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('Confirm closure'));
    fireEvent.click(screen.getByRole('checkbox', { name: /NH-6 km 142/ }));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Photo shows both lanes buried.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Record decision' }));

    await waitFor(() => expect(reviewIncident).toHaveBeenCalledTimes(1));
    const [, , body, idempotencyKey] = reviewIncident.mock.calls[0];
    expect(body).toMatchObject({
      decision: 'confirm_closure',
      expected_version: 7,
      affected_segment_ids: ['11111111-1111-4111-8111-111111111111'],
      reason: 'Photo shows both lanes buried.',
    });
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('does not require a segment for decisions that leave the road alone', async () => {
    wrap(
      <IncidentReviewPanel
        incident={baseIncident()}
        detailPending={false}
        onDecided={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('Request clarification'));
    fireEvent.change(screen.getByRole('textbox'), {
      target: { value: 'Photo is too dark to read.' },
    });

    const submit = screen.getByRole('button', { name: 'Record decision' });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(reviewIncident).toHaveBeenCalledTimes(1));
    expect(reviewIncident.mock.calls[0][2]).toMatchObject({
      decision: 'request_clarification',
      affected_segment_ids: [],
    });
  });

  it('warns when a report carries no evidence and no position', () => {
    wrap(
      <IncidentReviewPanel
        incident={baseIncident({
          attachments: [],
          location: null,
          accuracy_m: null,
        })}
        detailPending={false}
        onDecided={() => {}}
      />,
    );

    expect(screen.getByText(/No evidence was attached/)).toBeTruthy();
    expect(screen.getByText(/No position was attached/)).toBeTruthy();
  });

  it('locks a report that has already been decided', () => {
    wrap(
      <IncidentReviewPanel
        incident={baseIncident({ status: 'confirmed' })}
        detailPending={false}
        onDecided={() => {}}
      />,
    );

    expect(screen.getByText(/already been decided/)).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: 'Record decision' }),
    ).toBeNull();
  });
});
