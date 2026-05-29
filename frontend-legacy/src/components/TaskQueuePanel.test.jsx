import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TaskQueuePanel } from './TaskQueuePanel.jsx';

function makeRecommendation(overrides = {}) {
  return {
    id: 'rec-1',
    ad_name: 'DRC_CR2_CR002',
    reason_title: 'Нет блокирующих сигналов',
    state: 'OPEN',
    related_enable_task_status: null,
    ...overrides,
  };
}

describe('TaskQueuePanel: рекомендации на включение', () => {
  // Сценарий: активная связанная задача блокирует повторное создание enable-задачи.
  it('показывает статус активной enable-задачи и блокирует кнопку', async () => {
    const user = userEvent.setup();
    const onCreateEnableTask = vi.fn();
    render(
      <TaskQueuePanel
        enableRecs={[
          makeRecommendation({
            state: 'TASK_CREATED',
            related_enable_task_status: 'PENDING',
          }),
        ]}
        onCreateEnableTask={onCreateEnableTask}
      />,
    );

    expect(
      screen.getAllByText((_, node) => node?.textContent?.includes('Задача: Ожидает')).length,
    ).toBeGreaterThan(0);
    const button = screen.getByRole('button', { name: 'В очереди' });
    expect(button).toBeDisabled();

    await user.click(button);

    expect(onCreateEnableTask).not.toHaveBeenCalled();
  });

  // Сценарий: failed enable-задачу можно вернуть в очередь через ту же рекомендацию.
  it('разрешает повторить failed enable-задачу', async () => {
    const user = userEvent.setup();
    const onCreateEnableTask = vi.fn();
    render(
      <TaskQueuePanel
        enableRecs={[
          makeRecommendation({
            state: 'TASK_CREATED',
            related_enable_task_status: 'FAILED',
          }),
        ]}
        onCreateEnableTask={onCreateEnableTask}
      />,
    );

    expect(
      screen.getAllByText((_, node) => node?.textContent?.includes('Задача: Ошибка')).length,
    ).toBeGreaterThan(0);
    const button = screen.getByRole('button', { name: 'Повторить' });
    expect(button).toBeEnabled();

    await user.click(button);

    expect(onCreateEnableTask).toHaveBeenCalledWith('rec-1');
  });

  // Сценарий: открытая рекомендация без задачи создаёт enable-задачу по клику.
  it('создаёт enable-задачу для открытой рекомендации', async () => {
    const user = userEvent.setup();
    const onCreateEnableTask = vi.fn();
    render(
      <TaskQueuePanel
        enableRecs={[makeRecommendation()]}
        onCreateEnableTask={onCreateEnableTask}
      />,
    );

    const button = screen.getByRole('button', { name: 'Включить' });
    expect(button).toBeEnabled();

    await user.click(button);

    expect(onCreateEnableTask).toHaveBeenCalledWith('rec-1');
  });
});
