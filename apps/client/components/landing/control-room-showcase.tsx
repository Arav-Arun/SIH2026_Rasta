'use client';

import { useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/utils';

import { BrowserFrame } from './device-frames';

const SCREENS = [
  {
    id: 'overview',
    label: 'Overview',
    src: '/landing/web-overview.webp',
    address: 'rasta / overview',
    caption:
      'Reachable facilities, active deliveries, blocked roads and reports waiting for a decision.',
  },
  {
    id: 'map',
    label: 'Accessibility map',
    src: '/landing/web-map.webp',
    address: 'rasta / map',
    caption:
      'Every road segment in the district, marked open, restricted, closed or unknown.',
  },
  {
    id: 'planner',
    label: 'Route planner',
    src: '/landing/web-planner.webp',
    address: 'rasta / planner',
    caption:
      'Up to three genuinely different routes, each with its ETA range, risk and the limits it respects.',
  },
  {
    id: 'incidents',
    label: 'Field reports',
    src: '/landing/web-incidents.webp',
    address: 'rasta / incidents',
    caption:
      'Geo-tagged photos from the field, checked by a dispatcher before a road changes status.',
  },
  {
    id: 'deliveries',
    label: 'Deliveries',
    src: '/landing/web-deliveries.webp',
    address: 'rasta / deliveries',
    caption:
      'Requests, consignments, trips and receipts, with every shortfall flagged.',
  },
  {
    id: 'fleet',
    label: 'Fleet',
    src: '/landing/web-fleet.webp',
    address: 'rasta / fleet',
    caption: 'Where every vehicle is, and which ones have stopped reporting.',
  },
  {
    id: 'data-health',
    label: 'Data health',
    src: '/landing/web-data-health.webp',
    address: 'rasta / data-health',
    caption: 'The source and age of every feed, so nobody acts on stale data.',
  },
  {
    id: 'hindi',
    label: 'हिन्दी',
    src: '/landing/web-overview-hindi.webp',
    address: 'rasta / overview',
    caption:
      'The same control room in Hindi, one of the four Indian languages it supports.',
  },
] as const;

/**
 * Screens advance when the progress bar on the active tab finishes. The bar
 * waits while the showcase is off screen, and with reduced motion it never
 * runs, so the screens stay where the visitor put them.
 */
export function ControlRoomShowcase() {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const tabs = useRef<HTMLDivElement>(null);
  const screen = SCREENS[index];
  const paused = !visible;

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.25 },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Keep the active tab in view on narrow screens without scrolling the page.
  useEffect(() => {
    const list = tabs.current;
    const tab = list?.children[index] as HTMLElement | undefined;
    if (!list || !tab) return;
    list.scrollTo({
      left: tab.offsetLeft - (list.clientWidth - tab.offsetWidth) / 2,
      behavior: 'smooth',
    });
  }, [index]);

  return (
    <div ref={root}>
      <div
        ref={tabs}
        role="tablist"
        aria-label="Control room screens"
        className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-2 sm:mx-0 sm:flex-wrap sm:justify-center sm:px-0"
      >
        {SCREENS.map((item, itemIndex) => {
          const selected = itemIndex === index;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`screen-tab-${item.id}`}
              aria-selected={selected}
              aria-controls="screen-panel"
              onClick={() => setIndex(itemIndex)}
              className={cn(
                'relative shrink-0 overflow-hidden rounded-lg border px-3.5 py-2 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#191314]',
                selected
                  ? 'border-[#191314] bg-[#191314] text-white'
                  : 'border-[#191314]/15 bg-white text-[#191314] hover:border-[#191314]/40',
              )}
            >
              {item.label}
              {selected ? (
                <span
                  key={index}
                  aria-hidden
                  onAnimationEnd={() =>
                    setIndex((current) => (current + 1) % SCREENS.length)
                  }
                  className={cn(
                    'absolute inset-x-0 bottom-0 h-0.5 origin-left animate-[landing-progress_5s_linear_forwards] bg-[#7AA2F7] motion-reduce:hidden',
                    paused && '[animation-play-state:paused]',
                  )}
                />
              ) : null}
            </button>
          );
        })}
      </div>
      <div
        id="screen-panel"
        role="tabpanel"
        aria-labelledby={`screen-tab-${screen.id}`}
        // Sized from the window height so the tabs, screen and caption fit in one view.
        className="mx-auto mt-6 w-full max-w-[min(960px,calc((100dvh_-_17rem)*1.6))]"
      >
        <BrowserFrame address={screen.address}>
          <div className="grid">
            {SCREENS.map((item, itemIndex) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={item.id}
                src={item.src}
                alt={
                  itemIndex === index ? `RASTA control room: ${item.label}` : ''
                }
                aria-hidden={itemIndex !== index}
                width={1440}
                height={900}
                loading="lazy"
                decoding="async"
                className={cn(
                  'col-start-1 row-start-1 block h-auto w-full transition-opacity duration-500 motion-reduce:transition-none',
                  itemIndex === index ? 'opacity-100' : 'opacity-0',
                )}
              />
            ))}
          </div>
        </BrowserFrame>
        <p className="mx-auto mt-4 max-w-xl text-center text-[13px] leading-6 text-[#191314]/70">
          {screen.caption}
        </p>
      </div>
    </div>
  );
}
