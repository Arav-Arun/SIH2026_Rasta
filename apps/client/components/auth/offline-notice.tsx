'use client';

import { createContext, useContext, type ReactNode } from 'react';

/**
 * The "working offline" notice the access gate decides on. The screen's shell
 * draws it inside its content column, so the sidebar never covers it.
 */
export const OfflineNoticeContext = createContext<ReactNode>(null);

export function useOfflineNotice(): ReactNode {
  return useContext(OfflineNoticeContext);
}
