import { createFileRoute } from "@tanstack/react-router";
import { MonitorUp, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { HeaderSep, PageHeader } from "@/components/layout/PageHeader";
import { buttonStyles } from "@/components/ui/Button";
import { generatedFetchApi } from "@/lib/api/generatedClient";
import { cn } from "@/lib/utils/cn";

const DESKTOP_ORIGIN = "https://desktop.adpulse.su";
// Ссылка-триггер: настоящий клик по <a target="_blank"> открывает новую вкладку
// синхронно, а сам запуск (POST /desktop/launch + редирект на билет) происходит
// уже ВНУТРИ новой вкладки. Так нет async-разрыва между жестом и открытием, из-за
// которого Safari оставлял предоткрытый попап на about:blank.
const LAUNCH_HREF = "/remote-desktop?launch=1";

const ctaClassName = cn(buttonStyles({ variant: "primary", size: "lg" }), "min-w-44");

function validateDesktopLaunchUrl(rawUrl: string): string {
  const url = new URL(rawUrl, DESKTOP_ORIGIN);
  if (
    url.origin !== DESKTOP_ORIGIN ||
    url.pathname !== "/desktop-auth/redeem" ||
    !url.searchParams.get("ticket")
  ) {
    throw new Error("Сервер вернул некорректный билет рабочего стола.");
  }
  return url.toString();
}

export const desktopNavigation = {
  replace(url: string): void {
    window.location.replace(url);
  },
};

function isLaunchRequested(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return new URLSearchParams(window.location.search).get("launch") === "1";
}

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const launching = isLaunchRequested();
  const [launchError, setLaunchError] = useState<string | null>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    // Запуск исполняется только в открытой вкладке (?launch=1) и ровно один раз.
    if (!launching || startedRef.current) {
      return;
    }
    startedRef.current = true;
    let cancelled = false;

    void (async () => {
      try {
        const { data: payload, response } = await generatedFetchApi.POST("/api/desktop/launch", {
          body: { presentation: "desktop" },
        });
        if (!response.ok || !payload?.url) {
          throw new Error("Сервер вернул некорректный билет рабочего стола.");
        }
        if (payload.transport !== "kasm") {
          throw new Error("Сервер вернул неизвестный transport рабочего стола.");
        }
        const launchUrl = validateDesktopLaunchUrl(payload.url);
        if (!cancelled) {
          desktopNavigation.replace(launchUrl);
        }
      } catch (error) {
        if (!cancelled) {
          setLaunchError(
            error instanceof Error ? error.message : "Не удалось открыть рабочий стол.",
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [launching]);

  return (
    <>
      <PageHeader
        title="Рабочий стол"
        subtitle={
          <>
            Vision Server
            <HeaderSep />
            доступ по текущей авторизации панели
          </>
        }
      />

      <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1">
        <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-hairline)] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-3 text-accent">
              <MonitorUp size={17} strokeWidth={1.6} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="font-display text-[13px] text-bg-11">Vision Server</p>
              <p className="truncate text-[12px] text-bg-8">Единый защищённый контур</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 font-display text-[12px] uppercase tracking-[0.08em] text-success">
            <ShieldCheck size={14} strokeWidth={1.7} aria-hidden="true" />
            Защищённый доступ
          </div>
        </div>

        {/* Сетчатый фон убран: он ничего не означал, а на экране, где всё
            остальное значит хоть что-то, украшение читается как индикатор. */}
        <div className="flex min-h-[360px] items-center justify-center px-5 py-10 sm:min-h-[420px] sm:px-10 sm:py-12">
          <div className="max-w-[520px] text-center">
            <span className="mx-auto flex size-14 items-center justify-center rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-2 text-accent">
              <MonitorUp size={27} strokeWidth={1.45} aria-hidden="true" />
            </span>
            <h2 className="mt-5 font-display text-[20px] font-medium text-bg-11 sm:text-[22px]">
              Подключение к рабочему столу
            </h2>

            {launching ? (
              launchError ? (
                <>
                  <p
                    className="mx-auto mt-2 max-w-[450px] text-[13px] leading-relaxed text-danger"
                    role="alert"
                  >
                    {launchError}
                  </p>
                  <div className="mt-6 flex justify-center">
                    <a href={LAUNCH_HREF} className={ctaClassName}>
                      <MonitorUp size={15} aria-hidden="true" />
                      Повторить
                    </a>
                  </div>
                </>
              ) : (
                <p className="mx-auto mt-2 flex max-w-[450px] items-center justify-center gap-2 text-[13px] leading-relaxed text-bg-9">
                  <span
                    aria-hidden="true"
                    className="inline-block size-3.5 animate-spin rounded-full border border-current border-r-transparent"
                  />
                  Открываем рабочий стол…
                </p>
              )
            ) : (
              <>
                <p className="mx-auto mt-2 max-w-[450px] text-[13px] leading-relaxed text-bg-9">
                  Подключение откроется в новой вкладке. Дополнительный логин не потребуется.
                </p>
                <div className="mt-6 flex justify-center">
                  <a
                    href={LAUNCH_HREF}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={ctaClassName}
                  >
                    <MonitorUp size={15} aria-hidden="true" />
                    Подключиться
                  </a>
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    </>
  );
}
