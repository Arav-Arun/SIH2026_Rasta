'use client';

import { useState, type ReactNode } from 'react';
import { LayoutList, Table2 } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';

export type DataListColumn<T> = {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** Right-align numeric columns; tabular numerals are applied automatically. */
  numeric?: boolean;
  /** Hide this column in the stacked list view (e.g. a duplicated title). */
  hideInList?: boolean;
  className?: string;
};

type DataListProps<T> = {
  columns: DataListColumn<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  /** Column used as the stacked-list heading for each row. */
  titleKey?: string;
  /** Row-level actions rendered in the last column / list footer. */
  renderActions?: (row: T) => ReactNode;
  /** 'auto' picks table on wide screens and list on narrow ones. */
  view?: 'auto' | 'table' | 'list';
  /** Allow the user to toggle between table and list. */
  allowToggle?: boolean;
  emptyMessage?: string;
  caption?: string;
  className?: string;
};

/**
 * Table with a stacked-list alternative exposing the same values and actions.
 */
export function DataList<T>({
  columns,
  rows,
  getRowId,
  titleKey,
  renderActions,
  view = 'auto',
  allowToggle = true,
  emptyMessage,
  caption,
  className,
}: DataListProps<T>) {
  const t = useT();
  const isMobile = useIsMobile();
  const [override, setOverride] = useState<'table' | 'list' | null>(null);

  const resolved: 'table' | 'list' =
    override ?? (view === 'auto' ? (isMobile ? 'list' : 'table') : view);

  const titleColumn = titleKey
    ? columns.find((column) => column.key === titleKey)
    : undefined;

  const toggle = allowToggle ? (
    <div className="flex justify-end">
      <Button
        size="xs"
        variant="outline"
        aria-pressed={resolved === 'list'}
        onClick={() => setOverride(resolved === 'list' ? 'table' : 'list')}
      >
        {resolved === 'list' ? (
          <Table2 aria-hidden="true" />
        ) : (
          <LayoutList aria-hidden="true" />
        )}
        {resolved === 'list' ? t('list.viewAsTable') : t('list.viewAsList')}
      </Button>
    </div>
  ) : null;

  if (rows.length === 0) {
    return (
      <div className={cn('space-y-2', className)}>
        {toggle}
        <p className="rounded-lg border border-dashed bg-white px-4 py-6 text-center text-sm text-muted-foreground">
          {emptyMessage ?? t('list.noRows')}
        </p>
      </div>
    );
  }

  if (resolved === 'list') {
    return (
      <div className={cn('space-y-2', className)}>
        {toggle}
        <ul className="space-y-2" aria-label={caption}>
          {rows.map((row) => (
            <li
              key={getRowId(row)}
              className="rounded-lg border bg-white p-3 focus-within:ring-2 focus-within:ring-primary/40"
            >
              {titleColumn ? (
                <p className="mb-2 text-sm font-medium">
                  {titleColumn.render(row)}
                </p>
              ) : null}
              <dl className="grid grid-cols-[minmax(0,40%)_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                {columns
                  .filter(
                    (column) =>
                      !column.hideInList && column.key !== titleColumn?.key,
                  )
                  .map((column) => (
                    <div key={column.key} className="contents">
                      <dt className="text-xs text-muted-foreground">
                        {column.header}
                      </dt>
                      <dd
                        className={cn(
                          'min-w-0',
                          column.numeric && 'tabular-nums',
                          column.className,
                        )}
                      >
                        {column.render(row)}
                      </dd>
                    </div>
                  ))}
              </dl>
              {renderActions ? (
                <div className="mt-3 flex flex-wrap gap-2 border-t pt-3">
                  {renderActions(row)}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className={cn('space-y-2', className)}>
      {toggle}
      <div className="rounded-lg border bg-white">
        <Table>
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead
                  key={column.key}
                  scope="col"
                  className={cn(column.numeric && 'text-end', column.className)}
                >
                  {column.header}
                </TableHead>
              ))}
              {renderActions ? (
                <TableHead scope="col" className="text-end">
                  {t('list.actions')}
                </TableHead>
              ) : null}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={getRowId(row)}>
                {columns.map((column) => (
                  <TableCell
                    key={column.key}
                    className={cn(
                      column.numeric && 'text-end tabular-nums',
                      column.className,
                    )}
                  >
                    {column.render(row)}
                  </TableCell>
                ))}
                {renderActions ? (
                  <TableCell className="text-end">
                    <div className="flex justify-end gap-2">
                      {renderActions(row)}
                    </div>
                  </TableCell>
                ) : null}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
