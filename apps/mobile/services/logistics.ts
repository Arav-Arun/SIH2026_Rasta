import type { ConsignmentItem } from './rastaApi';

/** Quantities cross the wire as decimal strings and stay that way. */
export function formatQuantity(value: string | null | undefined): string {
  if (value === null || value === undefined) return '-';
  const trimmed = value.trim();
  if (trimmed === '') return '-';
  if (!trimmed.includes('.')) return trimmed;
  const stripped = trimmed.replace(/0+$/, '').replace(/\.$/, '');
  return stripped === '' ? '0' : stripped;
}

/** What the driver typed, validated against the line it fulfils. */
type QuantityInput =
  | { kind: 'empty' }
  | { kind: 'invalid'; reason: string }
  | { kind: 'ok'; value: string };

export function parseQuantity(
  raw: string,
  item: ConsignmentItem,
): QuantityInput {
  const text = raw.trim();
  if (text === '') return { kind: 'empty' };
  if (!/^\d+(\.\d{1,3})?$/.test(text)) {
    return { kind: 'invalid', reason: 'Enter a number, up to three decimals.' };
  }
  if (compareQuantity(text, item.quantity) > 0) {
    return {
      kind: 'invalid',
      reason: `More than the ${formatQuantity(item.quantity)} ${item.unit} loaded.`,
    };
  }
  return { kind: 'ok', value: text };
}

/** Decimal-string comparison, so 30 and 30.000 are equal. */
export function compareQuantity(left: string, right: string): number {
  const [leftWhole, leftFraction = ''] = left.trim().split('.');
  const [rightWhole, rightFraction = ''] = right.trim().split('.');
  const a = leftWhole.replace(/^0+(?=\d)/, '');
  const b = rightWhole.replace(/^0+(?=\d)/, '');
  if (a.length !== b.length) return a.length - b.length;
  if (a !== b) return a < b ? -1 : 1;
  const width = Math.max(leftFraction.length, rightFraction.length);
  const x = leftFraction.padEnd(width, '0');
  const y = rightFraction.padEnd(width, '0');
  if (x === y) return 0;
  return x < y ? -1 : 1;
}

type ReceiptStatus = 'delivered' | 'partially_delivered' | 'failed';

/** The status the quantities imply. */
export function deriveStatus(
  items: ConsignmentItem[],
  entered: Record<string, string>,
): ReceiptStatus {
  let anyDelivered = false;
  let allComplete = true;

  for (const item of items) {
    const parsed = parseQuantity(entered[item.id] ?? '', item);
    const value = parsed.kind === 'ok' ? parsed.value : '0';
    if (compareQuantity(value, '0') > 0) anyDelivered = true;
    if (compareQuantity(value, item.quantity) < 0) allComplete = false;
  }

  if (!anyDelivered) return 'failed';
  return allComplete ? 'delivered' : 'partially_delivered';
}

/** Every line is sent, including the zeroes: an omitted line reads as fine. */
export function receiptLines(
  items: ConsignmentItem[],
  entered: Record<string, string>,
): { consignment_item_id: string; delivered_quantity: string }[] {
  return items.map((item) => {
    const parsed = parseQuantity(entered[item.id] ?? '', item);
    return {
      consignment_item_id: item.id,
      delivered_quantity: parsed.kind === 'ok' ? parsed.value : '0',
    };
  });
}

export function firstProblem(
  items: ConsignmentItem[],
  entered: Record<string, string>,
): string | null {
  for (const item of items) {
    const parsed = parseQuantity(entered[item.id] ?? '', item);
    if (parsed.kind === 'invalid') return `${item.commodity}: ${parsed.reason}`;
  }
  return null;
}
