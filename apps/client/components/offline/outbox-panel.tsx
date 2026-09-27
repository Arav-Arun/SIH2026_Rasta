'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
  Hourglass,
  Link2,
  RotateCcw,
} from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { useAuth } from '@/components/auth/auth-provider';
import { EmptyState } from '@/components/common/empty-state';
import {
  blockedMutations,
  listMutations,
  retryMutation,
} from '@/lib/offline/outbox';
import { requestSync } from '@/lib/offline/sync-request';
import type { MutationEnvelope, MutationState } from '@/lib/offline/types';
import { cn } from '@/lib/utils';

/** What is still waiting to reach the server, and why. */
export function OutboxPanel() {
  const t = useT();
  const { workspace } = useAuth();
  const profileId = workspace?.identity.profileId ?? null;
  const queryClient = useQueryClient();

  const queue = useQuery({
    queryKey: ['outbox', profileId],
    queryFn: async () => {
      const [rows, blocked] = await Promise.all([
        listMutations(profileId),
        blockedMutations(profileId),
      ]);
      return {
        rows,
        blockedIds: new Set(blocked.map((row) => row.mutationId)),
      };
    },
    refetchInterval: 5_000,
  });

  const rows = queue.data?.rows ?? [];
  const pending = rows.filter((row) => row.state !== 'accepted');

  if (queue.isPending) {
    return <p className="text-sm text-muted-foreground">{t('list.loading')}</p>;
  }

  if (pending.length === 0) {
    return <EmptyState title={t('outbox.empty.title')} />;
  }

  return (
    <ul className="flex flex-col gap-2" aria-label={t('outbox.queueLabel')}>
      {pending.map((mutation) => (
        <li key={mutation.mutationId}>
          <OutboxRow
            mutation={mutation}
            blocked={queue.data?.blockedIds.has(mutation.mutationId) ?? false}
            onRetry={async () => {
              await retryMutation(mutation.mutationId);
              requestSync();
              await queryClient.invalidateQueries({ queryKey: ['outbox'] });
            }}
          />
        </li>
      ))}
    </ul>
  );
}

function OutboxRow({
  mutation,
  blocked,
  onRetry,
}: {
  mutation: MutationEnvelope;
  blocked: boolean;
  onRetry: () => void;
}) {
  const t = useT();
  const needsPerson =
    mutation.state === 'conflict' || mutation.state === 'needs_action';

  return (
    <div
      className={cn(
        'rounded-lg border p-3',
        needsPerson
          ? 'border-amber-500/60 bg-amber-50 dark:bg-amber-950/30'
          : 'border-border bg-card',
      )}
      data-mutation-state={blocked ? 'blocked' : mutation.state}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold">
          {t(`outbox.type.${mutation.type}`)}
        </span>
        <StateChip state={mutation.state} blocked={blocked} />
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        {blocked
          ? t('outbox.blockedReason')
          : (mutation.lastError ?? t(`outbox.stateHelp.${mutation.state}`))}
      </p>

      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {mutation.attemptCount > 0
            ? t('outbox.attempts', { count: String(mutation.attemptCount) })
            : t('outbox.notYetSent')}
        </span>

        {needsPerson && (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 font-medium"
          >
            <RotateCcw className="size-3" aria-hidden />
            {t('outbox.retry')}
          </button>
        )}
      </div>
    </div>
  );
}

function StateChip({
  state,
  blocked,
}: {
  state: MutationState;
  blocked: boolean;
}) {
  const t = useT();

  if (blocked) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
        <Link2 className="size-3.5" aria-hidden />
        {t('outbox.state.blocked')}
      </span>
    );
  }

  const icon = {
    queued: <Hourglass className="size-3.5" aria-hidden />,
    uploading: <CloudUpload className="size-3.5" aria-hidden />,
    accepted: <CheckCircle2 className="size-3.5" aria-hidden />,
    conflict: <AlertTriangle className="size-3.5" aria-hidden />,
    needs_action: <AlertTriangle className="size-3.5" aria-hidden />,
  }[state];

  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
      {icon}
      {t(`outbox.state.${state}`)}
    </span>
  );
}
