import { describe, expect, it } from 'vitest';

import { cn } from './utils';

describe('cn', () => {
  it('merges conflicting Tailwind utilities and omits false values', () => {
    const includeOptionalClass = false;

    expect(
      cn('p-2', includeOptionalClass && 'p-4', 'p-4', 'text-slate-700'),
    ).toBe('p-4 text-slate-700');
  });
});
