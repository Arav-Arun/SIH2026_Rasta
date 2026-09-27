import type { Metadata, Viewport } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';

import { AuthProvider } from '@/components/auth/auth-provider';
import { LocaleProvider } from '@/components/i18n/locale-provider';
import { SyncRunner } from '@/components/offline/sync-runner';
import { ServiceWorkerProvider } from '@/components/providers/service-worker-provider';
import { QueryProvider } from '@/components/providers/query-provider';

import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'RASTA | Logistics accessibility intelligence',
  description:
    'A transparent logistics accessibility demonstrator for the North Eastern Region.',
  manifest: '/manifest.webmanifest',
  applicationName: 'RASTA',
  appleWebApp: { capable: true, title: 'RASTA', statusBarStyle: 'default' },
  icons: {
    icon: [
      { url: '/favicon.svg', type: 'image/svg+xml' },
      { url: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
    ],
    apple: [{ url: '/icons/icon-192.png', sizes: '192x192' }],
  },
};

export const viewport: Viewport = {
  themeColor: '#FFFFFF',
  // Zoom is left alone deliberately: a dispatcher reading a map in daylight or a
  // driver with a cracked screen may need to pinch, and locking it out is the
  // most common accessibility regression a mobile-first layout introduces.
  initialScale: 1,
  width: 'device-width',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <LocaleProvider>
          <AuthProvider>
            <QueryProvider>
              {/* Drains the offline outbox whenever a session and a network
                  exist. Renders nothing; it only needs to be mounted. */}
              <SyncRunner />
              {/* Registers the service worker and holds a pending update until
                  nothing is waiting to be sent. */}
              <ServiceWorkerProvider>{children}</ServiceWorkerProvider>
            </QueryProvider>
          </AuthProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
