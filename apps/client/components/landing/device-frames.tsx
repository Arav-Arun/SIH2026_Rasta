import { cn } from '@/lib/utils';

type ScreenProps = {
  src: string;
  alt: string;
  className?: string;
  eager?: boolean;
};

/** A phone with a status bar above a real app screenshot (780 x 1688). */
export function PhoneFrame({ src, alt, className, eager }: ScreenProps) {
  return (
    <div
      className={cn(
        'rounded-[2.75rem] bg-[#191314] p-2 shadow-[0_40px_80px_-30px_rgba(25,19,20,0.55)]',
        className,
      )}
    >
      <div className="overflow-hidden rounded-[2.25rem] bg-white">
        <div
          className="relative flex h-7 items-center justify-between px-6 text-[10px] font-semibold text-[#191314]"
          aria-hidden
        >
          <span>9:41</span>
          <span className="absolute top-1.5 left-1/2 h-4 w-[30%] -translate-x-1/2 rounded-full bg-[#191314]" />
          <span className="flex items-center gap-1">
            <svg viewBox="0 0 16 10" className="h-2.5 w-3.5 fill-current">
              <rect x="0" y="6" width="3" height="4" rx="0.5" />
              <rect x="4.3" y="4" width="3" height="6" rx="0.5" />
              <rect x="8.6" y="2" width="3" height="8" rx="0.5" />
              <rect x="12.9" y="0" width="3" height="10" rx="0.5" />
            </svg>
            <svg viewBox="0 0 22 11" className="h-2.5 w-5">
              <rect
                x="0.5"
                y="0.5"
                width="18"
                height="10"
                rx="2.5"
                className="fill-none stroke-current"
              />
              <rect
                x="2"
                y="2"
                width="12"
                height="7"
                rx="1.2"
                className="fill-current"
              />
              <rect
                x="19.5"
                y="3.5"
                width="1.6"
                height="4"
                rx="0.8"
                className="fill-current"
              />
            </svg>
          </span>
        </div>
        {/* Pre-compressed WebP from public/, so there is nothing for an optimiser to do. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt}
          width={780}
          height={1688}
          loading={eager ? 'eager' : 'lazy'}
          decoding="async"
          className="block h-auto w-full"
        />
      </div>
    </div>
  );
}

/** A browser window around control room screenshots (1440 x 900). */
export function BrowserFrame({
  address,
  className,
  children,
}: {
  address: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        'overflow-hidden rounded-[1.25rem] border border-[#191314]/10 bg-white shadow-[0_40px_80px_-40px_rgba(25,19,20,0.4)]',
        className,
      )}
    >
      <div className="flex items-center gap-3 border-b border-[#191314]/10 px-4 py-3">
        <span className="flex gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-[#191314]/15" />
          <span className="size-2.5 rounded-full bg-[#191314]/15" />
          <span className="size-2.5 rounded-full bg-[#191314]/15" />
        </span>
        <span className="mx-auto truncate rounded-md bg-[#f4f4f4] px-4 py-1 font-(family-name:--font-landing-mono) text-[11px] text-[#191314]/60">
          {address}
        </span>
        <span className="w-[42px]" aria-hidden />
      </div>
      {children}
    </div>
  );
}
