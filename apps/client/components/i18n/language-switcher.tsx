'use client';

import { useId } from 'react';
import { Languages, LoaderCircle } from 'lucide-react';

import { useLocale } from '@/components/i18n/locale-provider';
import { languageInfo } from '@/lib/i18n/languages';
import { SUPPORTED_LOCALES } from '@/lib/i18n/messages';
import { cn } from '@/lib/utils';

/** The name a speaker looks for, followed by the one others can read. */
function optionLabel(code: string): string {
  const info = languageInfo(code);
  if (!info) return code;
  return info.native === info.english
    ? info.native
    : `${info.native} (${info.english})`;
}

export function LanguageSwitcher({ className }: { className?: string }) {
  const { locale, requestedLocale, meta, setLocale, t } = useLocale();
  const id = useId();
  const noteId = useId();
  const Icon = requestedLocale !== locale ? LoaderCircle : Languages;
  const unreviewed = meta.reviewed ? undefined : t('locale.unreviewed');

  return (
    <div
      className={cn(
        'inline-flex h-8 items-center gap-1 rounded-md border bg-background ps-2 text-xs',
        className,
      )}
      title={unreviewed}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          'size-3.5 shrink-0 text-muted-foreground',
          Icon === LoaderCircle && 'animate-spin',
        )}
      />
      <label htmlFor={id} className="sr-only">
        {t('locale.label')}
      </label>
      <select
        id={id}
        value={requestedLocale}
        onChange={(event) => void setLocale(event.target.value)}
        aria-describedby={unreviewed ? noteId : undefined}
        className="h-full max-w-[9rem] cursor-pointer rounded-md bg-transparent pe-1 font-medium"
        data-language-select
      >
        {SUPPORTED_LOCALES.map((code) => (
          <option key={code} value={code} lang={code}>
            {optionLabel(code)}
          </option>
        ))}
      </select>
      {unreviewed ? (
        <span id={noteId} role="note" className="sr-only">
          {unreviewed}
        </span>
      ) : null}
    </div>
  );
}
