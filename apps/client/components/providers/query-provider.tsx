'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { ApiClientError } from '@/lib/api/client';

/** Server-state cache. */
export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: true,
            refetchIntervalInBackground: false,
            retry: (failureCount, error) => {
              if (error instanceof ApiClientError) {
                if (error.kind === 'session' || error.kind === 'forbidden')
                  return false;
                if (error.status === 404 || error.status === 422) return false;
              }
              return failureCount < 2;
            },
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
