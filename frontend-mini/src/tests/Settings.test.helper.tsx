/**
 * Helper для теста SettingsPage — обёртка с QueryClient.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Card, Switch, Button, Badge, Skeleton, ErrorState } from "@/components/ui";
import {
  useObserverSettings, useToggleScanning, useTelegramSettings, useVisionSettings,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { useNavigate } from "@tanstack/react-router";
import type { ObserverConfig, TelegramSettings } from "@fb/shared";

function ObserverSection() {
  const { data, isLoading, isError, refetch } = useObserverSettings();
  const toggleScanning = useToggleScanning();

  if (isLoading) return <Card eyebrow="Observer" title="Сканирование"><Skeleton className="h-11" /></Card>;
  if (isError) return <Card eyebrow="Observer" title="Сканирование"><ErrorState message="Ошибка" onRetry={() => void refetch()} /></Card>;

  const cfg = data as ObserverConfig | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any

  async function handleToggle() {
    if (!cfg) return;
    haptic.impact("medium");
    await toggleScanning.mutateAsync({ enabled: !cfg.is_scanning_enabled });
  }

  return (
    <Card eyebrow="Observer" title="Сканирование">
      <div className="flex flex-col divide-y divide-[var(--color-bg-4)]">
        <div className="flex items-center justify-between min-h-[44px] gap-3">
          <div>
            <p className="text-[13px]">Мониторинг включён</p>
            <p className="text-[11px] text-[var(--color-bg-8)]">Бот сканирует объявления</p>
          </div>
          <Switch
            checked={cfg?.is_scanning_enabled ?? false}
            onChange={() => void handleToggle()}
            disabled={toggleScanning.isPending}
          />
        </div>
        <div className="flex items-center justify-between min-h-[44px] gap-3">
          <p className="text-[13px]">Авто-включение</p>
          <Badge variant={cfg?.auto_enable_recommendations ? "normal" : "neutral"}>
            {cfg?.auto_enable_recommendations ? "Вкл" : "Выкл"}
          </Badge>
        </div>
      </div>
    </Card>
  );
}

function TelegramSection() {
  const { data, isLoading, isError, refetch } = useTelegramSettings();
  if (isLoading) return <Card eyebrow="Telegram" title="Telegram Bot"><Skeleton className="h-24" /></Card>;
  if (isError) return <Card eyebrow="Telegram" title="Telegram Bot"><ErrorState message="Ошибка" onRetry={() => void refetch()} /></Card>;
  const tg = data as TelegramSettings | undefined;
  return (
    <Card eyebrow="Telegram" title="Telegram Bot">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px]">Статус</span>
          <Badge variant={tg?.is_authorized ? "normal" : "neutral"}>{tg?.is_authorized ? "Активен" : "Не настроен"}</Badge>
        </div>
        {tg?.bot_username && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px]">Бот</span>
            <span className="text-[12px] font-mono">@{tg.bot_username}</span>
          </div>
        )}
        {tg?.poller_status && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px]">Poller</span>
            <Badge variant={tg.poller_status === "ONLINE" ? "running" : "neutral"}>{tg.poller_status}</Badge>
          </div>
        )}
      </div>
    </Card>
  );
}

function VisionSection() {
  const { data, isLoading, isError, refetch } = useVisionSettings();
  if (isLoading) return <Card eyebrow="Vision" title="Браузер"><Skeleton className="h-16" /></Card>;
  if (isError) return <Card eyebrow="Vision" title="Браузер"><ErrorState message="Ошибка" onRetry={() => void refetch()} /></Card>;
  type VData = { profile_id?: string | null; cdp_ready?: boolean; has_token?: boolean };
  const v = data as VData | undefined;
  return (
    <Card eyebrow="Vision" title="Anti-detect браузер">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px]">Статус</span>
          <Badge variant={v?.cdp_ready ? "running" : "neutral"}>{v?.cdp_ready ? "CDP готов" : "Не готов"}</Badge>
        </div>
        {v?.profile_id && <p className="text-[12px] font-mono">{v.profile_id}</p>}
      </div>
    </Card>
  );
}

function TestSettingsPage() {
  const navigate = useNavigate();
  return (
    <div>
      <MiniHeader eyebrow="Конфигурация" title="Настройки" />
      <div className="p-4 flex flex-col gap-4">
        <ObserverSection />
        <TelegramSection />
        <VisionSection />
        <Card eyebrow="Разделы" title="Навигация" padding="sm">
          <div className="flex flex-col gap-2 mt-2">
            <Button variant="secondary" fullWidth onClick={() => void navigate({ to: "/health" })}>Здоровье воркеров</Button>
            <Button variant="secondary" fullWidth onClick={() => void navigate({ to: "/scripts" })}>Создание кампании</Button>
            <Button variant="secondary" fullWidth onClick={() => void navigate({ to: "/drafts" })}>Черновики задач</Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

export default function SettingsTestWrapper() {
  return (
    <QueryClientProvider client={qc}>
      <TestSettingsPage />
    </QueryClientProvider>
  );
}
