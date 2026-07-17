import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, MonitorUp, ShieldCheck } from "lucide-react";
import { HeaderSep, PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";

const REMOTE_DESKTOP_URL = "https://desktop.adpulse.su";
const DESKTOP_LAUNCH_URL = "/auth/desktop/launch";

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
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
              <form
                action={DESKTOP_LAUNCH_URL}
                method="get"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  className="min-w-44"
                  leftIcon={<ExternalLink size={15} aria-hidden="true" />}
                >
                  Подключиться
                </Button>
              </form>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
