import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

type EmptyStateProps = {
  title: string;
  action?: ReactNode;
  className?: string;
};

/** What a list or panel shows when it has nothing in it. */
export function EmptyState({ title, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-start gap-3 py-4', className)}>
      <p className="text-sm text-muted-foreground">{title}</p>
      {action}
    </div>
  );
}
