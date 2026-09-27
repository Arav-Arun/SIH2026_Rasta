'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

import { useAuth } from '@/components/auth/auth-provider';
import { useOnlineStatus } from '@/hooks/use-online-status';
import { uploadEvidenceObject } from '@/lib/api/evidence';
import { currentAccessToken } from '@/lib/auth/supabase';
import { pushOutbox, type PushSummary } from '@/lib/offline/sync';
import { onSyncRequested } from '@/lib/offline/sync-request';

/** Drives the outbox. */
function useSyncRunner() {
  const { session, workspace } = useAuth();
  const online = useOnlineStatus();
  const queryClient = useQueryClient();

  const accessToken = session?.access_token ?? null;
  const profileId = workspace?.identity.profileId ?? null;

  const push = useMutation<PushSummary | null>({
    mutationFn: async () => {
      if (!accessToken) return null;
      const token = await currentAccessToken(accessToken);
      if (!token) return null;
      return pushOutbox({
        accessToken: token,
        profileId,
        uploadObject: uploadEvidenceObject,
      });
    },
    onSuccess: (summary) => {
      if (!summary || summary.accepted === 0) return;
      // Something reached the server, so every view of server state may have
      // moved. Refetch rather than guessing what changed.
      void queryClient.invalidateQueries({ queryKey: ['outbox'] });
      void queryClient.invalidateQueries({ queryKey: ['incidents'] });
      void queryClient.invalidateQueries({ queryKey: ['inspections'] });
      void queryClient.invalidateQueries({ queryKey: ['network-segments'] });
      void queryClient.invalidateQueries({
        queryKey: ['connectivity-summary'],
      });
    },
  });

  // The profile is part of the condition, not just the session.
  const canSync = Boolean(accessToken) && online && profileId !== null;
  const { mutate } = push;

  // A new token is a reason to try again too: a pass refused for an expired
  // session stops and leaves its rows queued for exactly this moment.
  useEffect(() => {
    if (!canSync) return;
    mutate();
  }, [canSync, profileId, accessToken, mutate]);

  // Something was just queued: send it now rather than at the next reconnect.
  useEffect(
    () =>
      onSyncRequested(() => {
        if (canSync) mutate();
      }),
    [canSync, mutate],
  );

  useEffect(() => {
    if (typeof document === 'undefined') return;

    function onVisible() {
      if (document.visibilityState === 'visible' && canSync) mutate();
    }

    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [canSync, mutate]);

  return push;
}

/** Mounts the runner without rendering anything. */
export function SyncRunner() {
  useSyncRunner();
  return null;
}
