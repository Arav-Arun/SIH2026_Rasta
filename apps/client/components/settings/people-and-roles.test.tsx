// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from '@/components/i18n/locale-provider';
import { PeopleAndRolesView } from '@/components/settings/people-and-roles';
import type { PeopleResponse } from '@/lib/api/contracts';

vi.mock('@/lib/api/hooks', () => ({
  usePeople: vi.fn(),
  useAdministerRoles: vi.fn(),
}));

const DISTRICT = 'd-1';

function data(changes: Partial<PeopleResponse> = {}): PeopleResponse {
  return {
    as_of: '2026-10-06T12:00:00Z',
    invitations_available: true,
    districts: [{ id: DISTRICT, name: 'East Khasi Hills' }],
    roles: [
      { role: 'admin', organisation_wide: true, capabilities: [] },
      { role: 'field_officer', organisation_wide: false, capabilities: [] },
    ],
    people: [
      {
        profile_id: 'p-admin',
        display_name: 'Admin One',
        email: 'admin@example.test',
        active: true,
        is_you: true,
        grants: [
          {
            id: 'g-admin',
            role: 'admin',
            district_id: null,
            district_name: null,
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: null,
            revoked_at: null,
            granted_by_profile_id: null,
            granted_by_name: null,
            state: 'active',
          },
        ],
      },
      {
        profile_id: 'p-officer',
        display_name: 'Officer Two',
        email: null,
        active: true,
        is_you: false,
        grants: [
          {
            id: 'g-officer',
            role: 'field_officer',
            district_id: DISTRICT,
            district_name: 'East Khasi Hills',
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: null,
            revoked_at: null,
            granted_by_profile_id: 'p-admin',
            granted_by_name: 'Admin One',
            state: 'active',
          },
        ],
      },
    ],
    ...changes,
  };
}

function show(value: PeopleResponse, actions = fakeActions()) {
  render(
    <LocaleProvider>
      <PeopleAndRolesView data={value} actions={actions} />
    </LocaleProvider>,
  );
  return actions;
}

function fakeActions() {
  return {
    grant: vi.fn().mockResolvedValue({}),
    revoke: vi.fn().mockResolvedValue({}),
    invite: vi.fn().mockResolvedValue({}),
  };
}

describe('people and roles in settings', () => {
  it('lets an admin end someone else’s role but never their own', async () => {
    const actions = show(data());
    expect(
      screen.queryByRole('button', {
        name: "End Admin One's Administrator role",
      }),
    ).toBeNull();
    expect(
      screen.getByText('Your own roles are changed by another admin.'),
    ).toBeTruthy();
    fireEvent.click(
      screen.getByRole('button', {
        name: "End Officer Two's Field officer role",
      }),
    );
    await waitFor(() =>
      expect(actions.revoke).toHaveBeenCalledWith('g-officer'),
    );
  });

  it('asks for a district only where the role needs one', async () => {
    const actions = show(data({ invitations_available: false }));
    const role = screen.getByLabelText('Role');
    expect(screen.getByLabelText('District')).toBeTruthy();
    fireEvent.change(role, { target: { value: 'admin' } });
    expect(screen.queryByLabelText('District')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
    await waitFor(() =>
      expect(actions.grant).toHaveBeenCalledWith('p-officer', {
        role: 'admin',
        district_id: null,
        valid_to: null,
      }),
    );
  });

  it('says why a change was refused', async () => {
    const actions = fakeActions();
    actions.grant.mockRejectedValue(
      new Error('This person already holds that role there.'),
    );
    show(data({ invitations_available: false }), actions);
    fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
    expect(
      await screen.findByText(
        'Not changed: This person already holds that role there.',
      ),
    ).toBeTruthy();
  });

  it('invites a person with their first role, or says it cannot', async () => {
    const actions = show(data());
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: ' new@example.test ' },
    });
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'New Officer' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send invitation' }));
    await waitFor(() =>
      expect(actions.invite).toHaveBeenCalledWith({
        email: 'new@example.test',
        display_name: 'New Officer',
        grant: { role: 'field_officer', district_id: DISTRICT, valid_to: null },
      }),
    );
    expect(
      await screen.findByText('Invitation sent to new@example.test.'),
    ).toBeTruthy();
  });

  it('says plainly when invitations are not set up', () => {
    show(data({ invitations_available: false }));
    expect(
      screen.getByText(/Invitations need the sign-in service/),
    ).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: 'Send invitation' }),
    ).toBeNull();
  });
});
