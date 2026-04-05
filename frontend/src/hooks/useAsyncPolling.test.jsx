import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAsyncPolling } from './useAsyncPolling.js';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useAsyncPolling: базовый polling', () => {
  // Сценарий: callback вызывается через заданный интервал, а не сразу
  it('callback вызывается через intervalMs после монтирования', async () => {
    const callback = vi.fn().mockResolvedValue(undefined);

    renderHook(() =>
      useAsyncPolling(callback, { enabled: true, intervalMs: 1000 }),
    );

    expect(callback).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(callback).toHaveBeenCalledTimes(1);
  });

  // Сценарий: callback вызывается повторно после каждого интервала
  it('callback вызывается несколько раз при повторных тиках', async () => {
    const callback = vi.fn().mockResolvedValue(undefined);

    renderHook(() =>
      useAsyncPolling(callback, { enabled: true, intervalMs: 500 }),
    );

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(callback).toHaveBeenCalledTimes(3);
  });

  // Сценарий: runImmediately=true запускает callback до первого тика таймера
  it('runImmediately вызывает callback немедленно при монтировании', async () => {
    const callback = vi.fn().mockResolvedValue(undefined);

    await act(async () => {
      renderHook(() =>
        useAsyncPolling(callback, { enabled: true, intervalMs: 1000, runImmediately: true }),
      );
    });

    expect(callback).toHaveBeenCalledTimes(1);
  });
});

describe('useAsyncPolling: остановка при unmount', () => {
  // Сценарий: после размонтирования компонента таймер очищается и callback больше не вызывается
  it('polling прекращается после unmount', async () => {
    const callback = vi.fn().mockResolvedValue(undefined);

    const { unmount } = renderHook(() =>
      useAsyncPolling(callback, { enabled: true, intervalMs: 500 }),
    );

    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    unmount();

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    // После unmount количество вызовов не должно вырасти
    expect(callback).toHaveBeenCalledTimes(1);
  });

  // Сценарий: enabled=false не запускает ни одного вызова
  it('при enabled=false callback не вызывается', async () => {
    const callback = vi.fn().mockResolvedValue(undefined);

    renderHook(() =>
      useAsyncPolling(callback, { enabled: false, intervalMs: 500 }),
    );

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    expect(callback).not.toHaveBeenCalled();
  });
});

describe('useAsyncPolling: backoff при ошибке', () => {
  // Сценарий: если callback бросает ошибку, следующий тик откладывается на intervalMs * errorMultiplier
  it('при ошибке интервал увеличивается на errorMultiplier', async () => {
    let callCount = 0;
    const callback = vi.fn().mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) throw new Error('временная ошибка');
    });

    renderHook(() =>
      useAsyncPolling(callback, { enabled: true, intervalMs: 1000, errorMultiplier: 3 }),
    );

    // Первый тик через 1000ms — callback падает
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    // Через ещё 1000ms (нормальный интервал) — вызова ещё не должно быть,
    // потому что backoff = 1000 * 3 = 3000ms
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    // Ещё 2000ms — итого 3000ms после первой ошибки — второй вызов происходит
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(callback).toHaveBeenCalledTimes(2);
  });

  // Сценарий: после успешного вызова backoff сбрасывается, интервал возвращается к нормальному
  it('после успешного вызова backoff сбрасывается', async () => {
    let callCount = 0;
    const callback = vi.fn().mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) throw new Error('разовая ошибка');
      // второй и последующие — успех
    });

    renderHook(() =>
      useAsyncPolling(callback, { enabled: true, intervalMs: 1000, errorMultiplier: 3 }),
    );

    // Первый тик — ошибка
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(callback).toHaveBeenCalledTimes(1);

    // Второй тик с backoff 3000ms — успех
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(callback).toHaveBeenCalledTimes(2);

    // Третий тик — обычный интервал 1000ms (backoff сброшен)
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(callback).toHaveBeenCalledTimes(3);
  });
});
