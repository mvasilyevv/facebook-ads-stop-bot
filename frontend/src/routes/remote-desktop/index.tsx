import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import {
  ExternalLink,
  KeyRound,
  LoaderCircle,
  Maximize2,
  MonitorUp,
  PanelTopClose,
} from "lucide-react";
import { PageHeader, HeaderSep } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/Button";

const REMOTE_DESKTOP_URL =
  import.meta.env.VITE_REMOTE_DESKTOP_URL?.trim() || "https://desktop.adpulse.su";

export const Route = createFileRoute("/remote-desktop/")({
  component: RemoteDesktopPage,
});

function RemoteDesktopPage() {
  const [embedVisible, setEmbedVisible] = useState(false);
  const [embedResponded, setEmbedResponded] = useState(false);

  const openDesktop = () => {
    window.open(REMOTE_DESKTOP_URL, "_blank", "noopener,noreferrer");
  };

  const showEmbed = () => {
    setEmbedResponded(false);
    setEmbedVisible(true);
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
            внешний HTTPS-сервис с отдельной авторизацией
          </>
        }
        action={
          <Button
            variant="primary"
            size="md"
            leftIcon={<ExternalLink size={14} aria-hidden="true" />}
            onClick={openDesktop}
          >
            Открыть отдельно
          </Button>
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
          <div className="flex shrink-0 items-center gap-1.5 font-display text-[10px] uppercase tracking-[0.08em] text-warning">
            <KeyRound size={14} strokeWidth={1.7} aria-hidden="true" />
            Требуется вход
          </div>
        </div>

        {embedVisible ? (
          <div>
            <div className="flex flex-col gap-3 border-b border-[var(--hairline)] bg-bg-2 px-4 py-3 text-[12px] text-bg-9 sm:flex-row sm:items-center sm:justify-between">
              <p>
                Встроенный режим не может проверить Basic Auth из-за cross-origin ограничений.
                При пустом экране используйте отдельное окно.
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="self-start sm:self-auto"
                leftIcon={<PanelTopClose size={14} aria-hidden="true" />}
                onClick={() => setEmbedVisible(false)}
              >
                Скрыть
              </Button>
            </div>
            <div
              className="relative min-h-[480px] bg-bg-0"
              style={{ height: "max(480px, calc(100vh - 310px))" }}
            >
              {!embedResponded ? (
                <div
                  className="pointer-events-none absolute inset-0 z-[1] flex items-center justify-center bg-bg-0"
                  role="status"
                  aria-live="polite"
                >
                  <span className="inline-flex items-center gap-2 font-display text-[12px] text-bg-9">
                    <LoaderCircle className="animate-spin" size={16} aria-hidden="true" />
                    Запрашиваю встроенную страницу…
                  </span>
                </div>
              ) : null}
              <iframe
                src={REMOTE_DESKTOP_URL}
                title="Vision Desktop — встроенный режим"
                allow="clipboard-read; clipboard-write; fullscreen"
                allowFullScreen
                onLoad={() => setEmbedResponded(true)}
                className="size-full border-0"
              />
            </div>
          </div>
        ) : (
          <div className="relative flex min-h-[420px] items-center justify-center overflow-hidden px-5 py-12 sm:px-10">
            <div
              aria-hidden="true"
              className="absolute inset-0 opacity-35"
              style={{
                backgroundImage:
                  "linear-gradient(var(--hairline) 1px, transparent 1px), linear-gradient(90deg, var(--hairline) 1px, transparent 1px)",
                backgroundSize: "32px 32px",
              }}
            />
            <div className="relative max-w-[560px] text-center">
              <span className="mx-auto flex size-14 items-center justify-center rounded-[var(--radius-3)] border border-[var(--hairline-strong)] bg-bg-2 text-accent">
                <MonitorUp size={27} strokeWidth={1.45} aria-hidden="true" />
              </span>
              <h2 className="mt-5 font-display text-[22px] font-medium text-bg-11">
                Подключение открывается отдельно
              </h2>
              <p className="mx-auto mt-2 max-w-[470px] text-[13px] leading-relaxed text-bg-9">
                Так браузер корректно покажет окно авторизации и передаст клавиатуру,
                буфер обмена и полноэкранный режим удалённому рабочему столу.
              </p>
              <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
                <Button
                  variant="primary"
                  size="lg"
                  leftIcon={<ExternalLink size={15} aria-hidden="true" />}
                  onClick={openDesktop}
                >
                  Подключиться
                </Button>
                <Button
                  variant="secondary"
                  size="lg"
                  leftIcon={<Maximize2 size={15} aria-hidden="true" />}
                  onClick={showEmbed}
                >
                  Попробовать внутри
                </Button>
              </div>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
