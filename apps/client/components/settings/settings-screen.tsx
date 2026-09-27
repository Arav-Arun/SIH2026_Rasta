'use client';

import { useState, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, Info, RefreshCw, ShieldCheck } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { useT } from '@/components/i18n/locale-provider';
import { useServiceWorkerUpdate } from '@/components/providers/service-worker-provider';
import { CommandShell } from '@/components/layout/command-shell';
import { ErrorPanel } from '@/components/common/error-panel';
import { APP_VERSION } from '@/lib/pwa/app-version';
import {
  downloadPack,
  fetchManifest,
  packAgeDays,
  packIsStale,
  readActivePack,
  subscribeToActivePack,
  type PackManifestEntry,
} from '@/lib/pwa/data-pack';

/** Settings: what this build is, what data it is holding, and how old it is. */
export function SettingsScreen() {
  const t = useT();
  const { workspace } = useAuth();
  const update = useServiceWorkerUpdate();

  const [progress, setProgress] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  // The active pack lives in the shared local store, so a download in another
  // tab is reflected here and the server render sees "none" rather than
  // guessing.
  const pack = useSyncExternalStore(
    subscribeToActivePack,
    readActivePack,
    () => null,
  );

  const manifest = useQuery({
    queryKey: ['data-pack-manifest'],
    queryFn: ({ signal }) => fetchManifest(signal),
    // The manifest is a static file beside the build; it changes when the build
    // does, so there is nothing to poll for.
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const available = manifest.data ?? [];
  const manifestError =
    manifest.error instanceof Error
      ? manifest.error.message
      : manifest.error
        ? 'The pack list could not be read.'
        : null;

  async function download(entry: PackManifestEntry) {
    setOutcome(null);
    setProgress(0);
    const result = await downloadPack(entry, { onProgress: setProgress });
    setProgress(null);
    setOutcome(
      result.ok
        ? { ok: true, message: t('settings.pack.activated') }
        : { ok: false, message: result.reason },
    );
  }

  const stale = pack ? packIsStale(pack) : false;

  return (
    <CommandShell activeHref="/settings" title={t('settings.title')}>
      <div className="flex max-w-3xl flex-col gap-6">
        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold">{t('settings.build.title')}</h2>
          <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">
                {t('settings.build.appVersion')}
              </dt>
              <dd className="font-mono text-xs" data-app-version>
                {APP_VERSION}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                {t('settings.build.mode')}
              </dt>
              <dd data-deployment-mode>
                {workspace ? t(`mode.${workspace.organizationMode}`) : '-'}
              </dd>
            </div>
          </dl>

          {update.kind === 'update_waiting' ? (
            <div className="mt-4 flex flex-wrap items-center gap-3 rounded-md bg-[#FEF3C7] p-3">
              <RefreshCw className="size-4 shrink-0 text-[#92400E]" />
              <p className="min-w-0 flex-1 text-sm text-[#92400E]">
                {t('settings.update.ready')}
              </p>
              <button
                type="button"
                className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"
                onClick={update.apply}
                data-apply-update
              >
                {t('settings.update.apply')}
              </button>
            </div>
          ) : null}

          {update.kind === 'update_held' ? (
            <p
              className="mt-4 rounded-md bg-muted p-3 text-sm text-muted-foreground"
              data-update-held
            >
              {t('settings.update.held', { count: update.pending })}
            </p>
          ) : null}

          {update.kind === 'unsupported' ? (
            <p className="mt-4 text-sm text-muted-foreground">
              {t('settings.update.unsupported')}
            </p>
          ) : null}
        </section>

        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold">{t('settings.pack.title')}</h2>

          {pack ? (
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">
                  {t('settings.pack.version')}
                </dt>
                <dd className="font-mono text-xs" data-pack-version>
                  {pack.version}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('settings.pack.age')}
                </dt>
                <dd data-pack-age>
                  {t('settings.pack.ageDays', { days: packAgeDays(pack) })}
                  {stale ? `, ${t('settings.pack.stale')}` : ''}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('settings.pack.contents')}
                </dt>
                <dd>
                  {t('settings.pack.counts', {
                    edges: pack.counts.edges,
                    facilities: pack.counts.facilities,
                  })}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t('settings.pack.checksum')}
                </dt>
                <dd className="truncate font-mono text-xs">
                  {pack.sha256.slice(0, 16)}…
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground">
                  {t('settings.pack.attribution')}
                </dt>
                <dd className="text-xs">
                  {pack.attribution} ({pack.license})
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">
              {t('settings.pack.noneTitle')}
            </p>
          )}

          {stale ? (
            <p className="mt-3 flex items-start gap-2 rounded-md bg-[#FEF3C7] p-3 text-sm text-[#92400E]">
              <Info className="size-4 shrink-0" />
              {t('settings.pack.staleExplainer')}
            </p>
          ) : null}

          {manifestError ? (
            <div className="mt-3">
              <ErrorPanel
                title={t('settings.pack.manifestError')}
                message={manifestError}
                onRetry={() => void manifest.refetch()}
              />
            </div>
          ) : null}

          <ul className="mt-4 flex flex-col gap-2">
            {available.map((entry) => {
              const current = pack?.version === entry.version;
              return (
                <li
                  key={`${entry.pack_id}:${entry.version}`}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
                  data-pack-offer={entry.version}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {entry.pack_id}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t('settings.pack.offerDetail', {
                        version: entry.version,
                        megabytes: (entry.bytes / 1_048_576).toFixed(1),
                      })}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                    disabled={current || progress !== null}
                    onClick={() => void download(entry)}
                    data-download-pack={entry.version}
                  >
                    <Download className="size-4" />
                    {current
                      ? t('settings.pack.alreadyHave')
                      : progress !== null
                        ? t('settings.pack.downloading', {
                            percent: Math.round(progress * 100),
                          })
                        : t('settings.pack.download')}
                  </button>
                </li>
              );
            })}
          </ul>

          {outcome ? (
            <p
              className={
                outcome.ok
                  ? 'mt-3 flex items-start gap-2 text-sm text-[#166534]'
                  : 'mt-3 flex items-start gap-2 text-sm text-[#92400E]'
              }
              data-pack-outcome={outcome.ok ? 'ok' : 'refused'}
            >
              <ShieldCheck className="size-4 shrink-0" />
              {outcome.message}
            </p>
          ) : null}
        </section>
      </div>
    </CommandShell>
  );
}
