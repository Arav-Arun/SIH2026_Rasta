'use client';

import Link from 'next/link';
import { useMemo, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  BellRing,
  CircleGauge,
  ClipboardCheck,
  ClipboardList,
  FilePlus2,
  FileWarning,
  ListChecks,
  LogIn,
  MapPinned,
  PackageCheck,
  RadioTower,
  Route,
  Settings,
  Smartphone,
  Truck,
} from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { decideRouteAccess } from '@/lib/auth/policy';
import type { WorkspaceIdentity } from '@/lib/auth/policy';
import { LanguageSwitcher } from '@/components/i18n/language-switcher';
import { useLocale } from '@/components/i18n/locale-provider';
import { OfflineBanner } from '@/components/common/offline-banner';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar';

/** Which entries this profile may actually open. */
function visibleTo(
  identity: WorkspaceIdentity | null,
  entries: readonly { key: string; href: string; icon: LucideIcon }[],
) {
  if (!identity) return [];
  return entries.filter(
    (entry) => decideRouteAccess(identity, { path: entry.href }).allowed,
  );
}

const navigation = [
  { key: 'nav.overview', href: '/overview', icon: CircleGauge },
  { key: 'nav.map', href: '/map', icon: MapPinned },
  { key: 'nav.planner', href: '/planner', icon: Route },
  { key: 'nav.deliveries', href: '/deliveries', icon: PackageCheck },
  { key: 'nav.fleet', href: '/fleet', icon: Truck },
  { key: 'nav.incidents', href: '/incidents', icon: FileWarning },
  { key: 'nav.inspections', href: '/inspections', icon: ClipboardList },
  { key: 'nav.alerts', href: '/alerts', icon: BellRing },
  { key: 'nav.dataHealth', href: '/data-health', icon: Activity },
  { key: 'nav.settings', href: '/settings', icon: Settings },
] as const;

/** Work done away from a desk, or queued on this device until it can be sent. */
const fieldWork = [
  { key: 'nav.fieldHome', href: '/field/home', icon: ClipboardCheck },
  { key: 'nav.newReport', href: '/field/report', icon: FilePlus2 },
  { key: 'nav.myTrips', href: '/driver/trip', icon: Truck },
  { key: 'nav.syncQueue', href: '/sync', icon: ListChecks },
  { key: 'nav.permissions', href: '/permissions', icon: Smartphone },
] as const;

type CommandShellProps = {
  /** Current route used to highlight the navigation entry. */
  activeHref: string;
  title: string;
  /** Extra header controls (freshness label, filters). */
  headerExtra?: ReactNode;
  /** Whether the content area should stretch to the viewport (map screens). */
  fill?: boolean;
  /**
   * The screen renders its own level-one heading in the content. Otherwise the
   * header's title is the page's `<h1>`, so every screen has exactly one.
   */
  ownHeading?: boolean;
  children: ReactNode;
};

/** Desktop control-room frame: left navigation, 64 px header, page gutters. */
export function CommandShell({
  activeHref,
  title,
  headerExtra,
  fill = false,
  ownHeading = false,
  children,
}: CommandShellProps) {
  const TitleTag = ownHeading ? 'p' : 'h1';
  const { t, dir } = useLocale();
  const { workspace } = useAuth();

  const identity = workspace?.identity ?? null;
  const allowedNavigation = useMemo(
    () => visibleTo(identity, navigation),
    [identity],
  );
  const allowedFieldWork = useMemo(
    () => visibleTo(identity, fieldWork),
    [identity],
  );

  return (
    <SidebarProvider defaultOpen>
      {/* The first thing the keyboard reaches. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:start-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:outline-2 focus:outline-offset-2 focus:outline-primary"
      >
        {t('app.skipToContent')}
      </a>
      {/* Navigation sits on the side a reader starts from: right in Urdu and
       * Kashmiri, left otherwise. */}
      <Sidebar collapsible="icon" side={dir === 'rtl' ? 'right' : 'left'}>
        <SidebarHeader className="flex-row items-center justify-between gap-2 p-3 group-data-[collapsible=icon]:justify-center">
          <Link
            className="flex items-center gap-2 rounded-md px-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary group-data-[collapsible=icon]:hidden"
            href="/overview"
          >
            <RadioTower className="size-6 shrink-0 text-primary" aria-hidden />
            <span className="text-2xl font-bold tracking-tight">
              {t('app.name')}
            </span>
          </Link>
          <SidebarTrigger
            className="hidden md:inline-flex"
            label={t('nav.toggleSidebar')}
            title={t('nav.toggleSidebar')}
          />
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>{t('nav.operations')}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {allowedNavigation.map((item) => {
                  const Icon = item.icon;
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={item.href === activeHref}
                        render={<Link href={item.href} />}
                        tooltip={t(item.key)}
                      >
                        <Icon />
                        <span>{t(item.key)}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>

          <SidebarGroup hidden={allowedFieldWork.length === 0}>
            <SidebarGroupLabel>{t('nav.fieldWork')}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {allowedFieldWork.map((item) => {
                  const Icon = item.icon;
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={item.href === activeHref}
                        render={<Link href={item.href} />}
                        tooltip={t(item.key)}
                      >
                        <Icon />
                        <span>{t(item.key)}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter className="p-3">
          <Link
            href="/sign-in"
            className="flex items-center gap-2 rounded-lg border bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-2"
          >
            <LogIn className="size-4 shrink-0" />
            <span className="truncate group-data-[collapsible=icon]:hidden">
              {workspace?.displayName ?? t('nav.signIn')}
            </span>
          </Link>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset className={fill ? 'flex min-h-svh flex-col' : undefined}>
        <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b bg-white px-4 py-2 md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <SidebarTrigger
              className="md:hidden"
              label={t('nav.toggleSidebar')}
              title={t('nav.toggleSidebar')}
            />
            <TitleTag className="truncate text-sm font-medium md:text-base">
              {title}
            </TitleTag>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {headerExtra}
            <LanguageSwitcher />
          </div>
        </header>
        <main
          id="main-content"
          // Focusable so the skip link actually moves the caret here, rather
          // than only scrolling and leaving focus behind in the sidebar.
          tabIndex={-1}
          className={
            fill
              ? 'flex min-h-0 flex-1 flex-col'
              : 'mx-auto w-full max-w-[1600px] p-4 md:p-6'
          }
        >
          <OfflineBanner className={fill ? 'mx-4 mt-4' : 'mb-4'} />
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}
