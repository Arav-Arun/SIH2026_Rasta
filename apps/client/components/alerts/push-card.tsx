'use client';

import { useCallback, useEffect, useState } from 'react';
import { BellOff, BellRing } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useT } from '@/components/i18n/locale-provider';
import { useAccessToken, usePushStatus } from '@/lib/api/hooks';
import { sendTestPush } from '@/lib/api/operations';
import type { PushTestResponse } from '@/lib/api/contracts';
import {
  currentSubscription,
  disablePush,
  enablePush,
  expectTestPush,
  pushSupport,
  type PushSupport,
} from '@/lib/pwa/push';

type Local = {
  support: PushSupport;
  permission: NotificationPermission | 'unknown';
  subscribed: boolean;
};

async function readLocal(): Promise<Local> {
  const support = pushSupport();
  const permission =
    typeof Notification === 'undefined' ? 'unknown' : Notification.permission;
  const subscription =
    support === 'supported' ? await currentSubscription() : null;
  return { support, permission, subscribed: Boolean(subscription) };
}

/** Notifications on this device, as an opt-in beside the inbox. */
export function PushCard() {
  const t = useT();
  const accessToken = useAccessToken();
  const status = usePushStatus();
  const [local, setLocal] = useState<Local | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLocal(await readLocal());
  }, []);

  useEffect(() => {
    let active = true;
    void readLocal().then((next) => {
      if (active) setLocal(next);
    });
    return () => {
      active = false;
    };
  }, []);

  const configured = status.data?.configured ?? false;
  const publicKey = status.data?.public_key ?? null;

  async function turnOn() {
    if (!accessToken || !publicKey) return;
    setBusy(true);
    setMessage(null);
    const outcome = await enablePush({
      accessToken,
      publicKey,
      copy: {
        title: t('alerts.push.notificationTitle'),
        body: t('alerts.push.notificationBody'),
      },
    });
    if (outcome.kind === 'failed')
      setMessage(t('alerts.push.failed', { reason: outcome.reason }));
    if (outcome.kind === 'no_worker') setMessage(t('alerts.push.noWorker'));
    await refresh();
    void status.refetch();
    setBusy(false);
  }

  async function turnOff() {
    setBusy(true);
    setMessage(null);
    await disablePush(accessToken);
    await refresh();
    void status.refetch();
    setBusy(false);
  }

  async function test() {
    if (!accessToken) return;
    setBusy(true);
    setMessage(null);
    try {
      await expectTestPush({
        title: t('alerts.push.testTitle'),
        body: t('alerts.push.testBody'),
      });
      const result: PushTestResponse = await sendTestPush({ accessToken });
      setMessage(
        result.sent > 0
          ? t('alerts.push.testResult', {
              sent: result.sent,
              attempted: result.attempted,
            })
          : t('alerts.push.testNothing', {
              reasons: result.errors.join(', ') || '-',
            }),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
    void status.refetch();
    setBusy(false);
  }

  let state: string;
  if (!status.data || !local) state = t('list.loading');
  else if (!configured) state = t('alerts.push.notConfigured');
  else if (local.support === 'no_api') state = t('alerts.push.unsupported');
  else if (local.support === 'no_service_worker')
    state = t('alerts.push.noWorker');
  else if (local.permission === 'denied') state = t('alerts.push.blocked');
  else if (local.subscribed) state = t('alerts.push.on');
  else state = t('alerts.push.off');

  const canEnable =
    configured &&
    Boolean(publicKey) &&
    local?.support === 'supported' &&
    local.permission !== 'denied' &&
    !local.subscribed;

  return (
    <section
      aria-labelledby="push-card-title"
      className="rounded-lg border border-border bg-card p-4"
      data-push-state={
        !configured
          ? 'not_configured'
          : local?.subscribed
            ? 'subscribed'
            : local?.support
      }
    >
      <h2
        id="push-card-title"
        className="flex items-center gap-2 text-sm font-semibold"
      >
        {local?.subscribed ? (
          <BellRing className="size-4" aria-hidden />
        ) : (
          <BellOff className="size-4" aria-hidden />
        )}
        {t('alerts.push.title')}
      </h2>
      <p className="mt-1 text-sm" data-push-status>
        {state}
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {canEnable && (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => void turnOn()}
          >
            {t('alerts.push.enable')}
          </Button>
        )}
        {local?.subscribed && (
          <>
            <Button
              type="button"
              size="sm"
              disabled={busy}
              onClick={() => void test()}
            >
              {t('alerts.push.test')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void turnOff()}
            >
              {t('alerts.push.disable')}
            </Button>
          </>
        )}
      </div>

      {message && (
        <output className="mt-2 block text-xs" data-push-message>
          {message}
        </output>
      )}
    </section>
  );
}
