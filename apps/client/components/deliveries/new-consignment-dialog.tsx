'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Trash2, X } from 'lucide-react';

import { ErrorPanel } from '@/components/common/error-panel';
import { useT } from '@/components/i18n/locale-provider';
import { useConnectivitySummary, useCreateConsignment } from '@/lib/api/hooks';
import { newUuid } from '@/lib/ids';
import type {
  ConnectivityFacility,
  ConsignmentCreateRequest,
  DistrictIdentity,
} from '@/lib/api/contracts';

const PRIORITIES = ['low', 'normal', 'high', 'critical'] as const;

type ItemRow = {
  key: string;
  commodity: string;
  quantity: string;
  unit: string;
  weight: string;
};

function emptyItem(): ItemRow {
  return { key: newUuid(), commodity: '', quantity: '', unit: '', weight: '' };
}

/** A reference a dispatcher can read out on a call: date plus a short tag. */
function draftReference(now = new Date()): string {
  const two = (value: number) => String(value).padStart(2, '0');
  const stamp = `${two(now.getFullYear() % 100)}${two(now.getMonth() + 1)}${two(now.getDate())}`;
  return `CN-${stamp}-${newUuid().slice(0, 4).toUpperCase()}`;
}

/** Plain decimals only, as the API stores them: no signs, exponents or commas. */
const DECIMAL = /^\d+(\.\d+)?$/;

function positive(value: string): boolean {
  return DECIMAL.test(value.trim()) && Number(value) > 0;
}

function blankOrNonNegative(value: string): boolean {
  return value.trim() === '' || DECIMAL.test(value.trim());
}

/**
 * Raises a consignment as a draft: from one facility to another, with its
 * manifest. Planning and assignment stay separate steps on the detail panel.
 */
export function NewConsignmentDialog({
  districts,
  onClose,
  onCreated,
}: {
  districts: DistrictIdentity[];
  onClose: () => void;
  onCreated: (consignmentId: string) => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const [districtId, setDistrictId] = useState(districts[0]?.id ?? '');
  const [reference, setReference] = useState(draftReference);
  const [originId, setOriginId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [priority, setPriority] =
    useState<(typeof PRIORITIES)[number]>('normal');
  const [deadline, setDeadline] = useState('');
  const [items, setItems] = useState<ItemRow[]>(() => [emptyItem()]);
  // One key per distinct request: a retry after a dropped connection repeats
  // the key, so it cannot create the consignment twice; an edited form is a
  // new request and gets a new key.
  const lastAttempt = useRef<{ body: string; key: string } | null>(null);

  const connectivity = useConnectivitySummary(districtId || null);
  const create = useCreateConsignment();

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  // Only facilities on the road graph: anything else could never be planned.
  const facilities = useMemo<ConnectivityFacility[]>(
    () =>
      (connectivity.data?.facility_status ?? [])
        .filter((facility) => facility.routing_node_id != null)
        .sort((left, right) => left.name.localeCompare(right.name)),
    [connectivity.data],
  );

  const sameFacility = originId !== '' && originId === destinationId;
  const itemsValid = items.every(
    (item) =>
      item.commodity.trim() !== '' &&
      positive(item.quantity) &&
      item.unit.trim() !== '' &&
      blankOrNonNegative(item.weight),
  );
  const canSubmit =
    districtId !== '' &&
    reference.trim() !== '' &&
    originId !== '' &&
    destinationId !== '' &&
    !sameFacility &&
    itemsValid &&
    !create.isPending;

  function updateItem(key: string, change: Partial<ItemRow>) {
    setItems((rows) =>
      rows.map((row) => (row.key === key ? { ...row, ...change } : row)),
    );
  }

  function submit() {
    if (!canSubmit) return;
    const body: ConsignmentCreateRequest = {
      district_id: districtId,
      reference: reference.trim(),
      origin_facility_id: originId,
      destination_facility_id: destinationId,
      priority,
      deadline_at: deadline ? new Date(deadline).toISOString() : null,
      items: items.map((item) => ({
        commodity: item.commodity.trim(),
        quantity: item.quantity.trim(),
        unit: item.unit.trim(),
        weight_kg: item.weight.trim() === '' ? null : item.weight.trim(),
      })),
    };
    const serialised = JSON.stringify(body);
    if (lastAttempt.current?.body !== serialised) {
      lastAttempt.current = { body: serialised, key: newUuid() };
    }
    create.mutate(
      { body, idempotencyKey: lastAttempt.current.key },
      {
        onSuccess: (consignment) => {
          onCreated(consignment.id);
          ref.current?.close();
        },
      },
    );
  }

  const fieldClass =
    'rounded-md border border-border bg-background p-2 text-sm';
  const labelClass =
    'text-xs font-semibold uppercase tracking-wide text-muted-foreground';

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={t('newConsignment.title')}
      data-new-consignment
      className="m-auto w-[min(36rem,92vw)] rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-black/50"
    >
      <form
        className="flex max-h-[88vh] flex-col gap-4 overflow-y-auto p-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <header className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">
              {t('newConsignment.title')}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t('newConsignment.hint')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => ref.current?.close()}
            aria-label={t('map.close')}
            className="rounded-md p-1"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>

        <div className="grid gap-3 sm:grid-cols-2">
          {districts.length > 1 && (
            <label className="flex flex-col gap-1.5 sm:col-span-2">
              <span className={labelClass}>{t('newConsignment.district')}</span>
              <select
                value={districtId}
                onChange={(event) => {
                  setDistrictId(event.target.value);
                  setOriginId('');
                  setDestinationId('');
                }}
                className={fieldClass}
              >
                {districts.map((district) => (
                  <option key={district.id} value={district.id}>
                    {district.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>{t('newConsignment.reference')}</span>
            <input
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              maxLength={64}
              required
              data-consignment-reference
              className={fieldClass}
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>{t('priority.label')}</span>
            <select
              value={priority}
              onChange={(event) =>
                setPriority(event.target.value as (typeof PRIORITIES)[number])
              }
              data-consignment-priority
              className={fieldClass}
            >
              {PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {t(`priority.${value}`)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>{t('newConsignment.origin')}</span>
            <select
              value={originId}
              onChange={(event) => setOriginId(event.target.value)}
              disabled={connectivity.isPending}
              data-consignment-end="origin"
              className={fieldClass}
            >
              <option value="">{t('newConsignment.choose')}</option>
              {facilities.map((facility) => (
                <option key={facility.facility_id} value={facility.facility_id}>
                  {facility.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>
              {t('newConsignment.destination')}
            </span>
            <select
              value={destinationId}
              onChange={(event) => setDestinationId(event.target.value)}
              disabled={connectivity.isPending}
              data-consignment-end="destination"
              className={fieldClass}
            >
              <option value="">{t('newConsignment.choose')}</option>
              {facilities.map((facility) => (
                <option key={facility.facility_id} value={facility.facility_id}>
                  {facility.name}
                </option>
              ))}
            </select>
          </label>

          {sameFacility && (
            <p className="text-xs text-[#92400E] sm:col-span-2">
              {t('newConsignment.sameFacility')}
            </p>
          )}
          {!connectivity.isPending && facilities.length === 0 && (
            <p className="text-xs text-[#92400E] sm:col-span-2">
              {t('newConsignment.noFacilities')}
            </p>
          )}

          <label className="flex flex-col gap-1.5 sm:col-span-2">
            <span className={labelClass}>{t('newConsignment.deadline')}</span>
            <input
              type="datetime-local"
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
              className={fieldClass}
            />
          </label>
        </div>

        <section className="flex flex-col gap-2">
          <h3 className={labelClass}>{t('newConsignment.items')}</h3>
          <ul className="flex flex-col gap-2">
            {items.map((item, index) => (
              <li key={item.key} data-item-row>
                <fieldset className="grid grid-cols-2 gap-2 rounded-md border border-border p-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <legend className="sr-only">
                    {t('newConsignment.item', { number: index + 1 })}
                  </legend>
                  <label className="col-span-2 flex flex-col gap-1 sm:col-span-1">
                    <span className="text-xs text-muted-foreground">
                      {t('newConsignment.commodity')}
                    </span>
                    <input
                      value={item.commodity}
                      onChange={(event) =>
                        updateItem(item.key, { commodity: event.target.value })
                      }
                      maxLength={200}
                      data-item-field="commodity"
                      className={fieldClass}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      {t('newConsignment.quantity')}
                    </span>
                    <input
                      inputMode="decimal"
                      value={item.quantity}
                      onChange={(event) =>
                        updateItem(item.key, { quantity: event.target.value })
                      }
                      data-item-field="quantity"
                      className={fieldClass}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      {t('newConsignment.unit')}
                    </span>
                    <input
                      value={item.unit}
                      onChange={(event) =>
                        updateItem(item.key, { unit: event.target.value })
                      }
                      maxLength={32}
                      data-item-field="unit"
                      className={fieldClass}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-xs text-muted-foreground">
                      {t('newConsignment.weight')}
                    </span>
                    <input
                      inputMode="decimal"
                      value={item.weight}
                      onChange={(event) =>
                        updateItem(item.key, { weight: event.target.value })
                      }
                      data-item-field="weight"
                      className={fieldClass}
                    />
                  </label>
                  <div className="flex items-end">
                    <button
                      type="button"
                      onClick={() =>
                        setItems((rows) =>
                          rows.filter((row) => row.key !== item.key),
                        )
                      }
                      disabled={items.length === 1}
                      aria-label={t('newConsignment.removeItem', {
                        number: index + 1,
                      })}
                      className="rounded-md border border-border p-2 disabled:opacity-40"
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </button>
                  </div>
                </fieldset>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setItems((rows) => [...rows, emptyItem()])}
            className="flex items-center gap-1.5 self-start rounded-md border border-border px-3 py-1.5 text-sm font-medium"
          >
            <Plus className="size-4" aria-hidden />
            {t('newConsignment.addItem')}
          </button>
        </section>

        {create.isError && (
          <ErrorPanel
            title={t('newConsignment.error')}
            message={
              create.error instanceof Error ? create.error.message : undefined
            }
          />
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={() => ref.current?.close()}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium"
          >
            {t('newConsignment.cancel')}
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            data-create-consignment
            className="rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
          >
            {create.isPending
              ? t('confirm.working')
              : t('newConsignment.submit')}
          </button>
        </div>
      </form>
    </dialog>
  );
}
