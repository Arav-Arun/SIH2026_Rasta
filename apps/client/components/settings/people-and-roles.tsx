'use client';

import { useState } from 'react';
import { UserPlus } from 'lucide-react';

import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { ErrorPanel } from '@/components/common/error-panel';
import { Button } from '@/components/ui/button';
import type {
  InviteRequest,
  PeopleResponse,
  Person,
  RoleGrant,
  RoleGrantRequest,
} from '@/lib/api/contracts';
import { useAdministerRoles, usePeople } from '@/lib/api/hooks';
import { cn } from '@/lib/utils';

type Actions = {
  grant: (profileId: string, body: RoleGrantRequest) => Promise<unknown>;
  revoke: (grantId: string) => Promise<unknown>;
  invite: (body: InviteRequest) => Promise<unknown>;
};

const STATE_STYLES: Record<RoleGrant['state'], string> = {
  active: 'text-[#065F46]',
  scheduled: 'text-[#1E40AF]',
  expired: 'text-muted-foreground',
  revoked: 'text-muted-foreground line-through',
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The end of the chosen day, where the browser is: a grant ends that evening. */
function endOf(day: string): string | null {
  if (!day) return null;
  const end = new Date(`${day}T23:59:59`);
  return Number.isNaN(end.getTime()) ? null : end.toISOString();
}

/** People and their roles, for an organisation's admins. */
export function PeopleAndRoles() {
  const t = useT();
  const people = usePeople();
  const admin = useAdministerRoles();
  if (people.isPending) {
    return <p className="text-sm text-muted-foreground">{t('list.loading')}</p>;
  }
  if (people.isError) {
    return (
      <ErrorPanel
        title={t('admin.error.load')}
        message={message(people.error)}
        onRetry={() => void people.refetch()}
      />
    );
  }
  const key = () => crypto.randomUUID();
  return (
    <PeopleAndRolesView
      data={people.data}
      actions={{
        grant: (profileId, body) =>
          admin.grant.mutateAsync({ profileId, body, idempotencyKey: key() }),
        revoke: (grantId) =>
          admin.revoke.mutateAsync({ grantId, idempotencyKey: key() }),
        invite: (body) =>
          admin.invite.mutateAsync({ body, idempotencyKey: key() }),
      }}
    />
  );
}

export function PeopleAndRolesView({
  data,
  actions,
}: {
  data: PeopleResponse;
  actions: Actions;
}) {
  const t = useT();
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function attempt(run: () => Promise<unknown>, done?: string) {
    setFailure(null);
    setNotice(null);
    try {
      await run();
      if (done) setNotice(done);
      return true;
    } catch (error) {
      setFailure(t('admin.error.change', { reason: message(error) }));
      return false;
    }
  }

  return (
    <section
      className="rounded-lg border border-border bg-card p-4"
      data-people-and-roles
    >
      <h2 className="text-sm font-semibold">{t('admin.heading')}</h2>
      <p className="mt-1 text-xs text-muted-foreground">{t('admin.lede')}</p>

      {failure ? (
        <p role="alert" className="mt-3 text-sm font-medium text-[#B91C1C]">
          {failure}
        </p>
      ) : null}
      {notice ? <output className="mt-3 block text-sm">{notice}</output> : null}

      {data.invitations_available ? (
        <InviteForm
          data={data}
          onInvite={(body) =>
            attempt(
              () => actions.invite(body),
              t('admin.invite.sent', { email: body.email }),
            )
          }
        />
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          {t('admin.invite.unavailable')}
        </p>
      )}

      <ul className="mt-4 flex flex-col gap-3">
        {data.people.map((person) => (
          <PersonRow
            key={person.profile_id}
            person={person}
            data={data}
            onGrant={(body) =>
              attempt(() => actions.grant(person.profile_id, body))
            }
            onRevoke={(grantId) => attempt(() => actions.revoke(grantId))}
          />
        ))}
      </ul>
    </section>
  );
}

function PersonRow({
  person,
  data,
  onGrant,
  onRevoke,
}: {
  person: Person;
  data: PeopleResponse;
  onGrant: (body: RoleGrantRequest) => Promise<boolean>;
  onRevoke: (grantId: string) => Promise<boolean>;
}) {
  const t = useT();
  const formatTime = useFormatTime();
  return (
    <li
      className="rounded-md border border-border p-3"
      data-person={person.profile_id}
    >
      <p className="text-sm font-semibold">
        {person.display_name}
        {person.is_you ? (
          <span className="ms-2 text-xs font-normal text-muted-foreground">
            {t('admin.you')}
          </span>
        ) : null}
      </p>
      {person.email ? (
        <p className="text-xs text-muted-foreground">{person.email}</p>
      ) : null}
      {person.grants.length === 0 ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t('admin.noGrants')}
        </p>
      ) : (
        <ul className="mt-1.5 flex flex-col gap-1 text-xs">
          {person.grants.map((grant) => (
            <li
              key={grant.id}
              className="flex flex-wrap items-center gap-x-2 gap-y-1"
              data-grant-state={grant.state}
            >
              <span className={cn('font-medium', STATE_STYLES[grant.state])}>
                {t(`admin.role.${grant.role}`)}
              </span>
              <span className="text-muted-foreground">
                {grant.district_name ?? t('admin.wholeOrganisation')}
                {', '}
                {grant.valid_to
                  ? t('admin.until', { when: formatTime.date(grant.valid_to) })
                  : t('admin.noEnd')}
                {', '}
                {t(`admin.state.${grant.state}`)}
              </span>
              {!person.is_you &&
              (grant.state === 'active' || grant.state === 'scheduled') ? (
                <button
                  type="button"
                  onClick={() => void onRevoke(grant.id)}
                  className="rounded border border-border px-1.5 py-0.5 font-medium"
                  aria-label={t('admin.revokeLabel', {
                    role: t(`admin.role.${grant.role}`),
                    name: person.display_name,
                  })}
                >
                  {t('admin.revoke')}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {person.is_you ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t('admin.ownRoles')}
        </p>
      ) : person.active ? (
        <GrantFields
          data={data}
          label={t('admin.grant.submit')}
          idPrefix={`grant-${person.profile_id}`}
          onSubmit={onGrant}
        />
      ) : null}
    </li>
  );
}

function GrantFields({
  data,
  label,
  idPrefix,
  onSubmit,
}: {
  data: PeopleResponse;
  label: string;
  idPrefix: string;
  onSubmit: (body: RoleGrantRequest) => Promise<boolean>;
}) {
  const t = useT();
  const [role, setRole] = useState<RoleGrantRequest['role']>(
    data.roles.find((option) => !option.organisation_wide)?.role ?? 'driver',
  );
  const [district, setDistrict] = useState(data.districts[0]?.id ?? '');
  const [ends, setEnds] = useState('');
  const [busy, setBusy] = useState(false);
  const wide =
    data.roles.find((option) => option.role === role)?.organisation_wide ??
    false;

  async function submit() {
    setBusy(true);
    const ok = await onSubmit({
      role,
      district_id: wide ? null : district || null,
      valid_to: endOf(ends),
    });
    setBusy(false);
    if (ok) setEnds('');
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      className="mt-2 flex flex-wrap items-end gap-2 text-xs"
    >
      <label className="flex flex-col gap-0.5" htmlFor={`${idPrefix}-role`}>
        {t('admin.grant.role')}
        <select
          id={`${idPrefix}-role`}
          value={role}
          onChange={(event) =>
            setRole(event.target.value as RoleGrantRequest['role'])
          }
          className="rounded border border-border bg-background px-1.5 py-1"
        >
          {data.roles.map((option) => (
            <option key={option.role} value={option.role}>
              {t(`admin.role.${option.role}`)}
            </option>
          ))}
        </select>
      </label>
      {wide ? null : (
        <label
          className="flex flex-col gap-0.5"
          htmlFor={`${idPrefix}-district`}
        >
          {t('admin.grant.district')}
          <select
            id={`${idPrefix}-district`}
            value={district}
            onChange={(event) => setDistrict(event.target.value)}
            className="rounded border border-border bg-background px-1.5 py-1"
          >
            {data.districts.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-0.5" htmlFor={`${idPrefix}-ends`}>
        {t('admin.grant.ends')}
        <input
          id={`${idPrefix}-ends`}
          type="date"
          value={ends}
          onChange={(event) => setEnds(event.target.value)}
          className="rounded border border-border bg-background px-1.5 py-1"
        />
      </label>
      <Button type="submit" size="sm" variant="outline" disabled={busy}>
        {label}
      </Button>
    </form>
  );
}

function InviteForm({
  data,
  onInvite,
}: {
  data: PeopleResponse;
  onInvite: (body: InviteRequest) => Promise<boolean>;
}) {
  const t = useT();
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');

  return (
    <div className="mt-3 rounded-md border border-dashed border-border p-3">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold">
        <UserPlus className="size-4" aria-hidden />
        {t('admin.invite.heading')}
      </h3>
      <div className="mt-2 flex flex-wrap gap-2 text-xs">
        <label className="flex flex-col gap-0.5" htmlFor="invite-email">
          {t('admin.invite.email')}
          <input
            id="invite-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded border border-border bg-background px-1.5 py-1"
          />
        </label>
        <label className="flex flex-col gap-0.5" htmlFor="invite-name">
          {t('admin.invite.name')}
          <input
            id="invite-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded border border-border bg-background px-1.5 py-1"
          />
        </label>
      </div>
      <GrantFields
        data={data}
        label={t('admin.invite.submit')}
        idPrefix="invite"
        onSubmit={async (grant) => {
          const ok = await onInvite({
            email: email.trim(),
            display_name: name.trim(),
            grant,
          });
          if (ok) {
            setEmail('');
            setName('');
          }
          return ok;
        }}
      />
    </div>
  );
}
