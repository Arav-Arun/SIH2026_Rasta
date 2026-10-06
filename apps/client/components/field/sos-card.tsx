'use client';

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { MessageSquareText, Siren } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { useT } from '@/components/i18n/locale-provider';
import { Button } from '@/components/ui/button';
import { enqueueMutation, getMutation } from '@/lib/offline/outbox';
import { requestSync } from '@/lib/offline/sync-request';
import type { MutationEnvelope } from '@/lib/offline/types';

type Fix = { latitude: number; longitude: number; accuracy: number };

/** One fix, or null when the browser cannot or will not give one in time. */
function currentFix(): Promise<Fix | null> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    );
  });
}

type Stage = 'idle' | 'confirming' | 'locating' | 'done';

/**
 * Two channels at once: a text to 112 that the person checks and sends, and an
 * alert to the control room that waits in the sync queue until it can be sent.
 * Neither calls emergency services by itself.
 */
export function SosCard() {
  const t = useT();
  const { workspace } = useAuth();
  const profileId = workspace?.identity.profileId ?? null;
  const queryClient = useQueryClient();
  const [stage, setStage] = useState<Stage>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [mutation, setMutation] = useState<MutationEnvelope | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  // Follow the queued alert until the server has it.
  useEffect(() => {
    if (!mutation || mutation.state === 'accepted') return;
    const timer = setInterval(() => {
      void getMutation(mutation.mutationId).then((row) => {
        if (row) setMutation(row);
      });
    }, 2_000);
    return () => clearInterval(timer);
  }, [mutation]);

  async function send() {
    setStage('locating');
    setFailure(null);
    const pressedAt = new Date().toISOString();
    const fix = await currentFix();
    const lines = ['RASTA SOS', `TIME: ${pressedAt}`];
    lines.push(
      fix
        ? `POSITION: ${fix.latitude.toFixed(5)}, ${fix.longitude.toFixed(5)} (within ${Math.round(fix.accuracy)} m)`
        : 'POSITION: NOT AVAILABLE. Say where you are.',
    );
    const body = lines.join('\n');
    setMessage(body);
    try {
      const queued = await enqueueMutation({
        type: 'sos.raise',
        profileId,
        body: {
          captured_at: pressedAt,
          latitude: fix?.latitude ?? null,
          longitude: fix?.longitude ?? null,
          accuracy_m: fix ? Math.max(fix.accuracy, 0.1) : null,
        },
      });
      setMutation(queued);
      await queryClient.invalidateQueries({ queryKey: ['outbox'] });
      requestSync();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    }
    setStage('done');
    window.location.href = `sms:112?body=${encodeURIComponent(body)}`;
  }

  const recipients = mutation?.result?.recipients;

  return (
    <section
      aria-labelledby="sos-heading"
      className="flex flex-col gap-3 rounded-lg border border-[#FECACA] bg-[#FEF2F2] p-4 text-[#7F1D1D]"
    >
      <div>
        <h2 id="sos-heading" className="text-sm font-semibold">
          {t('fieldHome.sos.title')}
        </h2>
        <p className="mt-1 text-sm">{t('fieldHome.sos.lede')}</p>
      </div>

      {stage === 'idle' || stage === 'done' ? (
        <div>
          <Button variant="destructive" onClick={() => setStage('confirming')}>
            <Siren aria-hidden />
            {t('fieldHome.sos.button')}
          </Button>
        </div>
      ) : null}

      {stage === 'confirming' ? (
        <div className="flex flex-wrap items-center gap-2" role="alertdialog">
          <p className="text-sm font-medium">{t('fieldHome.sos.confirm')}</p>
          <Button variant="destructive" onClick={() => void send()}>
            {t('fieldHome.sos.confirmYes')}
          </Button>
          <Button variant="outline" onClick={() => setStage('idle')}>
            {t('fieldHome.sos.cancel')}
          </Button>
        </div>
      ) : null}

      {stage === 'locating' ? (
        <output className="block text-sm">{t('fieldHome.sos.locating')}</output>
      ) : null}

      {stage === 'done' && message ? (
        <div className="flex flex-col gap-2 text-sm">
          <output className="block font-medium">
            {failure
              ? t('fieldHome.sos.notQueued', { reason: failure })
              : mutation?.state === 'accepted'
                ? typeof recipients === 'number' && recipients > 0
                  ? t('fieldHome.sos.sent', { count: recipients })
                  : t('fieldHome.sos.sentNobody')
                : t('fieldHome.sos.queued')}
          </output>
          <p className="text-xs">{t('fieldHome.sos.messageHelp')}</p>
          <pre className="whitespace-pre-wrap rounded-md border border-[#FECACA] bg-background p-2 text-xs text-foreground">
            {message}
          </pre>
          <a
            href={`sms:112?body=${encodeURIComponent(message)}`}
            className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground"
          >
            <MessageSquareText className="size-4" aria-hidden />
            {t('fieldHome.sos.openAgain')}
          </a>
        </div>
      ) : null}
    </section>
  );
}
