'use client';

import { useT } from '@/components/i18n/locale-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { DeviceCheck } from '@/components/permissions/device-check';

/** What this device will let the app use, checked for real. */
export function PermissionsScreen() {
  const t = useT();
  return (
    <CommandShell
      ownHeading
      activeHref="/permissions"
      title={t('permissions.title')}
    >
      <div className="flex max-w-2xl flex-col gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('permissions.title')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t('permissions.lede')}
          </p>
        </div>
        <DeviceCheck />
      </div>
    </CommandShell>
  );
}
