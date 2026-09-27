'use client';

import { CommandShell } from '@/components/layout/command-shell';
import { useT } from '@/components/i18n/locale-provider';

import { OutboxPanel } from './outbox-panel';

/** The sync queue as a screen of its own. */
export function SyncQueueScreen() {
  const t = useT();

  return (
    <CommandShell activeHref="/sync" title={t('outbox.title')}>
      <div className="max-w-2xl">
        <OutboxPanel />
      </div>
    </CommandShell>
  );
}
