// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LocaleProvider } from '@/components/i18n/locale-provider';
import { TrainedModelStatus } from '@/components/operations/data-health-screen';

vi.mock('@/lib/api/hooks', () => ({
  useDataHealth: vi.fn(),
  usePushStatus: vi.fn(),
  useRecomputeRisk: vi.fn(),
  useRiskOutcomes: vi.fn(),
}));

function status(value: unknown) {
  return render(
    <LocaleProvider>
      <TrainedModelStatus status={value} />
    </LocaleProvider>,
  );
}

describe('trained model on the data-health screen', () => {
  it('says a shadow model runs and that it lacks its inputs', () => {
    status({
      state: 'shadow',
      version: 'lr-0123456789ab',
      reason: null,
      inputs_missing: ['rain_1d', 'slope_p90_deg'],
    });
    expect(screen.getByText(/lr-0123456789ab runs in shadow/)).toBeTruthy();
    expect(screen.getByText(/rain_1d, slope_p90_deg/)).toBeTruthy();
  });

  it('says why a configured model was refused', () => {
    status({ state: 'rejected', reason: 'the model did not pass its gate' });
    expect(
      screen.getByText(/refused: the model did not pass its gate/),
    ).toBeTruthy();
  });

  it('adds nothing when no model is configured', () => {
    const { container } = status({ state: 'not_configured' });
    expect(container.textContent).toBe('');
  });
});
