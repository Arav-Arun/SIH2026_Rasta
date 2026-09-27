import { describe, expect, it } from 'vitest';

import type { ConsignmentItem } from './rastaApi';

import {
  compareQuantity,
  deriveStatus,
  firstProblem,
  formatQuantity,
  parseQuantity,
  receiptLines,
} from './logistics';

const item = (over: Partial<ConsignmentItem> = {}): ConsignmentItem => ({
  id: 'rice',
  commodity: 'Rice',
  quantity: '40.000',
  unit: 'sacks',
  weight_kg: '1000.000',
  expiry_at: null,
  ...over,
});

const RICE = item();
const ORS = item({
  id: 'ors',
  commodity: 'ORS sachets',
  quantity: '200',
  unit: 'packs',
});

describe('formatQuantity', () => {
  it('trims the scale the database stores without changing the value', () => {
    expect(formatQuantity('40.000')).toBe('40');
    expect(formatQuantity('1120.500')).toBe('1120.5');
    expect(formatQuantity('0.250')).toBe('0.25');
  });

  it('shows a zero as zero and an absent value as a dash', () => {
    expect(formatQuantity('0.000')).toBe('0');
    expect(formatQuantity(null)).toBe('-');
  });
});

describe('compareQuantity', () => {
  it('compares by value, not by string length', () => {
    expect(compareQuantity('40', '40.000')).toBe(0);
    expect(compareQuantity('9', '100')).toBeLessThan(0);
    expect(compareQuantity('30.5', '30.25')).toBeGreaterThan(0);
  });
});

describe('parseQuantity', () => {
  it('tells an empty field from a typed zero', () => {
    // Zero says nothing arrived. Empty says the driver has not answered yet.
    expect(parseQuantity('', RICE).kind).toBe('empty');
    expect(parseQuantity('0', RICE)).toEqual({ kind: 'ok', value: '0' });
  });

  it('refuses more than was loaded, because the server would too', () => {
    const result = parseQuantity('41', RICE);
    expect(result.kind).toBe('invalid');
    expect(result.kind === 'invalid' && result.reason).toContain('40 sacks');
  });

  it('accepts exactly what was loaded', () => {
    expect(parseQuantity('40', RICE)).toEqual({ kind: 'ok', value: '40' });
  });

  it('refuses text, signs and more than three decimals', () => {
    for (const bad of ['abc', '-5', '1.2345', '1,5', '']) {
      expect(parseQuantity(bad, RICE).kind).not.toBe('ok');
    }
  });
});

describe('deriveStatus', () => {
  it('is delivered only when every line arrived in full', () => {
    expect(deriveStatus([RICE, ORS], { rice: '40', ors: '200' })).toBe(
      'delivered',
    );
  });

  it('is partial when any line is short', () => {
    expect(deriveStatus([RICE, ORS], { rice: '30', ors: '200' })).toBe(
      'partially_delivered',
    );
  });

  it('is failed when nothing arrived at all', () => {
    expect(deriveStatus([RICE, ORS], { rice: '0', ors: '0' })).toBe('failed');
    expect(deriveStatus([RICE, ORS], {})).toBe('failed');
  });

  it('treats an unanswered line as nothing delivered, never as complete', () => {
    // Silence is not delivery. Reading a blank field as "fine" would overstate
    // what reached the facility.
    expect(deriveStatus([RICE, ORS], { rice: '40' })).toBe(
      'partially_delivered',
    );
  });

  it('ignores an invalid entry rather than counting it as delivered', () => {
    expect(deriveStatus([RICE, ORS], { rice: '41', ors: '200' })).toBe(
      'partially_delivered',
    );
  });
});

describe('receiptLines', () => {
  it('sends every line, including the zeroes', () => {
    // A line the receipt omits would read to a dispatcher as if it were fine.
    expect(receiptLines([RICE, ORS], { rice: '30' })).toEqual([
      { consignment_item_id: 'rice', delivered_quantity: '30' },
      { consignment_item_id: 'ors', delivered_quantity: '0' },
    ]);
  });
});

describe('firstProblem', () => {
  it('names the commodity so the driver knows which field to fix', () => {
    expect(firstProblem([RICE, ORS], { rice: '40', ors: '500' })).toContain(
      'ORS sachets',
    );
  });

  it('is silent when every line is acceptable', () => {
    expect(firstProblem([RICE, ORS], { rice: '40', ors: '200' })).toBeNull();
  });
});
