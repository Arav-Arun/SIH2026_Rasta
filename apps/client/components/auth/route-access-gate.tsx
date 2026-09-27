'use client';

import Link from 'next/link';
import { CloudOff, LoaderCircle, LockKeyhole, ShieldAlert } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { useFormatTime, useT } from '@/components/i18n/locale-provider';
import { decideRouteAccess, homePathFor } from '@/lib/auth/policy';

function GateFrame({
  children,
  title,
}: {
  children: React.ReactNode;
  title: string;
}) {
  const t = useT();
  return (
    <main className="grid min-h-svh place-items-center bg-[#f5f7fa] p-4 text-[#172033]">
      <Card className="w-full max-w-md shadow-none">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-lg bg-blue-50 text-blue-700">
            <LockKeyhole className="size-5" />
          </div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{t('gate.frameBody')}</CardDescription>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </main>
  );
}

/** Client-side presentation guard for the static shell. */
export function RouteAccessGate({
  children,
  path,
}: {
  children: React.ReactNode;
  path: string;
}) {
  const { bootstrapStatus, offlineSince, status, workspace } = useAuth();
  const t = useT();
  const formatTime = useFormatTime();

  if (
    status === 'loading' ||
    (status === 'authenticated' && bootstrapStatus === 'loading')
  ) {
    return (
      <GateFrame title={t('gate.checkingTitle')}>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" aria-hidden />
          {t('gate.checkingBody')}
        </div>
      </GateFrame>
    );
  }

  if (status === 'unconfigured') {
    return (
      <GateFrame title={t('gate.unconfiguredTitle')}>
        <Alert>
          <ShieldAlert />
          <AlertTitle>{t('gate.unconfiguredHeading')}</AlertTitle>
          <AlertDescription>{t('gate.unconfiguredBody')}</AlertDescription>
        </Alert>
      </GateFrame>
    );
  }

  if (status !== 'authenticated' || !workspace) {
    const reason =
      bootstrapStatus === 'scope_denied'
        ? t('gate.reason.scopeDenied')
        : bootstrapStatus === 'session_expired'
          ? t('gate.reason.sessionExpired')
          : bootstrapStatus === 'unavailable'
            ? t('gate.reason.unavailable')
            : t('gate.reason.signIn');

    return (
      <GateFrame title={t('gate.requiredTitle')}>
        <Alert>
          <ShieldAlert />
          <AlertTitle>{t('gate.requiredHeading')}</AlertTitle>
          <AlertDescription>{reason}</AlertDescription>
        </Alert>
        <Button
          className="mt-4"
          nativeButton={false}
          render={<Link href="/sign-in" />}
        >
          {t('nav.signIn')}
        </Button>
      </GateFrame>
    );
  }

  const decision = decideRouteAccess(workspace.identity, { path });
  if (!decision.allowed) {
    return (
      <GateFrame title={t('gate.deniedTitle')}>
        <Alert>
          <ShieldAlert />
          <AlertTitle>{t('gate.deniedHeading')}</AlertTitle>
          <AlertDescription>{t('gate.deniedBody')}</AlertDescription>
        </Alert>
        <Button
          className="mt-4"
          nativeButton={false}
          render={<Link href={homePathFor(workspace.identity)} />}
        >
          {t('gate.returnToWorkspace')}
        </Button>
      </GateFrame>
    );
  }

  if (bootstrapStatus === 'offline') {
    return (
      <>
        <output
          data-offline-identity
          className="flex items-center gap-2 border-b border-[#FCD34D] bg-[#FFFBEB] px-4 py-2 text-sm text-[#92400E]"
        >
          <CloudOff className="size-4 shrink-0" aria-hidden />
          {t('gate.offlineNotice', {
            when: offlineSince ? formatTime.time(offlineSince) : '-',
          })}
        </output>
        {children}
      </>
    );
  }

  return <>{children}</>;
}
