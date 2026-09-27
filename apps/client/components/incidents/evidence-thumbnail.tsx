'use client';

import { useQuery } from '@tanstack/react-query';
import { FileWarning, ImageOff, Loader2 } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { createEvidenceUrl } from '@/lib/api/evidence';
import type { AttachmentInfo } from '@/lib/api/contracts';

/** Renders one evidence photo from the private bucket. */
export function EvidenceThumbnail({
  attachment,
  onOpen,
}: {
  attachment: AttachmentInfo;
  onOpen: (url: string) => void;
}) {
  const t = useT();
  const viewable = attachment.upload_status === 'verified';

  // Signed links are short-lived, so they are refetched rather than cached for
  // long; TanStack Query also dedupes the request when a report is re-rendered.
  const link = useQuery({
    queryKey: ['evidence-url', attachment.storage_key],
    queryFn: () => createEvidenceUrl(attachment.storage_key),
    enabled: viewable,
    staleTime: 240_000,
    retry: false,
  });

  const url = link.data?.ok ? link.data.url : null;
  const error = link.data && !link.data.ok ? link.data.reason : null;

  if (!viewable) {
    return (
      <div className="flex aspect-4/3 items-center justify-center rounded-md border border-dashed border-border bg-muted/40 p-2">
        <p className="text-center text-[11px] leading-tight text-muted-foreground">
          <FileWarning className="mx-auto mb-1 size-4" aria-hidden />
          {t('incidents.evidenceNotViewable')}
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex aspect-4/3 items-center justify-center rounded-md border border-border bg-muted/40 p-2">
        <p className="text-center text-[11px] leading-tight text-muted-foreground">
          <ImageOff className="mx-auto mb-1 size-4" aria-hidden />
          {error}
        </p>
      </div>
    );
  }

  if (!url) {
    return (
      <div className="flex aspect-4/3 items-center justify-center rounded-md border border-border bg-muted/40">
        <Loader2
          className="size-4 animate-spin text-muted-foreground"
          aria-label={t('incidents.evidenceLoading')}
        />
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(url)}
      className="group relative block aspect-4/3 w-full overflow-hidden rounded-md border border-border"
      aria-label={t('incidents.evidenceOpen')}
    >
      {/* Evidence is arbitrary user-supplied imagery of unknown dimensions, so
          it is served straight from storage rather than through the image
          optimiser, which would need the private object to be fetchable. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={t('incidents.evidenceAlt')}
        className="size-full object-cover transition-transform group-hover:scale-105"
      />
    </button>
  );
}
