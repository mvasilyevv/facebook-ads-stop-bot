/**
 * SettingsPage («Ещё») — конфигурация + навигация к вторичным экранам.
 * Канон: MiniHeader (eyebrowNum) → РАЗДЕЛЫ → OBSERVER → TELEGRAM → VISION.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import {
  ChevronRight,
  Heart,
  FileText,
  BarChart3,
  MonitorUp,
  ListChecks,
} from "lucide-react";
import {
  useTelegramSettings,
  useTelegramNotificationDiagnostics,
  useVisionSettings,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data";
import { Badge, Skeleton, ErrorState } from "@/components/ui";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

// ─── Field-строка: label + control, border-b ─────────────────────────────

interface FieldRowProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
  noBorder?: boolean;
}

function FieldRow({ label, hint, children, noBorder = false }: FieldRowProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 min-h-[44px] py-2.5",
        !noBorder && "border-b border-[var(--color-hairline)]",
      )}
    >
      <div className="min-w-0">
        <p className="text-[13px] text-bg-11 leading-tight">{label}</p>
        {hint && (
          <p className="text-[12px] text-bg-8 mt-0.5 leading-tight">{hint}</p>
        )}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

// ─── Секция-обёртка: Eyebrow + контент ────────────────────────────────────

interface SectionProps {
  eyebrow: string;
  num?: string;
  children: React.ReactNode;
}

function Section({ eyebrow, num, children }: SectionProps) {
  return (
    <section>
      <Eyebrow num={num} className="mb-2.5 flex">
        {eyebrow}
      </Eyebrow>
      <div className="border border-[var(--color-hairline)] bg-bg-1 px-4 rounded-[var(--radius-3)]">
        {children}
      </div>
    </section>
  );
}

// ─── Навигационная строка-ссылка ──────────────────────────────────────────

interface NavRowProps {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  noBorder?: boolean;
}

function NavRow({ icon, label, onClick, noBorder = false }: NavRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-3 min-h-[44px] py-2.5 text-left",
        "active:bg-bg-2 transition-colors",
        !noBorder && "border-b border-[var(--color-hairline)]",
      )}
    >
      <span className="text-bg-9 shrink-0">{icon}</span>
      <span className="flex-1 text-[14px] text-bg-11">{label}</span>
      <ChevronRight
        size={16}
        strokeWidth={1.5}
        className="shrink-0 text-bg-8"
      />
    </button>
  );
}

// ─── Секция TELEGRAM ──────────────────────────────────────────────────────

function TelegramSection() {
  const { data, isLoading, isError, error, refetch } = useTelegramSettings();
  const diagnosticsQuery = useTelegramNotificationDiagnostics();

  if (isLoading) {
    return (
      <Section eyebrow="TELEGRAM" num="07">
        <div className="py-2 space-y-3">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      </Section>
    );
  }

  if (isError) {
    return (
      <Section eyebrow="TELEGRAM" num="07">
        <ErrorState message={String(error)} onRetry={() => void refetch()} />
      </Section>
    );
  }

  const diagnostics = diagnosticsQuery.data;
  const activeOutbox = diagnostics
    ? [
        diagnostics.inbox_counts,
        diagnostics.delivery_counts,
        diagnostics.command_reply_counts,
      ].reduce(
        (total, counts) =>
          total +
          (counts.pending ?? 0) +
          (counts.retry ?? 0) +
          (counts.leased ?? 0),
        0,
      )
    : null;
  const failedOutbox = diagnostics
    ? [
        diagnostics.inbox_counts,
        diagnostics.delivery_counts,
        diagnostics.command_reply_counts,
      ].reduce(
        (total, counts) => total + (counts.dead ?? 0) + (counts.unknown ?? 0),
        0,
      )
    : null;

  return (
    <Section eyebrow="TELEGRAM" num="07">
      <FieldRow label="Токен">
        <Badge variant="neutral">
          {data?.is_authorized ? "Настроен" : "Не настроен"}
        </Badge>
      </FieldRow>

      {data?.bot_username ? (
        <FieldRow label="Бот">
          <span className="font-mono text-[12px] text-bg-10">
            @{data.bot_username}
          </span>
        </FieldRow>
      ) : null}

      <FieldRow label="Webhook">
        <Badge
          variant={
            diagnostics?.webhook_state === "unconfigured"
              ? "warning"
              : "neutral"
          }
        >
          {diagnosticsQuery.isError
            ? "Недоступен"
            : diagnostics?.webhook_state === "configured"
              ? "Настроен"
              : diagnostics?.webhook_state === "unconfigured"
                ? "Не настроен"
                : "Проверка…"}
        </Badge>
      </FieldRow>

      <FieldRow label="Gateway">
        <Badge
          variant={
            diagnostics?.gateway_state === "auth_error" ? "failed" : "neutral"
          }
        >
          {diagnosticsQuery.isError
            ? "Недоступен"
            : diagnostics?.gateway_state === "configured"
              ? "Настроен"
              : diagnostics?.gateway_state === "auth_error"
                ? "Ошибка авторизации"
                : diagnostics?.gateway_state === "unconfigured"
                  ? "Не настроен"
                  : "Проверка…"}
        </Badge>
      </FieldRow>

      <FieldRow label="Outbox" noBorder>
        <Badge
          variant={
            diagnostics?.outbox_state === "degraded"
              ? "failed"
              : diagnostics?.outbox_state === "active"
                ? "warning"
                : diagnostics?.outbox_state === "idle"
                  ? "done"
                  : "neutral"
          }
        >
          {diagnosticsQuery.isError
            ? "Недоступен"
            : diagnostics?.outbox_state === "degraded"
              ? `${failedOutbox ?? 0} ошибок`
              : diagnostics?.outbox_state === "active"
                ? `${activeOutbox ?? 0} в работе`
                : diagnostics?.outbox_state === "idle"
                  ? "Очередь пуста"
                  : "Проверка…"}
        </Badge>
      </FieldRow>
    </Section>
  );
}

// ─── Секция VISION ────────────────────────────────────────────────────────

function VisionSection() {
  const { data, isLoading, isError, error, refetch } = useVisionSettings();

  if (isLoading) {
    return (
      <Section eyebrow="VISION" num="08">
        <div className="py-2 space-y-3">
          <Skeleton className="h-11 w-full" />
        </div>
      </Section>
    );
  }

  if (isError) {
    return (
      <Section eyebrow="VISION" num="08">
        <ErrorState message={String(error)} onRetry={() => void refetch()} />
      </Section>
    );
  }

  const statusVariant =
    data?.channel_status === "READY"
      ? "running"
      : data?.channel_status === "DEGRADED" ||
          data?.channel_status === "UNAVAILABLE"
        ? "warning"
        : "neutral";
  const statusLabel =
    data?.channel_status === "READY"
      ? "Канал готов"
      : data?.channel_status === "DEGRADED"
        ? "Канал деградирован"
        : data?.channel_status === "UNAVAILABLE"
          ? "Канал недоступен"
          : data?.has_token
            ? "Статус не подтверждён"
            : "Не настроен";

  return (
    <Section eyebrow="VISION" num="08">
      <FieldRow label="Browser channel">
        <Badge variant={statusVariant}>{statusLabel}</Badge>
      </FieldRow>

      {data?.profile_id ? (
        <FieldRow label="Profile ID" noBorder>
          <span className="font-mono text-[12px] text-bg-9 truncate max-w-[120px]">
            {data.profile_id}
          </span>
        </FieldRow>
      ) : (
        <FieldRow label="Profile ID" noBorder>
          <span className="text-[12px] text-bg-8">—</span>
        </FieldRow>
      )}
    </Section>
  );
}

// ─── SettingsPage ─────────────────────────────────────────────────────────

function SettingsPage() {
  const navigate = useNavigate();

  const navTo = (to: string) => {
    haptic.selection();
    void navigate({ to: to as "/" });
  };

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="СИСТЕМА · МОБИЛЬНЫЙ ДОСТУП"
        title="Ещё"
      />

      <div className="flex flex-col gap-5 p-4">
        {/* ── РАЗДЕЛЫ ── */}
        <section>
          <Eyebrow num="05" className="mb-2.5 flex">
            РАЗДЕЛЫ
          </Eyebrow>
          <div className="border border-[var(--color-hairline)] bg-bg-1 px-4 rounded-[var(--radius-3)]">
            <NavRow
              icon={<MonitorUp size={16} strokeWidth={1.5} />}
              label="Рабочий стол"
              onClick={() => navTo("/desktop")}
            />
            <NavRow
              icon={<BarChart3 size={16} strokeWidth={1.5} />}
              label="Аналитика"
              onClick={() => navTo("/analytics")}
            />
            <NavRow
              icon={<Heart size={16} strokeWidth={1.5} />}
              label="Источники и воркеры"
              onClick={() => navTo("/system/sources")}
            />
            <NavRow
              icon={<ListChecks size={16} strokeWidth={1.5} />}
              label="Запуски кампаний"
              onClick={() => navTo("/campaigns")}
            />
            <NavRow
              icon={<FileText size={16} strokeWidth={1.5} />}
              label="Офферы"
              onClick={() => navTo("/offers")}
              noBorder
            />
          </div>
        </section>

        <p className="rounded-[var(--radius-3)] border border-[var(--color-hairline)] bg-bg-1 p-4 text-[14px] leading-5 text-bg-9">
          Здесь доступны диагностика и оперативные экраны. Редкие настройки
          сканирования, кабинетов и расписаний изменяются в web-панели.
        </p>

        {/* Read-only diagnostics. Configuration remains desktop-first. */}
        <TelegramSection />
        <VisionSection />
      </div>
    </div>
  );
}
