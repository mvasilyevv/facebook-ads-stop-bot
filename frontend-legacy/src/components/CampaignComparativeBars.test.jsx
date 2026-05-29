import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CampaignComparativeBars } from './CampaignComparativeBars.jsx';

// Сценарий: кампания с расходом без регистраций показывается с CPR ∞.
describe('CampaignComparativeBars', () => {
  it('shows campaign with spend and zero registrations', () => {
    render(
      <CampaignComparativeBars
        data={[
          {
            campaign: 'Test | OFFER',
            spend: '120.50',
            registrations: 0,
            deposits: 0,
            leads: 3,
            cpr: '0',
          },
        ]}
      />,
    );

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText('CPR ∞')).toBeInTheDocument();
  });
});
