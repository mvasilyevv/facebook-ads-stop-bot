import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, KeyRound, MonitorUp, Smartphone } from "lucide-react";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Button } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic, openLink } from "@/lib/tg";

const REMOTE_DESKTOP_URL =
  import.meta.env.VITE_REMOTE_DESKTOP_URL?.trim() || "https://desktop.adpulse.su";

export const Route = createFileRoute("/desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const handleOpen = () => {
    haptic.impact("medium");
    openLink(REMOTE_DESKTOP_URL);
  };

  return (
    <div className="flex min-h-full flex-col pb-6">
      <MiniHeader eyebrowNum="05" eyebrow="SYSTEM · REMOTE" title="Рабочий стол" />

      <div className="flex flex-col gap-5 p-4">
        <section className="overflow-hidden rounded-[var(--radius-3)] border border-[var(--hairline-strong)] bg-bg-1">
          <div className="flex min-h-[156px] flex-col items-center justify-center gap-3 border-b border-[var(--hairline)] px-5 py-6 text-center">
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
              <KeyRound size={16} strokeWidth={1.6} className="shrink-0 text-warning" />
              Внешний HTTPS-сервис запросит отдельный вход
            </div>
            <div className="flex items-center gap-3 text-[12px] text-bg-10">
              <Smartphone size={16} strokeWidth={1.6} className="shrink-0 text-accent" />
              Откроется во внешнем браузере для корректной клавиатуры и жестов
            </div>
          </div>
        </section>

        <section>
          <Eyebrow className="mb-2.5 flex">ПОДКЛЮЧЕНИЕ</Eyebrow>
          <Button fullWidth size="lg" onClick={handleOpen}>
            <ExternalLink size={17} strokeWidth={1.7} aria-hidden="true" />
            Подключиться к рабочему столу
          </Button>
          <p className="mt-2 break-all text-center font-mono text-[10px] text-bg-7">
            {REMOTE_DESKTOP_URL}
          </p>
        </section>
      </div>
    </div>
  );
}
