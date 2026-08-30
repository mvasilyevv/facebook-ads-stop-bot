import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  BarChart3,
  ChevronRight,
  FileText,
  Heart,
  ListChecks,
  MonitorUp,
} from "lucide-react";

import { Eyebrow } from "@fb/operator-ui";

import { Badge } from "@/components/ui";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  useObserverSettings,
  useOperatorDisplayPreference,
  useTelegramNotificationDiagnostics,
  useTelegramSettings,
  useVisionSettings,
} from "@/lib/api";
import { getStoredRole } from "@/lib/auth";
import { haptic } from "@/lib/tg";

type SettingsSection = "display" | "observer" | "telegram" | "vision";
type SettingsSectionRoute =
  | "/settings/display"
  | "/settings/observer"
  | "/settings/telegram"
  | "/settings/vision";

const SECTION_ROUTES: Record<SettingsSection, SettingsSectionRoute> = {
  display: "/settings/display",
  observer: "/settings/observer",
  telegram: "/settings/telegram",
  vision: "/settings/vision",
};

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

function SettingsPage() {
  const navigate = useNavigate();
  const canEdit = getStoredRole() === "owner";
  const displayPreferenceQuery = useOperatorDisplayPreference(canEdit);
  const observerQuery = useObserverSettings();
  const telegramQuery = useTelegramSettings();
  const diagnosticsQuery = useTelegramNotificationDiagnostics();
  const visionQuery = useVisionSettings();

  function openSection(next: SettingsSection) {
    haptic.selection();
    void navigate({ to: SECTION_ROUTES[next] });
  }

  function navTo(
    to:
      | "/desktop"
      | "/analytics"
      | "/system/sources"
      | "/campaigns"
      | "/offers",
  ) {
    haptic.selection();
    void navigate({ to });
  }

  const observerStatus = observerQuery.isError
    ? { label: "Недоступен", variant: "warning" as const }
    : observerQuery.isLoading || !observerQuery.data
      ? { label: "Загрузка…", variant: "neutral" as const }
      : observerQuery.data?.is_scanning_enabled
        ? { label: "Сканирует", variant: "neutral" as const }
        : { label: "Остановлен", variant: "warning" as const };
  const telegramStatus = telegramQuery.isError
    ? { label: "Недоступен", variant: "warning" as const }
    : telegramQuery.isLoading || !telegramQuery.data
      ? { label: "Загрузка…", variant: "neutral" as const }
      : telegramQuery.data?.is_authorized
        ? diagnosticsQuery.isLoading
          ? { label: "Проверяем…", variant: "neutral" as const }
          : diagnosticsQuery.isError
            ? { label: "Диагностика недоступна", variant: "warning" as const }
            : diagnosticsQuery.data?.outbox_state === "degraded"
              ? { label: "Деградация", variant: "failed" as const }
              : { label: "Настроен", variant: "neutral" as const }
        : { label: "Не настроен", variant: "warning" as const };
  const visionStatus = visionQuery.isError
    ? { label: "Недоступен", variant: "warning" as const }
    : visionQuery.isLoading || !visionQuery.data
      ? { label: "Загрузка…", variant: "neutral" as const }
      : visionQuery.data?.channel_status === "READY"
        ? { label: "Готов", variant: "neutral" as const }
        : visionQuery.data?.channel_status === "DEGRADED"
          ? { label: "Деградация", variant: "warning" as const }
          : visionQuery.data?.channel_status === "UNAVAILABLE"
            ? { label: "Недоступен", variant: "warning" as const }
            : { label: "Не подтверждён", variant: "neutral" as const };

  return (
    <div className="flex min-h-full flex-col pb-[max(96px,var(--tg-content-safe-bottom,0px),env(safe-area-inset-bottom))]">
      <MiniHeader
        eyebrowNum="04"
        eyebrow="СИСТЕМА · КОНФИГУРАЦИЯ"
        title="Ещё"
      />

      <div className="flex flex-col gap-7 px-4 py-5">
        {!canEdit ? (
          <p
            role="status"
            className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] leading-5 text-warning"
          >
            Этот запуск доступен только для чтения. Изменения настроек разрешены
            владельцу.
          </p>
        ) : null}

        <section aria-labelledby="settings-controls-heading">
          <h2 id="settings-controls-heading" className="m-0 mb-2.5">
            <Eyebrow num="05" className="flex">
              НАСТРОЙКИ
            </Eyebrow>
          </h2>
          <div className="border-y border-[var(--color-hairline)]">
            <SectionButton
              label="Отображение"
              detail={
                !canEdit
                  ? "Только владелец"
                  : displayPreferenceQuery.isPending
                    ? "Загрузка…"
                    : (displayPreferenceQuery.data?.timezone_name ??
                      "Недоступно")
              }
              status={
                <Badge
                  variant={
                    !canEdit || displayPreferenceQuery.isError
                      ? "warning"
                      : "neutral"
                  }
                >
                  {!canEdit
                    ? "Только owner"
                    : displayPreferenceQuery.isError
                      ? "Недоступно"
                      : "Web + TMA"}
                </Badge>
              }
              onClick={() => openSection("display")}
            />
            <SectionButton
              label="Observer"
              detail="Интервал, теги, allowlist и scan-now"
              status={
                <Badge variant={observerStatus.variant}>
                  {observerStatus.label}
                </Badge>
              }
              onClick={() => openSection("observer")}
            />
            <SectionButton
              label="Telegram"
              detail="Бот, получатели, preferences и диагностика"
              status={
                <Badge variant={telegramStatus.variant}>
                  {telegramStatus.label}
                </Badge>
              }
              onClick={() => openSection("telegram")}
            />
            <SectionButton
              label="Vision и desktop"
              detail="Профиль, секретный токен и переподключение"
              status={
                <Badge variant={visionStatus.variant}>
                  {visionStatus.label}
                </Badge>
              }
              onClick={() => openSection("vision")}
              noBorder
            />
          </div>
        </section>

        <section aria-labelledby="settings-routes-heading">
          <h2 id="settings-routes-heading" className="m-0 mb-2.5">
            <Eyebrow className="flex">РАЗДЕЛЫ</Eyebrow>
          </h2>
          <div className="border-y border-[var(--color-hairline)]">
            <NavRow
              icon={<MonitorUp size={17} />}
              label="Рабочий стол"
              onClick={() => navTo("/desktop")}
            />
            <NavRow
              icon={<BarChart3 size={17} />}
              label="Аналитика"
              onClick={() => navTo("/analytics")}
            />
            <NavRow
              icon={<Heart size={17} />}
              label="Источники и воркеры"
              onClick={() => navTo("/system/sources")}
            />
            <NavRow
              icon={<ListChecks size={17} />}
              label="Запуски кампаний"
              onClick={() => navTo("/campaigns")}
            />
            <NavRow
              icon={<FileText size={17} />}
              label="Офферы"
              onClick={() => navTo("/offers")}
              noBorder
            />
          </div>
        </section>
      </div>
    </div>
  );
}

function SectionButton({
  label,
  detail,
  status,
  onClick,
  noBorder = false,
}: {
  label: string;
  detail: string;
  status: React.ReactNode;
  onClick: () => void;
  noBorder?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex min-h-[64px] w-full items-center gap-3 py-3 text-left active:bg-bg-2 ${
        noBorder ? "" : "border-b border-[var(--color-hairline)]"
      }`}
    >
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium text-bg-11">
          {label}
        </span>
        <span className="mt-1 block break-words text-[13px] leading-5 text-bg-8">
          {detail}
        </span>
      </span>
      <span className="flex max-w-[45%] shrink-0 items-center gap-2">
        {status}
        <ChevronRight size={16} aria-hidden="true" className="text-bg-8" />
      </span>
    </button>
  );
}

function NavRow({
  icon,
  label,
  onClick,
  noBorder = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  noBorder?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex min-h-11 w-full items-center gap-3 py-2.5 text-left active:bg-bg-2 ${
        noBorder ? "" : "border-b border-[var(--color-hairline)]"
      }`}
    >
      <span className="shrink-0 text-bg-9" aria-hidden="true">
        {icon}
      </span>
      <span className="flex-1 text-[14px] text-bg-11">{label}</span>
      <ChevronRight
        size={16}
        aria-hidden="true"
        className="shrink-0 text-bg-8"
      />
    </button>
  );
}
