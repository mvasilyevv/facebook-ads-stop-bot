import { render } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAutoRefresh } from "../../src/hooks/useAutoRefresh";

type AutoRefreshProbeProps = {
  enabled?: boolean;
  intervalMs?: number;
  onReload: (silent?: boolean) => Promise<void>;
};

function AutoRefreshProbe({
  enabled = true,
  intervalMs = 5_000,
  onReload,
}: AutoRefreshProbeProps) {
  useAutoRefresh(onReload, { enabled, intervalMs });
  return null;
}

describe("useAutoRefresh", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  // Проверяет, что хук запускает тихий рефреш по таймеру без ручного клика.
  it("вызывает reload с silent=true по интервалу", async () => {
    vi.useFakeTimers();
    const onReload = vi.fn().mockResolvedValue(undefined);

    render(<AutoRefreshProbe onReload={onReload} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(onReload).toHaveBeenCalledTimes(1);
    expect(onReload).toHaveBeenCalledWith(true);
  });

  // Проверяет, что хук не запускает второй polling, пока предыдущий запрос еще не завершился.
  it("не допускает параллельные автообновления", async () => {
    vi.useFakeTimers();
    let resolveReload: (() => void) | null = null;
    const onReload = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveReload = resolve;
        }),
    );

    render(<AutoRefreshProbe onReload={onReload} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(onReload).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(onReload).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveReload?.();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(5_000);
    });

    expect(onReload).toHaveBeenCalledTimes(2);
  });
});
