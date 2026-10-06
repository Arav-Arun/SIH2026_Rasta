// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LocaleProvider, useT } from '@/components/i18n/locale-provider';
import { RiskAssessment } from '@/components/map/evidence-drawer';
import type { SegmentDetailResponse } from '@/lib/api/contracts';

vi.mock('@/lib/api/hooks', () => ({}));

type Risk = SegmentDetailResponse['risk'];

function Panel({ risk }: { risk: Risk }) {
  const t = useT();
  return <RiskAssessment risk={risk} t={t} />;
}

function show(risk: Risk) {
  return render(
    <LocaleProvider>
      <Panel risk={risk} />
    </LocaleProvider>,
  );
}

const SCORED: Risk = {
  available: true,
  level: 'moderate',
  score: 0.42,
  model_version: 'baseline-v1',
  computed_at: '2026-10-06T12:00:00Z',
  explanations: [],
  caveats: [],
  missing_inputs: [],
};

describe('risk assessment on the map', () => {
  it('shows a shadow model as a separate view, not as the score', () => {
    show({
      ...SCORED,
      shadow: { model_version: 'lr-0123456789ab', score: 0.031, level: 'high' },
    });
    expect(
      screen.getByText(
        /Trained model lr-0123456789ab, run in shadow and not used for this score: High risk, a 3\.1% chance/,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Model baseline-v1/)).toBeTruthy();
  });

  it('says nothing of a trained model when none ran', () => {
    const { container } = show(SCORED);
    expect(container.querySelector('[data-risk-shadow]')).toBeNull();
  });
});
