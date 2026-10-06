import {
  ArrowDown,
  ArrowUpRight,
  BellRing,
  BrainCircuit,
  Camera,
  Download,
  Languages,
  LayoutDashboard,
  Map as MapIcon,
  Minus,
  Play,
  Plus,
  RadioTower,
  Route,
  Truck,
  type LucideIcon,
} from 'lucide-react';
import { Martian_Mono, Ubuntu_Sans } from 'next/font/google';
import Link from 'next/link';

import { cn } from '@/lib/utils';

import { ControlRoomShowcase } from './control-room-showcase';
import { DemoVideo } from './demo-video';
import { PhoneFrame } from './device-frames';
import { APK_URL, CONTROL_ROOM_PATH, SOURCE_URL, VIDEO_URL } from './links';

const mono = Martian_Mono({
  subsets: ['latin'],
  variable: '--font-landing-mono',
});

const display = Ubuntu_Sans({
  subsets: ['latin'],
  variable: '--font-landing-display',
});

const focusRing =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#191314]';

const NAV = [
  { href: '#demo', label: 'Demo' },
  { href: '#how-it-works', label: 'How it works' },
  { href: '#features', label: 'Features' },
  { href: '#control-room', label: 'Control room' },
  { href: '#faq', label: 'FAQ' },
];

const EVENTS = [
  {
    when: 'May 2022',
    where: 'Dima Hasao, Assam',
    what: 'Over 5,000 landslides in a single week cut the district’s road and rail links.',
  },
  {
    when: 'Sept 2024',
    where: 'Mizoram',
    what: 'Damaged stretches of NH-6 and NH-306 stopped trucks and left the state short of fuel.',
  },
  {
    when: 'Aug 2025',
    where: 'NH-10, Sikkim',
    what: 'The highway linking Sikkim to the rest of India stayed shut for six days.',
  },
];

const HAZARDS = [
  'Landslide or slurry',
  'Boulder fall',
  'Bridge overwash',
  'Road sunk or cracked',
  'Flash flood',
  'Blocked by traffic',
];

const FEATURES: ReadonlyArray<{
  requirement: string;
  title: string;
  text: string;
  icon: LucideIcon;
}> = [
  {
    requirement: 'a',
    title: 'Accessibility map',
    text: 'Every road marked open, restricted, closed or unknown, with where the status came from and how old it is.',
    icon: MapIcon,
  },
  {
    requirement: 'b',
    title: 'Disruption prediction',
    text: 'Each road is scored from IMD rainfall, NDMA SACHET warnings, past incidents, terrain slope and tracked vehicle speeds, and the score shows why.',
    icon: BrainCircuit,
  },
  {
    requirement: 'c',
    title: 'Alternate routes',
    text: 'Up to three routes that avoid closures and bridges the vehicle cannot cross, and weigh road risk, with ETA ranges.',
    icon: Route,
  },
  {
    requirement: 'd',
    title: 'GPS tracking',
    text: 'Vehicles carrying medicines, food and materials tracked trip by trip, even through dead zones.',
    icon: Truck,
  },
  {
    requirement: 'e',
    title: 'Automatic alerts',
    text: 'Closures, cut-off facilities, withdrawn routes and delivery shortfalls reach the right people at once.',
    icon: BellRing,
  },
  {
    requirement: 'f',
    title: 'Geo-tagged reports',
    text: 'Photo, GPS position, accuracy and time, checked by a dispatcher before it counts as evidence.',
    icon: Camera,
  },
  {
    requirement: 'g',
    title: 'Central dashboards',
    text: 'District connectivity, supply gaps, fleet positions and live deliveries on one screen.',
    icon: LayoutDashboard,
  },
  {
    requirement: 'h',
    title: 'Languages and offline',
    text: 'The control room and offline web app speak English and all 22 scheduled languages. Reports and routes keep working without network.',
    icon: Languages,
  },
];

const STEPS = [
  {
    title: 'Predict',
    text: 'High-risk roads are flagged ahead of time from rainfall forecasts, disaster warnings and terrain slope.',
  },
  {
    title: 'Report',
    text: 'A field officer photographs the blockage. GPS and time are saved, even with no signal.',
  },
  {
    title: 'Verify',
    text: 'A dispatcher checks the evidence. Only a confirmed report closes the road.',
  },
  {
    title: 'Replan',
    text: 'Affected trips are withdrawn and the dispatcher approves a safe alternate route.',
  },
  {
    title: 'Track',
    text: 'The driver accepts the new route and the vehicle is tracked to its destination.',
  },
  {
    title: 'Deliver',
    text: 'The facility records what arrived, and any shortfall is flagged.',
  },
];

const FAQS = [
  {
    q: 'Who is RASTA for?',
    a: 'District dispatchers and state coordinators work in the web control room. Field officers and drivers use the Android app.',
  },
  {
    q: 'Does it work without mobile network?',
    a: 'Yes. Reports, GPS positions and approved routes are stored on the phone and sent automatically when the network returns. Map data can be downloaded in advance.',
  },
  {
    q: 'Can the AI close a road by itself?',
    a: 'No. Forecasts only raise a road’s risk level. A road closes only after a dispatcher verifies field evidence, and an official approves every route.',
  },
  {
    q: 'Where does the data come from?',
    a: 'IMD rainfall forecasts, NDMA SACHET disaster alerts, OpenStreetMap roads, SRTM terrain elevation and verified field reports. Every value on screen shows its source and how old it is.',
  },
  {
    q: 'Which languages are supported?',
    a: 'The control room and the offline web app are available in English and all 22 languages of the Eighth Schedule, including Assamese, Bengali, Bodo, Manipuri and Nepali.',
  },
  {
    q: 'How do officials get access?',
    a: 'Download the Android app from this page, or open the control room in a browser. Accounts are issued by the district administration with one role and one district. There is no public sign-up.',
  },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-lg border border-[#191314] px-3 py-1.5 text-xs">
      {children}
    </span>
  );
}

function SectionHeading({
  label,
  title,
  text,
}: {
  label: string;
  title: string;
  text?: string;
}) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <SectionLabel>{label}</SectionLabel>
      <h2 className="mt-5 font-(family-name:--font-landing-display) text-[clamp(1.9rem,4vw,2.9rem)] leading-[1.08] font-bold tracking-[-0.02em] text-balance">
        {title}
      </h2>
      {text ? (
        <p className="mx-auto mt-5 max-w-lg text-[13px] leading-6 text-[#191314]/70">
          {text}
        </p>
      ) : null}
    </div>
  );
}

function DownloadButton({
  variant = 'accent',
  className,
}: {
  variant?: 'accent' | 'dark' | 'light';
  className?: string;
}) {
  return (
    <a
      href={APK_URL}
      download
      className={cn(
        'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-[13px] font-medium transition-colors',
        focusRing,
        variant === 'accent' &&
          'bg-[#7AA2F7] text-[#191314] hover:bg-[#6690ec]',
        variant === 'dark' && 'bg-[#191314] text-white hover:bg-[#2c2527]',
        variant === 'light' && 'bg-white text-[#191314] hover:bg-white/80',
        className,
      )}
    >
      Download for Android
      <Download className="size-4" aria-hidden />
    </a>
  );
}

function ControlRoomButton({
  variant = 'outline',
  className,
}: {
  variant?: 'outline' | 'light';
  className?: string;
}) {
  return (
    <Link
      href={CONTROL_ROOM_PATH}
      className={cn(
        'inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-[13px] font-medium transition-colors',
        focusRing,
        variant === 'outline' &&
          'border border-[#191314]/20 bg-white text-[#191314] hover:border-[#191314]',
        variant === 'light' && 'bg-white text-[#191314] hover:bg-white/80',
        className,
      )}
    >
      Open control room
      <ArrowUpRight className="size-4" aria-hidden />
    </Link>
  );
}

function WatchDemoButton() {
  return (
    <a
      href="#demo"
      className={cn(
        'inline-flex items-center gap-2 rounded-lg bg-[#191314] px-4 py-2.5 text-[13px] font-medium text-white transition-colors hover:bg-[#2c2527]',
        focusRing,
      )}
    >
      Watch the demo
      <Play className="size-4 fill-current" aria-hidden />
    </a>
  );
}

function Logo() {
  return (
    <span className="flex items-center gap-2">
      <RadioTower
        className="size-6 text-[#5b86e5]"
        strokeWidth={2.25}
        aria-hidden
      />
      <span className="font-(family-name:--font-landing-display) text-lg font-bold tracking-tight">
        RASTA
      </span>
    </span>
  );
}

/** Contour lines of a hillside, the terrain every route in the region has to cross. */
function Contours({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 600 600"
      fill="none"
      className={className}
      aria-hidden
      preserveAspectRatio="xMidYMid slice"
    >
      <g stroke="currentColor" strokeWidth="1.2">
        <path d="M-20 520C80 470 140 500 230 450S360 330 470 350 600 300 640 270" />
        <path d="M-20 470C70 430 150 450 225 400S350 280 455 300 590 250 640 215" />
        <path d="M-20 420C60 385 150 400 220 350S340 230 440 250 580 200 640 160" />
        <path d="M-20 370C55 340 145 350 212 300S330 180 425 200 570 150 640 105" />
        <path d="M-20 320C50 295 140 300 205 250S320 130 410 150 560 100 640 50" />
        <path d="M-20 270C45 250 135 250 198 200S312 85 395 100 550 50 640 -5" />
        <path d="M300 160C330 140 380 145 395 170S370 215 335 212 280 185 300 160Z" />
        <path d="M320 168C335 158 362 161 369 175S356 196 338 195 310 182 320 168Z" />
      </g>
      <path
        d="M40 590C120 520 110 430 200 410S330 420 360 330 470 220 560 200"
        stroke="#191314"
        strokeWidth="3"
        strokeDasharray="2 10"
        strokeLinecap="round"
        opacity="0.55"
      />
    </svg>
  );
}

/** The ring of text that sits in the notch between the two hero cards. */
function ScrollBadge() {
  return (
    <a
      href="#problem"
      aria-label="Scroll to the problem RASTA solves"
      className={cn(
        'group relative grid size-32 place-items-center rounded-full bg-[#f4f4f4]',
        focusRing,
      )}
    >
      <svg
        viewBox="0 0 120 120"
        className="absolute inset-2 animate-[spin_24s_linear_infinite] motion-reduce:animate-none"
        aria-hidden
      >
        <defs>
          <path
            id="badge-ring"
            d="M60 60m-44 0a44 44 0 1 1 88 0a44 44 0 1 1-88 0"
          />
        </defs>
        <text className="fill-[#191314] text-[10px]">
          <textPath href="#badge-ring" textLength="272" lengthAdjust="spacing">
            Report • Verify • Reroute • Deliver •
          </textPath>
        </text>
      </svg>
      <ArrowDown
        className="size-5 transition-transform group-hover:translate-y-0.5"
        aria-hidden
      />
    </a>
  );
}

function Hero() {
  return (
    <section>
      <div className="relative grid gap-4 lg:grid-cols-[1fr_1.05fr]">
        <div className="flex flex-col justify-between rounded-[2rem] bg-white p-7 sm:p-10 lg:min-h-[560px] lg:p-12">
          <div>
            <span className="inline-block rounded-lg bg-[#f4f4f4] px-3 py-1.5 text-[11px] text-[#191314]/70">
              Team Side Quest, Team ID 127269
            </span>
            <h1 className="mt-7 font-(family-name:--font-landing-display) text-[clamp(2.7rem,6.2vw,4.9rem)] leading-[0.98] font-bold tracking-[-0.03em]">
              Roads fail.
              <br />
              Supplies
              <br />
              shouldn’t.
            </h1>
            <p className="mt-7 max-w-sm text-[13px] leading-6 text-[#191314]/75">
              Road accessibility and supply tracking for India’s North Eastern
              Region. See which roads are open, reroute essential goods, and
              know every delivery arrived.
            </p>
          </div>
          <div className="mt-10 flex flex-wrap items-center gap-3">
            <DownloadButton />
            <ControlRoomButton />
            <WatchDemoButton />
          </div>
        </div>

        <div className="relative min-h-[520px] overflow-hidden rounded-[2rem] bg-[#7AA2F7] lg:min-h-[560px]">
          <Contours className="absolute inset-0 size-full text-white/35" />
          <div className="absolute top-6 left-5 z-10 w-[136px] rounded-2xl bg-white/80 p-4 shadow-[0_20px_40px_-24px_rgba(25,19,20,0.5)] backdrop-blur-md sm:top-8 sm:left-8 sm:w-[200px]">
            <p className="text-[11px] text-[#191314]/60">Approved route</p>
            <p className="mt-1.5 font-(family-name:--font-landing-display) text-2xl font-bold tracking-tight sm:text-3xl">
              4.6 km
            </p>
            <p className="mt-1 text-[11px] text-[#191314]/70">
              5 to 11 min, clear of closures
            </p>
          </div>
          <div className="absolute top-[48%] left-8 z-10 hidden items-center gap-2.5 rounded-xl bg-[#191314] px-3.5 py-2.5 text-white shadow-lg sm:flex">
            <span className="size-2 rounded-full bg-[#ff6b6b]" aria-hidden />
            <span className="text-[11px] leading-4">
              NH-6 closed
              <span className="block text-white/60">
                verified by dispatcher
              </span>
            </span>
          </div>
          <PhoneFrame
            src="/landing/app-observer-capture.webp"
            alt="RASTA field app: report a landslide, boulder fall or flash flood with a photo and GPS position"
            eager
            className="absolute top-24 right-[6%] w-[54%] max-w-[300px] sm:top-20 sm:right-[9%] sm:w-[47%]"
          />
        </div>

        <div className="absolute bottom-4 left-[calc(48.8%-4rem)] hidden lg:block">
          <ScrollBadge />
        </div>
      </div>

      <div
        id="demo"
        className="mt-4 scroll-mt-24 rounded-[2rem] bg-white p-4 sm:p-6 lg:p-8"
      >
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <SectionLabel>3-minute walkthrough</SectionLabel>
            <h2 className="mt-4 font-(family-name:--font-landing-display) text-[clamp(1.6rem,3.2vw,2.4rem)] leading-[1.08] font-bold tracking-[-0.02em] text-balance">
              One landslide, three people, end to end.
            </h2>
            <p className="mt-3 max-w-xl text-[13px] leading-6 text-[#191314]/70">
              A field officer reports a blocked road, a dispatcher verifies it
              and approves a new route, and the driver delivers.
            </p>
          </div>
          <a
            href={VIDEO_URL}
            target="_blank"
            rel="noreferrer"
            className={cn(
              'inline-flex items-center gap-2 self-start rounded-lg border border-[#191314]/20 px-4 py-2.5 text-[13px] font-medium whitespace-nowrap transition-colors hover:border-[#191314] sm:self-auto',
              focusRing,
            )}
          >
            Watch on YouTube
            <ArrowUpRight className="size-4" aria-hidden />
          </a>
        </div>
        <DemoVideo className="mt-6" />
      </div>
    </section>
  );
}

function Problem() {
  return (
    <section id="problem" className="scroll-mt-24 pt-28">
      <SectionHeading
        label="The problem"
        title="Every monsoon, the North East’s roads give way"
        text="Landslides, floods and heavy rain cut roads across the region, and officials still find out through phone calls. There is no single view of which roads are open, which deliveries are stuck and which route is still safe."
      />
      <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <div className="flex flex-col justify-between rounded-3xl bg-[#191314] p-7 text-white">
          <p className="font-(family-name:--font-landing-display) text-6xl font-bold tracking-tight text-[#7AA2F7]">
            18.8%
          </p>
          <div className="mt-10">
            <p className="text-[13px] leading-6">
              of India’s mapped landslides are in the North East Himalaya.
            </p>
            <p className="mt-3 text-[11px] text-white/50">
              NRSC Landslide Atlas of India, 2023
            </p>
          </div>
        </div>
        {EVENTS.map((event) => (
          <div key={event.where} className="rounded-3xl bg-white p-7">
            <p className="text-[11px] text-[#191314]/55">{event.when}</p>
            <p className="mt-2 font-(family-name:--font-landing-display) text-xl font-bold">
              {event.where}
            </p>
            <p className="mt-8 text-[13px] leading-6 text-[#191314]/75">
              {event.what}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Evidence() {
  return (
    <section className="grid items-center gap-10 pt-28 lg:grid-cols-2 lg:gap-16">
      <div className="relative h-[460px] overflow-hidden rounded-[2rem] bg-[#e4e4e4] sm:h-[520px]">
        <PhoneFrame
          src="/landing/pwa-field-report.webp"
          alt="RASTA field report in a phone browser, saved on the device as it is typed"
          className="absolute top-12 left-1/2 w-[62%] max-w-[290px] -translate-x-1/2"
        />
      </div>
      <div className="max-w-md">
        <SectionLabel>Field reports</SectionLabel>
        <h2 className="mt-5 font-(family-name:--font-landing-display) text-[clamp(1.9rem,4vw,2.9rem)] leading-[1.08] font-bold tracking-[-0.02em]">
          Evidence before action
        </h2>
        <p className="mt-5 text-[13px] leading-6 text-[#191314]/75">
          A field officer photographs the blockage and picks what is blocking
          the road. The GPS position, its accuracy and the time are saved on the
          phone, even with no signal, and sent as soon as there is network. A
          dispatcher checks every photo before a road changes status.
        </p>
        <ul
          className="mt-7 flex flex-wrap gap-2"
          aria-label="Hazards a field officer can report"
        >
          {HAZARDS.map((hazard) => (
            <li
              key={hazard}
              className="rounded-lg border border-[#191314]/15 bg-white px-3 py-1.5 text-xs"
            >
              {hazard}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="scroll-mt-24 pt-28">
      <SectionHeading
        label="Features"
        title="Everything a district needs when a road closes"
        text="Each feature answers one of the eight requirements, (a) to (h), in problem statement SIH26002 from the Ministry of Development of North Eastern Region."
      />
      <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {FEATURES.map((feature) => {
          const Icon = feature.icon;
          return (
            <article
              key={feature.requirement}
              className={cn(
                'flex min-h-[260px] flex-col rounded-3xl bg-white p-6',
              )}
            >
              <div className="flex items-start justify-between">
                <span className="grid size-11 place-items-center rounded-xl bg-[#7AA2F7]">
                  <Icon className="size-5" aria-hidden />
                </span>
                <span
                  className="text-[11px] text-[#191314]/50"
                  title={`Requirement (${feature.requirement}) of SIH26002`}
                >
                  ({feature.requirement})
                </span>
              </div>
              <h3 className="mt-14 font-(family-name:--font-landing-display) text-xl leading-tight font-bold">
                {feature.title}
              </h3>
              <p className="mt-3 text-xs leading-5 text-[#191314]/65">
                {feature.text}
              </p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-24 pt-28">
      <SectionHeading
        label="How it works"
        title="From a blocked road to a safe new route"
      />
      <ol className="mt-14 grid gap-px overflow-hidden rounded-3xl bg-[#191314]/10 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {STEPS.map((step, index) => (
          <li key={step.title} className="bg-white p-6">
            <span
              className={cn(
                'grid size-8 place-items-center rounded-lg text-[11px]',
                index === 0 ? 'bg-[#7AA2F7]' : 'bg-[#191314] text-white',
              )}
            >
              {String(index + 1).padStart(2, '0')}
            </span>
            <h3 className="mt-8 font-(family-name:--font-landing-display) text-lg font-bold">
              {step.title}
            </h3>
            <p className="mt-2 text-xs leading-5 text-[#191314]/65">
              {step.text}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ControlRoom() {
  return (
    <section id="control-room" className="scroll-mt-24 pt-28">
      <SectionHeading
        label="Control room"
        title="One screen for the whole district"
        text="Dispatchers review reports, approve routes and follow every delivery from a browser. No installation, no special hardware."
      />
      <div className="mt-10">
        <ControlRoomShowcase />
      </div>
      <div className="mt-8 flex justify-center">
        <ControlRoomButton />
      </div>
    </section>
  );
}

function MobileApp() {
  const left = [
    {
      title: 'For drivers',
      text: 'The approved route, the load on board and one tap to record what was delivered.',
    },
    {
      title: 'Works offline',
      text: 'Reports, GPS positions and routes are kept on the phone until the network returns.',
    },
  ];
  const right = [
    {
      title: 'For field officers',
      text: 'Report a landslide, flash flood or damaged bridge in seconds, with photo and GPS.',
    },
    {
      title: 'SOS in one tap',
      text: 'Call 112, 108 or the state disaster helpline 1070, or text 112 your exact position.',
    },
  ];

  return (
    <section id="app" className="scroll-mt-24 pt-28">
      <SectionHeading
        label="Android app"
        title="In the field, on any Android phone"
      />
      <div className="mt-14 grid items-center gap-10 lg:grid-cols-[1fr_1.3fr_1fr]">
        <div className="space-y-10 lg:text-right">
          {left.map((item) => (
            <div key={item.title}>
              <h3 className="font-(family-name:--font-landing-display) text-xl font-bold">
                {item.title}
              </h3>
              <p className="mt-2 text-xs leading-5 text-[#191314]/65">
                {item.text}
              </p>
            </div>
          ))}
        </div>
        <div className="mx-auto flex w-full max-w-[400px] items-start justify-center pb-6">
          <PhoneFrame
            src="/landing/app-driver-load.webp"
            alt="RASTA driver app: position reporting and delivery for the current load"
            className="mt-12 w-1/2 -rotate-[5deg]"
          />
          <PhoneFrame
            src="/landing/app-sos.webp"
            alt="RASTA SOS screen with one-tap calls to 112, 108 and 1070"
            className="relative -ml-[6%] w-1/2 rotate-[4deg]"
          />
        </div>
        <div className="space-y-10">
          {right.map((item) => (
            <div key={item.title}>
              <h3 className="font-(family-name:--font-landing-display) text-xl font-bold">
                {item.title}
              </h3>
              <p className="mt-2 text-xs leading-5 text-[#191314]/65">
                {item.text}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div className="mt-10 flex justify-center">
        <DownloadButton variant="dark" />
      </div>
    </section>
  );
}

function Faq() {
  return (
    <section id="faq" className="scroll-mt-24 pt-28">
      <SectionHeading label="FAQ" title="Frequently asked questions" />
      <div className="mx-auto mt-12 max-w-4xl divide-y divide-[#191314]/10 border-y border-[#191314]/10">
        {FAQS.map((item, index) => (
          <details key={item.q} className="group" open={index === 0}>
            <summary
              className={cn(
                'grid cursor-pointer list-none grid-cols-[2.5rem_1fr_auto] items-center gap-4 py-5 [&::-webkit-details-marker]:hidden',
                focusRing,
              )}
            >
              <span className="grid size-7 place-items-center rounded-md text-[11px] text-[#191314]/60 group-open:bg-[#191314] group-open:text-white">
                {String(index + 1).padStart(2, '0')}
              </span>
              <span className="font-(family-name:--font-landing-display) text-base font-bold sm:text-lg">
                {item.q}
              </span>
              <span className="grid size-8 place-items-center rounded-lg bg-[#7AA2F7]">
                <Plus className="size-4 group-open:hidden" aria-hidden />
                <Minus className="hidden size-4 group-open:block" aria-hidden />
              </span>
            </summary>
            <p className="max-w-2xl pb-6 pl-14 text-xs leading-6 text-[#191314]/70">
              {item.a}
            </p>
          </details>
        ))}
      </div>
    </section>
  );
}

function GetStarted() {
  return (
    <section className="pt-28">
      <div className="grid overflow-hidden rounded-[2rem] bg-white lg:grid-cols-[0.9fr_1.1fr]">
        <div className="p-8 sm:p-12">
          <SectionLabel>Get started</SectionLabel>
          <h2 className="mt-5 font-(family-name:--font-landing-display) text-[clamp(1.9rem,3.6vw,2.6rem)] leading-[1.08] font-bold tracking-[-0.02em]">
            Keep supplies moving across the North East
          </h2>
          <p className="mt-5 max-w-sm text-[13px] leading-6 text-[#191314]/70">
            Install the app on your phone for field work, or sign in to the
            control room with your district account.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <DownloadButton />
            <ControlRoomButton />
          </div>
        </div>
        <div className="relative min-h-[300px] bg-[#7AA2F7] bg-[linear-gradient(to_right,rgba(25,19,20,0.08)_1px,transparent_1px),linear-gradient(to_bottom,rgba(25,19,20,0.08)_1px,transparent_1px)] bg-size-[44px_44px]">
          <svg
            viewBox="0 0 520 300"
            className="absolute inset-0 size-full"
            preserveAspectRatio="xMidYMid meet"
            aria-hidden
          >
            <path
              d="M40 250C110 250 120 150 190 150"
              stroke="#191314"
              strokeWidth="2.5"
              fill="none"
              strokeDasharray="3 8"
              strokeLinecap="round"
              opacity="0.45"
            />
            <path
              d="M190 150C250 150 250 70 320 70S420 190 480 170"
              stroke="#191314"
              strokeWidth="2.5"
              fill="none"
              strokeLinecap="round"
            />
            <path
              d="M190 150C240 170 270 230 330 230"
              stroke="#191314"
              strokeWidth="2"
              fill="none"
              strokeDasharray="1 7"
              strokeLinecap="round"
              opacity="0.35"
            />
            <g>
              <circle cx="330" cy="230" r="13" fill="#191314" />
              <path
                d="M325 225l10 10M335 225l-10 10"
                stroke="#ff6b6b"
                strokeWidth="2.2"
                strokeLinecap="round"
              />
            </g>
            <g>
              <circle
                cx="190"
                cy="150"
                r="13"
                fill="white"
                stroke="#191314"
                strokeWidth="2"
              />
              <circle cx="190" cy="150" r="4" fill="#191314" />
            </g>
            <g>
              <circle cx="480" cy="170" r="13" fill="#191314" />
              <path
                d="M474 170l4 4 8-8"
                stroke="#7AA2F7"
                strokeWidth="2.2"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </g>
          </svg>
          <div className="absolute top-[38%] left-[30%] hidden rounded-md bg-white px-2 py-1 text-[10px] sm:block">
            Rerouted
          </div>
          <div className="absolute top-[82%] left-[58%] hidden rounded-md bg-[#191314] px-2 py-1 text-[10px] text-white sm:block">
            Closed
          </div>
          <div className="absolute top-[64%] right-[4%] hidden rounded-md bg-white px-2 py-1 text-[10px] sm:block">
            Delivered
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="mt-20 border-t border-[#191314]/10 py-10">
      <div className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
        <div className="max-w-sm">
          <Logo />
          <p className="mt-4 text-xs leading-5 text-[#191314]/60">
            Road Accessibility & Supply Tracking Assistant.
          </p>
          <p className="mt-4 text-[13px] leading-6 font-medium text-[#191314]">
            Built by Team Side Quest
            <br />
            Team ID 127269
          </p>
        </div>
        <nav
          aria-label="Footer"
          className="flex flex-wrap gap-x-8 gap-y-3 text-xs"
        >
          <a href={SOURCE_URL} className={cn('hover:underline', focusRing)}>
            Source code
          </a>
          <a href={VIDEO_URL} className={cn('hover:underline', focusRing)}>
            Demo video
          </a>
          <Link
            href={CONTROL_ROOM_PATH}
            className={cn('hover:underline', focusRing)}
          >
            Control room
          </Link>
          <a
            href={APK_URL}
            download
            className={cn('hover:underline', focusRing)}
          >
            Android app
          </a>
        </nav>
      </div>
      <p className="mt-10 text-[11px] leading-5 text-[#191314]/45">
        Map data © OpenStreetMap contributors.
      </p>
    </footer>
  );
}

export function LandingPage() {
  return (
    <div
      className={cn(
        mono.variable,
        display.variable,
        'min-h-dvh bg-[#f4f4f4] font-(family-name:--font-landing-mono) text-[#191314] selection:bg-[#7AA2F7]',
      )}
    >
      <header className="sticky top-0 z-30 bg-[#f4f4f4]/85 backdrop-blur-md">
        <div className="mx-auto grid max-w-[1240px] grid-cols-[1fr_auto] items-center gap-6 px-4 py-4 sm:px-6 md:grid-cols-[1fr_auto_1fr]">
          <Link
            href="/"
            className={cn('justify-self-start rounded-lg', focusRing)}
            aria-label="RASTA home"
          >
            <Logo />
          </Link>
          <nav
            aria-label="Main"
            className="hidden items-center gap-8 text-[13px] md:flex"
          >
            {NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className={cn('rounded hover:underline', focusRing)}
              >
                {item.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2 justify-self-end">
            <Link
              href={CONTROL_ROOM_PATH}
              className={cn(
                'hidden rounded-lg px-3.5 py-2.5 text-[13px] hover:bg-white sm:inline-flex',
                focusRing,
              )}
            >
              Sign in
            </Link>
            <a
              href={APK_URL}
              download
              className={cn(
                'inline-flex items-center gap-2 rounded-lg bg-[#191314] px-4 py-2.5 text-[13px] text-white hover:bg-[#2c2527]',
                focusRing,
              )}
            >
              Download app
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1240px] px-4 pt-2 sm:px-6">
        <Hero />
        <Problem />
        <Evidence />
        <Features />
        <HowItWorks />
        <ControlRoom />
        <MobileApp />
        <Faq />
        <GetStarted />
        <Footer />
      </main>
    </div>
  );
}
