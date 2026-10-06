import { describe, expect, it } from 'vitest';

import type { ConsignmentItem, ReceiptItem } from '@/lib/api/contracts';

import {
  compareDecimal,
  formatDecimal,
  phaseOf,
  receiptByItem,
  shortfallOf,
  subtractDecimal,
} from './format';

const item = (over: Partial<ConsignmentItem> = {}): ConsignmentItem => ({
  id: 'item-1',
  commodity: 'Rice',
  quantity: '50.000',
  unit: 'bag',
  weight_kg: '1000.000',
  expiry_at: null,
  ...over,
});

describe('formatDecimal', () => {
  it('drops the trailing zeros the database pads quantities with', () => {
    expect(formatDecimal('50.000')).toBe('50');
    expect(formatDecimal('1120.500')).toBe('1120.5');
    expect(formatDecimal('0.250')).toBe('0.25');
  });

  it('keeps an integer untouched and reports an absent value as null', () => {
    expect(formatDecimal('7')).toBe('7');
    expect(formatDecimal(null)).toBeNull();
    expect(formatDecimal('  ')).toBeNull();
  });

  it('never renders an empty string for a zero quantity', () => {
    expect(formatDecimal('0.000')).toBe('0');
  });
});

describe('compareDecimal', () => {
  it('treats different scales of the same value as equal', () => {
    expect(compareDecimal('30', '30.000')).toBe(0);
    expect(compareDecimal('30.5', '30.50')).toBe(0);
  });

  it('orders by magnitude, not by string length', () => {
    expect(compareDecimal('9', '100')).toBeLessThan(0);
    expect(compareDecimal('100', '9')).toBeGreaterThan(0);
    expect(compareDecimal('30.25', '30.5')).toBeLessThan(0);
  });

  it('ignores leading zeros', () => {
    expect(compareDecimal('0030', '30')).toBe(0);
  });
});

describe('subtractDecimal', () => {
  it('reports the shortfall at the wider of the two scales', () => {
    expect(subtractDecimal('50.000', '30')).toBe('20');
    expect(subtractDecimal('50.5', '30.25')).toBe('20.25');
  });

  it('returns zero when nothing is missing', () => {
    expect(subtractDecimal('50.000', '50')).toBe('0');
  });
});

describe('phaseOf', () => {
  it('groups the eight API statuses into the four a dispatcher sorts by', () => {
    expect(phaseOf('draft')).toBe('draft');
    expect(phaseOf('planned')).toBe('planned');
    expect(phaseOf('assigned')).toBe('moving');
    expect(phaseOf('in_transit')).toBe('moving');
    for (const status of [
      'delivered',
      'partially_delivered',
      'failed',
      'cancelled',
    ] as const) {
      expect(phaseOf(status)).toBe('arrived');
    }
  });
});

describe('shortfallOf', () => {
  const received = (over: Partial<ReceiptItem> = {}): ReceiptItem => ({
    consignment_item_id: 'item-1',
    commodity: 'Rice',
    ordered_quantity: '50.000',
    delivered_quantity: '50.000',
    unit: 'bag',
    note: null,
    ...over,
  });

  it('reports nothing missing when the full quantity arrived', () => {
    expect(shortfallOf(item(), received())).toBeNull();
  });

  it('reports the gap when a line arrived short', () => {
    expect(
      shortfallOf(item(), received({ delivered_quantity: '30.000' })),
    ).toBe('20');
  });

  it('counts a line the receipt never mentions as nothing delivered', () => {
    // Silence is not delivery. Treating an omitted line as fulfilled would
    // overstate what reached the facility.
    expect(shortfallOf(item(), undefined)).toBe('50');
  });

  it('does not report a shortfall when more arrived than was ordered', () => {
    expect(
      shortfallOf(item(), received({ delivered_quantity: '55.000' })),
    ).toBeNull();
  });
});

describe('receiptByItem', () => {
  it('indexes lines by the consignment item they fulfil', () => {
    const index = receiptByItem([
      {
        consignment_item_id: 'a',
        commodity: 'Rice',
        ordered_quantity: '1',
        delivered_quantity: '1',
        unit: 'bag',
        note: null,
      },
    ]);
    expect(index.get('a')?.commodity).toBe('Rice');
    expect(index.get('b')).toBeUndefined();
  });

  it('handles a trip with no receipt at all', () => {
    expect(receiptByItem(undefined).size).toBe(0);
  });
});
