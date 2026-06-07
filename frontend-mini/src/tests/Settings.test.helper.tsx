/**
 * Helper для теста SettingsPage — дублирует логику компонента с QueryClient.
 * Обновлён под канон: РАЗДЕЛЫ + OBSERVER + TELEGRAM + VISION.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { ChevronRight, Heart, FileCode, FileText, RefreshCw } from "lucide-react";
import type { ObserverConfig, TelegramSettings } from "@fb/shared";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data";
import { Badge, Button, Skeleton, ErrorState, Switch, Input } from "@/components/ui";
import {
  useObserverSettings,
  useToggleScanning,
  useTriggerScan,
  useTelegramSettings,
  useVisionSettings,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { useNavigate } from "@tanstack/react-router";
import { cn } from "@/lib/cn";

// ─── Field-строка ────────────────────────────────────────────────────────

function FieldRow({
  label,
  hint,
  children,
  noBorder = false,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  noBorder?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 min-h-[44px] py-2.5",
        !noBorder && "border-b border-bg-5",
      )}
    >
      <div className="min-w-0">
        <p className="text-[13px] text-bg-11 leading-tight">{label}</p>
        {hint && <p className="text-[11px] text-bg-8 mt-0.5 leading-tight">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

// ─── Секция ────────────────────────────────────────────────────────────────

function Section({
  eyebrow,
  num,
  children,
}: {
  eyebrow: string;
  num?: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <Eyebrow num={num} className="mb-2.5 flex">
        {eyebrow}
      </Eyebrow>
      <div className="border border-bg-5 bg-bg-1 px-4">{children}</div>
    </section>
  );
}

// ─── ObserverSection ──────────────────────────────────────────────────────

function ObserverSection() {
  const { data, isLoading, isError, refetch } = useObserverSettings();
  const toggleScanning = useToggleScanning();
  const triggerScan = useTriggerScan();
  const [ownerTag, setOwnerTag] = useState<string>("");

  useEffect(() => {
    if (data) setOwnerTag((data as ObserverConfig).owner_campaign_tag ?? "");
  }, [data]);

  if (isLoading)
    return (
      <Section eyebrow="OBSERVER">
        <Skeleton className="h-11 w-full" />
      </Section>
    );
  if (isError)
    return (
      <Section eyebrow="OBSERVER">
        <ErrorState message="Ошибка" onRetry={() => void refetch()} />
      </Section>
    );

  const cfg = data as ObserverConfig;

  return (
    <Section eyebrow="OBSERVER">
      <FieldRow label="Сканирование" hint="Observer периодически сканирует объявления">
        <Switch
          checked={cfg.is_scanning_enabled}
          onChange={() => {
            haptic.impact("medium");
            void toggleScanning.mutateAsync({ enabled: !cfg.is_scanning_enabled });
          }}
          disabled={toggleScanning.isPending}
        />
      </FieldRow>
      <FieldRow label="Owner Campaign Tag" noBorder>
        <Input
          aria-label="Owner Campaign Tag"
          placeholder="MV,ABC"
          value={ownerTag}
          onChange={(e) => setOwnerTag(e.target.value)}
          className="w-[120px]"
        />
      </FieldRow>
      <div className="py-3">
        <Button
          variant="secondary"
          fullWidth
          onClick={() => void triggerScan.mutateAsync()}
          disabled={triggerScan.isPending}
          aria-label="Сканировать сейчас"
        >
          <RefreshCw size={15} strokeWidth={1.6} className="shrink-0" />
          Сканировать сейчас
        </Button>
      </div>
    </Section>
  );
}

// ─── TelegramSection ──────────────────────────────────────────────────────

function TelegramSection() {
  const { data, isLoading, isError, refetch } = useTelegramSettings();
  if (isLoading)
    return (
      <Section eyebrow="TELEGRAM">
        <Skeleton className="h-11 w-full" />
      </Section>
    );
  if (isError)
    return (
      <Section eyebrow="TELEGRAM">
        <ErrorState message="Ошибка" onRetry={() => void refetch()} />
      </Section>
    );

  const tg = data as TelegramSettings | undefined;
  const pollerVariant = tg?.poller_status === "ONLINE" ? "running" : "neutral";

  return (
    <Section eyebrow="TELEGRAM">
      <FieldRow label="Авторизация">
        <Badge variant={tg?.is_authorized ? "done" : "neutral"}>
          {tg?.is_authorized ? "Активен" : "Не настроен"}
        </Badge>
      </FieldRow>
      {tg?.bot_username ? (
        <FieldRow label="Бот">
          <span className="font-mono text-[12px] text-bg-10">@{tg.bot_username}</span>
        </FieldRow>
      ) : null}
      <FieldRow label="Poller" noBorder>
        <Badge variant={tg?.poller_status ? pollerVariant : "neutral"}>
          {tg?.poller_status ?? "—"}
        </Badge>
      </FieldRow>
    </Section>
  );
}

// ─── VisionSection ────────────────────────────────────────────────────────

function VisionSection() {
  const { data, isLoading, isError, refetch } = useVisionSettings();
  if (isLoading)
    return (
      <Section eyebrow="VISION">
        <Skeleton className="h-11 w-full" />
      </Section>
    );
  if (isError)
    return (
      <Section eyebrow="VISION">
        <ErrorState message="Ошибка" onRetry={() => void refetch()} />
      </Section>
    );

  type VData = { profile_id?: string | null; cdp_ready?: boolean; has_token?: boolean };
  const v = data as VData | undefined;
  const statusVariant = v?.cdp_ready ? "running" : "neutral";

  return (
    <Section eyebrow="VISION">
      <FieldRow label="Статус CDP">
        <Badge variant={statusVariant}>{v?.cdp_ready ? "CDP готов" : "Не готов"}</Badge>
      </FieldRow>
      <FieldRow label="Profile ID" noBorder>
        <span className="font-mono text-[12px] text-bg-9">{v?.profile_id ?? "—"}</span>
      </FieldRow>
    </Section>
  );
}

// ─── TestSettingsPage ─────────────────────────────────────────────────────

function TestSettingsPage() {
  const navigate = useNavigate();

  const navTo = (to: string) => {
    void navigate({ to: to as "/" });
  };

  return (
    <div className="flex flex-col min-h-full pb-20">
      <MiniHeader
        eyebrowNum="05"
        eyebrow="SYSTEM · КОНФИГУРАЦИЯ"
        title="Настройки"
      />
      <div className="flex flex-col gap-5 p-4">
        {/* РАЗДЕЛЫ */}
        <section>
          <Eyebrow num="05" className="mb-2.5 flex">
            РАЗДЕЛЫ
          </Eyebrow>
          <div className="border border-bg-5 bg-bg-1 px-4">
            <button
              type="button"
              onClick={() => navTo("/health")}
              className="w-full flex items-center gap-3 min-h-[44px] py-2.5 text-left border-b border-bg-5"
            >
              <Heart size={16} strokeWidth={1.5} className="text-bg-9 shrink-0" />
              <span className="flex-1 text-[14px] text-bg-11">Здоровье воркеров</span>
              <ChevronRight size={16} strokeWidth={1.5} className="text-bg-7 shrink-0" />
            </button>
            <button
              type="button"
              onClick={() => navTo("/scripts")}
              className="w-full flex items-center gap-3 min-h-[44px] py-2.5 text-left border-b border-bg-5"
            >
              <FileCode size={16} strokeWidth={1.5} className="text-bg-9 shrink-0" />
              <span className="flex-1 text-[14px] text-bg-11">Скрипты кампаний</span>
              <ChevronRight size={16} strokeWidth={1.5} className="text-bg-7 shrink-0" />
            </button>
            <button
              type="button"
              onClick={() => navTo("/offers")}
              className="w-full flex items-center gap-3 min-h-[44px] py-2.5 text-left"
            >
              <FileText size={16} strokeWidth={1.5} className="text-bg-9 shrink-0" />
              <span className="flex-1 text-[14px] text-bg-11">Офферы</span>
              <ChevronRight size={16} strokeWidth={1.5} className="text-bg-7 shrink-0" />
            </button>
          </div>
        </section>

        <ObserverSection />
        <TelegramSection />
        <VisionSection />
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
