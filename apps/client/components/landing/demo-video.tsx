'use client';

import { Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/utils';

import { VIDEO_ID } from './links';

/**
 * The walkthrough film. Nothing is requested from YouTube until the visitor
 * presses play, and then only through the privacy-enhanced embed domain.
 */
export function DemoVideo({ className }: { className?: string }) {
  const [playing, setPlaying] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);

  // The button that was pressed is gone once the player replaces it.
  useEffect(() => {
    if (playing) frame.current?.focus();
  }, [playing]);

  return (
    <div
      className={cn(
        'relative aspect-video overflow-hidden rounded-2xl bg-[#191314]',
        className,
      )}
    >
      {playing ? (
        <iframe
          ref={frame}
          className="absolute inset-0 size-full"
          src={`https://www.youtube-nocookie.com/embed/${VIDEO_ID}?autoplay=1&rel=0&playsinline=1`}
          title="RASTA demo: from a blocked road to an approved new route"
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          referrerPolicy="strict-origin-when-cross-origin"
        />
      ) : (
        <button
          type="button"
          onClick={() => setPlaying(true)}
          aria-label="Play the 3-minute RASTA demo video"
          className="group absolute inset-0 focus-visible:outline-2 focus-visible:-outline-offset-4 focus-visible:outline-[#7AA2F7]"
        >
          {/* Pre-compressed WebP from public/, so there is nothing for an optimiser to do. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/landing/demo-poster.webp"
            alt=""
            width={1280}
            height={720}
            loading="lazy"
            decoding="async"
            className="size-full object-cover"
          />
          <span
            className="absolute bottom-3 left-3 inline-flex items-center gap-2.5 rounded-full bg-[#7AA2F7] py-2 pr-4 pl-2 text-[12px] font-medium text-[#191314] shadow-[0_16px_32px_-12px_rgba(25,19,20,0.5)] transition-transform group-hover:scale-[1.03] sm:bottom-6 sm:left-6 sm:gap-3 sm:py-2.5 sm:pr-5 sm:pl-2.5 sm:text-[13px]"
            aria-hidden
          >
            <span className="flex size-8 items-center justify-center rounded-full bg-[#191314] text-white sm:size-9">
              <Play className="size-4 translate-x-px fill-current" />
            </span>
            <span className="sm:hidden">Play, 3 min</span>
            <span className="hidden sm:inline">
              Play the walkthrough, 3 min
            </span>
          </span>
        </button>
      )}
    </div>
  );
}
