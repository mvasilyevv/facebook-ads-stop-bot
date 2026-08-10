import { createFileRoute } from "@tanstack/react-router";
import { MonitorUp, Smartphone } from "lucide-react";
import { useState } from "react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { tmaFetchApi } from "@/lib/auth";
import { haptic, openLink } from "@/lib/tg";

const DESKTOP_ORIGIN = "https://desktop.adpulse.su";

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

export const Route = createFileRoute("/desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const [isConnecting, setIsConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const handleOpen = async () => {
    haptic.impact("medium");
    setConnectionError(null);
    setIsConnecting(true);
    try {
      const { data: payload, response } = await tmaFetchApi.POST("/api/desktop/launch", {
        body: { presentation: "mobile" },
      });
      if (!response.ok || !payload?.url) {
        throw new Error("Сервер вернул некорректный билет рабочего стола.");
      }
      if (payload.transport !== "kasm") {
        throw new Error("Сервер вернул неизвестный transport рабочего стола.");
      }
      openLink(validateDesktopLaunchUrl(payload.url));
    } catch (error) {
      haptic.notify("error");
      setConnectionError(
        error instanceof Error ? error.message : "Не удалось открыть рабочий стол.",
      );
    } finally {
      setIsConnecting(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col pb-6">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="СИСТЕМА · УДАЛЁННЫЙ ДОСТУП"
        title="Рабочий стол"
      />

      <div className="flex flex-col gap-5 p-4">
        <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--color-hairline-strong)] bg-bg-1">
          <div className="flex min-h-[156px] flex-col items-center justify-center gap-3 border-b border-[var(--color-hairline)] px-5 py-6 text-center">
            <span className="flex size-14 items-center justify-center rounded-[var(--radius-3)] bg-bg-3 text-accent">
              <MonitorUp size={28} strokeWidth={1.5} aria-hidden="true" />
            </span>
            <div>
              <h2 className="font-display text-[20px] font-medium text-bg-11">Vision Desktop</h2>
              <p className="mt-1 text-[12px] leading-relaxed text-bg-9">
                Полный доступ к экрану, терминалу и приложениям сервера.
              </p>
            </div>
          </div>

          <div className="space-y-3 px-4 py-4">
            <div className="flex items-center gap-3 text-[12px] text-bg-10">
              <Smartphone size={16} strokeWidth={1.6} className="shrink-0 text-accent" />
              Откроется во внешнем браузере для корректной клавиатуры и жестов
            </div>
          </div>
        </section>

        <section>
          <Eyebrow className="mb-2.5 flex">ПОДКЛЮЧЕНИЕ</Eyebrow>
          <Button fullWidth size="lg" disabled={isConnecting} onClick={() => void handleOpen()}>
            <MonitorUp size={17} strokeWidth={1.7} aria-hidden="true" />
            {isConnecting ? "Подключение…" : connectionError ? "Повторить" : "Подключиться"}
          </Button>
          {connectionError ? (
            <p className="mt-3 text-center text-[12px] text-danger" role="alert">
              {connectionError}
            </p>
          ) : null}
        </section>
      </div>
    </div>
  );
}
