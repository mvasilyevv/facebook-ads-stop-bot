import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, MonitorUp, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { HeaderSep, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";

const REMOTE_DESKTOP_URL = "https://desktop.adpulse.su";
const DESKTOP_SESSION_URL = "/auth/desktop/session";

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const connectDesktop = async () => {
    setConnectionError(null);

    // Open synchronously so Safari treats this as a user-initiated popup. The
    // authenticated ticket request stays in the current page and the new tab
    // navigates directly to the desktop host instead of re-entering app.adpulse.su.
    const desktopWindow = window.open("about:blank", "_blank");
    if (!desktopWindow) {
      setConnectionError("Разрешите всплывающие окна для AdPulse и повторите подключение.");
      return;
    }
    desktopWindow.opener = null;
    setIsConnecting(true);

    try {
      const response = await fetch(DESKTOP_SESSION_URL, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const payload = (await response.json().catch(() => null)) as
        | { url?: unknown; detail?: unknown }
        | null;
      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : "Не удалось получить билет рабочего стола.",
        );
      }
      if (typeof payload?.url !== "string") {
        throw new Error("Сервер вернул некорректный билет рабочего стола.");
      }
      const launchUrl = new URL(payload.url);
      if (
        launchUrl.origin !== REMOTE_DESKTOP_URL ||
        launchUrl.pathname !== "/desktop-auth/redeem"
      ) {
        throw new Error("Сервер вернул небезопасный адрес рабочего стола.");
      }
      desktopWindow.location.replace(launchUrl.toString());
    } catch (error) {
      desktopWindow.close();
      setConnectionError(
        error instanceof Error ? error.message : "Не удалось открыть рабочий стол.",
      );
    } finally {
      setIsConnecting(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrowNum="04"
        eyebrow="SYSTEM · REMOTE"
        title="Рабочий стол"
        subtitle={
          <>
            Vision Server
            <HeaderSep />
            доступ по текущей авторизации панели
          </>
        }
      />

      <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--hairline-strong)] bg-bg-1">
        <div className="flex min-h-12 flex-wrap items-center justify-between gap-3 border-b border-[var(--hairline)] px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-[var(--radius-2)] bg-bg-3 text-accent">
              <MonitorUp size={17} strokeWidth={1.6} aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="font-display text-[13px] text-bg-11">Vision Desktop</p>
              <p className="truncate font-mono text-[11px] text-bg-8">{REMOTE_DESKTOP_URL}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 font-display text-[10px] uppercase tracking-[0.08em] text-success">
            <ShieldCheck size={14} strokeWidth={1.7} aria-hidden="true" />
            Защищённый доступ
          </div>
        </div>

        <div className="relative flex min-h-[360px] items-center justify-center overflow-hidden px-5 py-10 sm:min-h-[420px] sm:px-10 sm:py-12">
          <div
            aria-hidden="true"
            className="absolute inset-0 opacity-35"
            style={{
              backgroundImage:
                "linear-gradient(var(--hairline) 1px, transparent 1px), linear-gradient(90deg, var(--hairline) 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
          />
          <div className="relative max-w-[520px] text-center">
            <span className="mx-auto flex size-14 items-center justify-center rounded-[var(--radius-3)] border border-[var(--hairline-strong)] bg-bg-2 text-accent">
              <MonitorUp size={27} strokeWidth={1.45} aria-hidden="true" />
            </span>
            <h2 className="mt-5 font-display text-[20px] font-medium text-bg-11 sm:text-[22px]">
              Vision Desktop
            </h2>
            <p className="mx-auto mt-2 max-w-[450px] text-[13px] leading-relaxed text-bg-9">
              Панель выдаст одноразовый билет и откроет рабочий стол в новой вкладке. Дополнительный
              логин не потребуется.
            </p>
            <div className="mt-6 flex justify-center">
              <Button
                type="button"
                variant="primary"
                size="lg"
                className="min-w-44"
                leftIcon={<ExternalLink size={15} aria-hidden="true" />}
                disabled={isConnecting}
                onClick={connectDesktop}
              >
                {isConnecting ? "Подключение…" : "Подключиться"}
              </Button>
            </div>
            {connectionError ? (
              <p className="mt-3 text-[12px] text-danger" role="alert">
                {connectionError}
              </p>
            ) : null}
          </div>
        </div>
      </section>
    </>
  );
}
