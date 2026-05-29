import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import OfferDetailPanel from './OfferDetailPanel.jsx';

vi.mock('./OfferThresholdsTab.jsx', () => ({
  default: () => <div data-testid="thresholds-tab">Пороги</div>,
}));

vi.mock('./OfferRulesTab.jsx', () => ({
  default: () => <div data-testid="rules-tab">Правила</div>,
}));

const sampleOffer = {
  id: '11111111-2222-3333-4444-555555555555',
  code: 'DRC_CR2',
  cpa_amount: 5,
  country_name: 'Конго',
  is_active: true,
  cabinet_id: 'act_123',
  pixel_id: '999',
};

describe('OfferDetailPanel: master-detail панель', () => {
  // Сценарий: заголовок оффера и переключение вкладок пороги/правила.
  it('показывает код оффера и переключает вкладки', async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();

    render(
      <OfferDetailPanel
        offer={sampleOffer}
        activeTab="thresholds"
        onTabChange={onTabChange}
        onOfferUpdated={vi.fn()}
        onToast={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'DRC_CR2' })).toBeInTheDocument();
    expect(screen.getByTestId('thresholds-tab')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Правила' }));
    expect(onTabChange).toHaveBeenCalledWith('rules');
  });

  // Сценарий: chips копирования ID, кабинета и пикселя.
  it('рендерит copy chips для идентификаторов', () => {
    render(
      <OfferDetailPanel
        offer={sampleOffer}
        activeTab="thresholds"
        onTabChange={vi.fn()}
        onOfferUpdated={vi.fn()}
        onToast={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Скопировать ID' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скопировать Кабинет' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Скопировать Пиксель' })).toBeInTheDocument();
  });
});
