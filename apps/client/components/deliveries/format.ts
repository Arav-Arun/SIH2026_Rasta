import type {
  Consignment,
  ConsignmentItem,
  ReceiptItem,
} from '@/lib/api/contracts';

/**
 * Quantities and weights cross the wire as decimal strings, never as floats.
 */
export function formatDecimal(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  if (trimmed === '') return null;
  if (!trimmed.includes('.')) return trimmed;
  const withoutTrailingZeros = trimmed.replace(/0+$/, '').replace(/\.$/, '');
  return withoutTrailingZeros === '' ? '0' : withoutTrailingZeros;
}

/** Decimal-string comparison, so 30 vs 30.000 is equality and not a shortfall. */
export function compareDecimal(left: string, right: string): number {
  const [leftWhole, leftFraction = ''] = left.trim().split('.');
  const [rightWhole, rightFraction = ''] = right.trim().split('.');
  const negative = leftWhole.startsWith('-') || rightWhole.startsWith('-');
  if (negative) {
    // Quantities are non-negative by database constraint; fall back rather
    // than pretend this helper handles signs it will never be given.
    return Number(left) - Number(right);
  }
  const whole = leftWhole.replace(/^0+(?=\d)/, '');
  const otherWhole = rightWhole.replace(/^0+(?=\d)/, '');
  if (whole.length !== otherWhole.length)
    return whole.length - otherWhole.length;
  if (whole !== otherWhole) return whole < otherWhole ? -1 : 1;
  const width = Math.max(leftFraction.length, rightFraction.length);
  const a = leftFraction.padEnd(width, '0');
  const b = rightFraction.padEnd(width, '0');
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/** Subtraction for the shortfall label; both sides are non-negative decimals. */
export function subtractDecimal(ordered: string, delivered: string): string {
  const scale = Math.max(
    (ordered.split('.')[1] ?? '').length,
    (delivered.split('.')[1] ?? '').length,
  );
  const toScaled = (value: string) => {
    const [whole, fraction = ''] = value.trim().split('.');
    return BigInt(whole + fraction.padEnd(scale, '0'));
  };
  const difference = toScaled(ordered) - toScaled(delivered);
  if (scale === 0) return difference.toString();
  const text = difference.toString().padStart(scale + 1, '0');
  const whole = text.slice(0, text.length - scale);
  const fraction = text.slice(text.length - scale);
  return formatDecimal(`${whole}.${fraction}`) ?? '0';
}

type ConsignmentPhase = 'draft' | 'planned' | 'moving' | 'arrived';

/**
 * The four states a dispatcher actually sorts by. The API's eight statuses are
 * the record; these are the question "what do I need to do about it?".
 */
export function phaseOf(status: Consignment['status']): ConsignmentPhase {
  switch (status) {
    case 'draft':
      return 'draft';
    case 'planned':
      return 'planned';
    case 'assigned':
    case 'in_transit':
      return 'moving';
    default:
      return 'arrived';
  }
}

/** Receipt lines indexed by the consignment item they fulfil. */
export function receiptByItem(
  items: ReceiptItem[] | undefined,
): Map<string, ReceiptItem> {
  return new Map((items ?? []).map((line) => [line.consignment_item_id, line]));
}

/** A line the receipt does not mention did not arrive. */
export function shortfallOf(
  item: ConsignmentItem,
  received: ReceiptItem | undefined,
): string | null {
  const delivered = received?.delivered_quantity ?? '0';
  if (compareDecimal(delivered, item.quantity) >= 0) return null;
  return subtractDecimal(item.quantity, delivered);
}
