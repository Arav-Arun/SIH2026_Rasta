// @vitest-environment jsdom
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { LanguageSwitcher } from '@/components/i18n/language-switcher';
import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
  useLocale,
} from '@/components/i18n/locale-provider';
import { ConfirmDialog } from '@/components/common/confirm-dialog';
import { DataList } from '@/components/common/data-list';
import { ErrorPanel } from '@/components/common/error-panel';
import { FreshnessLabel } from '@/components/common/freshness-label';
import { ModeBadge } from '@/components/common/mode-badge';
import { OfflineBanner } from '@/components/common/offline-banner';
import { StatusBadge } from '@/components/common/status-badge';
import { SUPPORTED_LOCALES } from '@/lib/i18n/messages';

function wrap(ui: ReactNode) {
  return render(<LocaleProvider>{ui}</LocaleProvider>);
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('StatusBadge', () => {
  it('renders a text label with a screen-reader category prefix', () => {
    wrap(<StatusBadge kind="passability" value="closed" />);
    const badge = screen.getByText('Closed').closest('[data-status-kind]');
    expect(badge).toHaveAttribute('data-status-value', 'closed');
    expect(badge?.textContent).toContain('Road status:');
    // colour is never the only carrier: a glyph is present
    expect(badge?.querySelector('svg')).not.toBeNull();
  });

  it('uses truthful sync language', () => {
    wrap(<StatusBadge kind="sync" value="waiting" />);
    expect(screen.getByText('Waiting for connection')).toBeTruthy();
  });
});

describe('ModeBadge', () => {
  it('marks a simulated scenario and says when nothing is connected', () => {
    const { rerender } = wrap(<ModeBadge dataMode="synthetic" />);
    const synthetic = screen
      .getByText('Simulated scenario')
      .closest('[data-data-mode]');
    expect(synthetic).toHaveAttribute('data-data-mode', 'synthetic');
    expect(synthetic?.className).toContain('uppercase');

    rerender(
      <LocaleProvider>
        <ModeBadge dataMode={null} />
      </LocaleProvider>,
    );
    expect(screen.getByText('No data connected')).toBeTruthy();
  });
});

describe('FreshnessLabel', () => {
  const now = new Date('2026-09-18T09:00:00.000Z');

  it('shows relative + absolute IST time and a stale marker', () => {
    wrap(
      <FreshnessLabel
        asOf="2026-09-18T06:00:00.000Z"
        now={now}
        source="IMD district warning"
      />,
    );
    expect(screen.getByText('Updated 3 hours ago')).toBeTruthy();
    expect(screen.getByText(/11:30 IST/)).toBeTruthy();
    expect(screen.getByText('Source: IMD district warning')).toBeTruthy();
    expect(screen.getByText('Stale')).toBeTruthy();
    expect(document.querySelector('time')).toHaveAttribute(
      'dateTime',
      '2026-09-18T06:00:00.000Z',
    );
  });

  it('says so when nothing was ever updated', () => {
    wrap(<FreshnessLabel asOf={null} now={now} />);
    expect(screen.getByText('Never updated')).toBeTruthy();
    expect(document.querySelector('[data-stale]')).toHaveAttribute(
      'data-stale',
      'true',
    );
  });
});

describe('OfflineBanner', () => {
  it('is hidden while online unless forced', () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    const { container, rerender } = wrap(<OfflineBanner queuedCount={2} />);
    expect(container.querySelector('[data-offline-banner]')).toBeNull();

    rerender(
      <LocaleProvider>
        <OfflineBanner queuedCount={2} forceVisible onRetry={() => {}} />
      </LocaleProvider>,
    );
    expect(screen.getByRole('status').textContent).toContain(
      '2 items waiting to send',
    );
    expect(screen.getByRole('button', { name: 'Retry now' })).toBeTruthy();
  });

  it('appears when the browser reports offline', () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    wrap(<OfflineBanner />);
    expect(screen.getByText('Working from the last saved data')).toBeTruthy();
  });
});

describe('ErrorPanel', () => {
  it('keeps user input visible next to retry and the request ID', () => {
    const onRetry = vi.fn();
    wrap(
      <ErrorPanel requestId="req_123" onRetry={onRetry}>
        <input defaultValue="my draft" aria-label="draft" />
      </ErrorPanel>,
    );
    expect(screen.getByRole('alert').textContent).toContain(
      'Request ID req_123',
    );
    expect(screen.getByLabelText('draft')).toHaveValue('my draft');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe('ConfirmDialog', () => {
  it('refuses to confirm without a reason and passes the reason through', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    wrap(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        title="Confirm closure"
        requireReason
        onConfirm={onConfirm}
      />,
    );
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(within(dialog).getByText('A reason is required.')).toBeTruthy();

    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'Landslide photo verified by dispatcher' },
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    });
    expect(onConfirm).toHaveBeenCalledWith(
      'Landslide photo verified by dispatcher',
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('stays open with the reason preserved when confirmation fails', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('boom'));
    const onOpenChange = vi.fn();
    wrap(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        requireReason
        onConfirm={onConfirm}
      />,
    );
    const dialog = screen.getByRole('alertdialog');
    fireEvent.change(within(dialog).getByLabelText('Reason'), {
      target: { value: 'keep me' },
    });
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }));
    });
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(within(dialog).getByLabelText('Reason')).toHaveValue('keep me');
  });
});

describe('DataList', () => {
  type Row = { id: string; ref: string; qty: number };
  const rows: Row[] = [
    { id: 'a', ref: 'CN-001', qty: 12 },
    { id: 'b', ref: 'CN-002', qty: 3 },
  ];
  const columns = [
    { key: 'ref', header: 'Reference', render: (r: Row) => r.ref },
    {
      key: 'qty',
      header: 'Quantity',
      render: (r: Row) => String(r.qty),
      numeric: true,
    },
  ];

  it('renders the same values and actions in table and list views', () => {
    const onOpen = vi.fn();
    wrap(
      <DataList
        columns={columns}
        rows={rows}
        getRowId={(r) => r.id}
        titleKey="ref"
        view="table"
        caption="Consignments"
        renderActions={(r) => (
          <button type="button" onClick={() => onOpen(r.id)}>
            Open
          </button>
        )}
      />,
    );
    expect(screen.getByRole('table')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Open' })).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'View as list' }));
    expect(screen.queryByRole('table')).toBeNull();
    const list = screen.getByRole('list', { name: 'Consignments' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(within(list).getByText('CN-002')).toBeTruthy();
    expect(within(list).getAllByText('Quantity')).toHaveLength(2);
    fireEvent.click(within(list).getAllByRole('button', { name: 'Open' })[1]);
    expect(onOpen).toHaveBeenCalledWith('b');
  });

  it('shows an explicit empty message', () => {
    wrap(<DataList columns={columns} rows={[]} getRowId={(r: Row) => r.id} />);
    expect(screen.getByText('No rows to display')).toBeTruthy();
  });
});

function DraftProbe() {
  const { locale, t } = useLocale();
  return (
    <div>
      <p data-testid="locale">{locale}</p>
      <p data-testid="label">{t('status.passability.closed')}</p>
      <textarea aria-label="draft" defaultValue="" />
    </div>
  );
}

describe('LocaleProvider + LanguageSwitcher', () => {
  it('switches language in place, keeps the draft, persists the choice', async () => {
    wrap(
      <>
        <LanguageSwitcher />
        <DraftProbe />
      </>,
    );
    const draft = screen.getByLabelText('draft');
    fireEvent.change(draft, { target: { value: 'NH-6 blocked near km 42' } });
    expect(screen.getByTestId('label').textContent).toBe('Closed');

    // Every offered language is listed by its own name.
    const select = screen.getByLabelText(
      'Language',
    ) as unknown as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual([
      ...SUPPORTED_LOCALES,
    ]);
    expect(screen.getByRole('option', { name: /हिन्दी/ })).toBeTruthy();

    fireEvent.change(select, { target: { value: 'hi' } });
    // The catalogue loads on demand; the page switches once it has arrived.
    await waitFor(() =>
      expect(screen.getByTestId('locale').textContent).toBe('hi'),
    );
    expect(screen.getByTestId('label').textContent).toBe('बंद');
    expect(screen.getByLabelText('draft')).toHaveValue(
      'NH-6 blocked near km 42',
    );
    expect(document.documentElement.lang).toBe('hi');
    expect(document.documentElement.dir).toBe('ltr');
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('hi');
    // the unreviewed warning is shown in the selected language
    expect(screen.getByRole('note').textContent).toBe(
      'अनुवाद का मसौदा, मूल भाषी की समीक्षा अभी बाकी',
    );

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'as' } });
    await waitFor(() =>
      expect(screen.getByTestId('label').textContent).toBe('বন্ধ'),
    );
    expect(screen.getByLabelText('draft')).toHaveValue(
      'NH-6 blocked near km 42',
    );
  });

  it('restores a persisted locale on mount', async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'as');
    wrap(<DraftProbe />);
    await waitFor(() =>
      expect(screen.getByTestId('locale').textContent).toBe('as'),
    );
  });

  it('ignores a stored language this build has no catalogue for', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'ur');
    wrap(<DraftProbe />);
    expect(screen.getByTestId('locale').textContent).toBe('en');
  });
});
