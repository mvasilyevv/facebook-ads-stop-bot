/**
 * SettingsPage — настройки Observer, Telegram, Vision + навигация.
 * TabBar "Ещё" ведёт сюда — здесь же ссылки на /health, /scripts, /drafts.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { MiniHeader } from "@/components/layout/MiniHeader";
import {
  Card,
  Switch,
  Button,
  Badge,
  Skeleton,
  ErrorState,
} from "@/components/ui";
import {
  useObserverSettings,
  useToggleScanning,
  useTelegramSettings,
  useVisionSettings,
} from "@/lib/api";
import { haptic } from "@/lib/tg";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

// ─── Observer-секция ─────────────────────────────────────────────────────

function ObserverSection() {
  const { data, isLoading, isError, refetch } = useObserverSettings();
  const toggleScanning = useToggleScanning();

  if (isLoading) {
    return (
      <Card eyebrow="Observer" title="Сканирование">
        <Skeleton className="h-11" />
      </Card>
    );
  }

  if (isError) {
    return (
      <Card eyebrow="Observer" title="Сканирование">
        <ErrorState message="Не удалось загрузить настройки" onRetry={() => void refetch()} />
      </Card>
    );
  }

  async function handleToggle() {
    if (!data) return;
    haptic.impact("medium");
    try {
      await toggleScanning.mutateAsync({ enabled: !data.is_scanning_enabled });
      haptic.notify("success");
    } catch {
      haptic.notify("error");
    }
  }

  return (
    <Card eyebrow="Observer" title="Сканирование">
      <div className="flex flex-col divide-y divide-[var(--color-bg-4)]">
        <div className="flex items-center justify-between min-h-[44px] gap-3">
          <div>
            <p className="text-[13px] text-[var(--color-bg-11)]">Мониторинг включён</p>
            <p className="text-[11px] text-[var(--color-bg-8)]">Бот сканирует объявления</p>
          </div>
          <Switch
            checked={data?.is_scanning_enabled ?? false}
            onChange={() => void handleToggle()}
            disabled={toggleScanning.isPending}
          />
        </div>
        <div className="flex items-center justify-between min-h-[44px] gap-3">
          <div>
            <p className="text-[13px] text-[var(--color-bg-11)]">Авто-включение</p>
            <p className="text-[11px] text-[var(--color-bg-8)]">Рекомендации recovery</p>
          </div>
          <Badge variant={data?.auto_enable_recommendations ? "normal" : "neutral"}>
            {data?.auto_enable_recommendations ? "Вкл" : "Выкл"}
          </Badge>
        </div>
      </div>
    </Card>
  );
}

// ─── Telegram-секция ─────────────────────────────────────────────────────

function TelegramSection() {
  const { data, isLoading, isError, refetch } = useTelegramSettings();

  if (isLoading) {
    return (
      <Card eyebrow="Telegram" title="Telegram Bot">
        <Skeleton className="h-24" />
      </Card>
    );
  }

  if (isError) {
    return (
      <Card eyebrow="Telegram" title="Telegram Bot">
        <ErrorState message="Не удалось загрузить" onRetry={() => void refetch()} />
      </Card>
    );
  }

  return (
    <Card eyebrow="Telegram" title="Telegram Bot">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-[var(--color-bg-9)]">Статус авторизации</span>
          <Badge variant={data?.is_authorized ? "normal" : "neutral"}>
            {data?.is_authorized ? "Активен" : "Не настроен"}
          </Badge>
        </div>
        {data?.bot_username && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] text-[var(--color-bg-9)]">Бот</span>
            <span className="text-[12px] font-mono text-[var(--color-bg-11)]">
              @{data.bot_username}
            </span>
          </div>
        )}
        {data?.poller_status && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] text-[var(--color-bg-9)]">Poller</span>
            <Badge variant={data.poller_status === "ONLINE" ? "running" : "neutral"}>
              {data.poller_status}
            </Badge>
          </div>
        )}
        {data?.web_app_url && (
          <div>
            <p className="text-[11px] text-[var(--color-bg-8)] uppercase tracking-wide mb-1">
              Web App URL
            </p>
            <p className="text-[12px] font-mono text-[var(--color-bg-10)] break-all">
              {data.web_app_url}
            </p>
          </div>
        )}
        {!data?.is_authorized && data?.activation_command && (
          <p className="text-[12px] text-[var(--color-bg-8)]">
            Активируйте бота командой{" "}
            <span className="font-mono text-[var(--color-accent)]">{data.activation_command}</span>
          </p>
        )}
      </div>
    </Card>
  );
}

// ─── Vision-секция ────────────────────────────────────────────────────────

function VisionSection() {
  const { data, isLoading, isError, refetch } = useVisionSettings();

  if (isLoading) {
    return (
      <Card eyebrow="Vision" title="Anti-detect браузер">
        <Skeleton className="h-16" />
      </Card>
    );
  }

  if (isError) {
    return (
      <Card eyebrow="Vision" title="Anti-detect браузер">
        <ErrorState message="Не удалось загрузить" onRetry={() => void refetch()} />
      </Card>
    );
  }

  const statusVariant = data?.cdp_ready ? "running" : data?.has_token ? "warning" : "neutral";
  const statusLabel = data?.cdp_ready
    ? "CDP готов"
    : data?.has_token
      ? "Токен есть, CDP не готов"
      : "Не настроен";

  return (
    <Card eyebrow="Vision" title="Anti-detect браузер">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-[var(--color-bg-9)]">Статус</span>
          <Badge variant={statusVariant}>{statusLabel}</Badge>
        </div>
        {data?.profile_id && (
          <div>
            <p className="text-[11px] text-[var(--color-bg-8)] uppercase tracking-wide mb-1">
              Profile ID
            </p>
            <p className="text-[12px] font-mono text-[var(--color-bg-10)]">{data.profile_id}</p>
          </div>
        )}
        {data?.runtime_status_message && (
          <p className="text-[12px] text-[var(--color-bg-8)]">{data.runtime_status_message}</p>
        )}
        {data?.cdp_port && (
          <p className="text-[12px] text-[var(--color-bg-8)]">
            CDP порт:{" "}
            <span className="font-mono text-[var(--color-bg-10)]">{data.cdp_port}</span>
          </p>
        )}
      </div>
    </Card>
  );
}

// ─── SettingsPage ─────────────────────────────────────────────────────────

function SettingsPage() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader eyebrow="Конфигурация" title="Настройки" />

      <div className="p-4 flex flex-col gap-4">
        {/* Секции настроек */}
        <ObserverSection />
        <TelegramSection />
        <VisionSection />

        {/* Навигационные ссылки */}
        <Card eyebrow="Разделы" title="Навигация" padding="sm">
          <div className="flex flex-col gap-2 mt-2">
            <Button
              variant="secondary"
              fullWidth
              onClick={() => void navigate({ to: "/health" })}
            >
              Здоровье воркеров
            </Button>
            <Button
              variant="secondary"
              fullWidth
              onClick={() => void navigate({ to: "/scripts" })}
            >
              Создание кампании
            </Button>
            <Button
              variant="secondary"
              fullWidth
              onClick={() => void navigate({ to: "/drafts" })}
            >
              Черновики задач
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
