/**
 * SettingsPage («Ещё») — конфигурация + навигация к вторичным экранам.
 * Канон: MiniHeader (eyebrowNum) → РАЗДЕЛЫ → OBSERVER → TELEGRAM → VISION.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { ChevronRight, Heart, FileCode, FileText, RefreshCw, Check } from "lucide-react";
import {
  useObserverSettings,
  useToggleScanning,
  useTriggerScan,
  useTelegramSettings,
  useVisionSettings,
  useCabinetAutostart,
  useObserverCampaigns,
  fetchJson,
  QK,
} from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { haptic } from "@/lib/tg";
import { MiniHeader } from "@/components/layout/MiniHeader";
import { Eyebrow } from "@/components/data";
import { Badge, Button, Skeleton, ErrorState, Switch, Input } from "@/components/ui";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/settings/")({
  component: SettingsPage,
});

// ─── Toast ────────────────────────────────────────────────────────────────

interface ToastState {
  text: string;
  ok: boolean;
}

function useToast() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const show = (text: string, ok = true) => {
    setToast({ text, ok });
    setTimeout(() => setToast(null), 3000);
  };
  return { toast, show };
}

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
        !noBorder && "border-b border-[var(--hairline)]",
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
      <div className="border border-[var(--hairline)] bg-bg-1 px-4 rounded-[var(--radius-3)]">{children}</div>
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
        !noBorder && "border-b border-[var(--hairline)]",
      )}
    >
      <span className="text-bg-9 shrink-0">{icon}</span>
      <span className="flex-1 text-[14px] text-bg-11">{label}</span>
      <ChevronRight size={16} strokeWidth={1.5} className="text-bg-7 shrink-0" />
    </button>
  );
}

// ─── Секция OBSERVER ──────────────────────────────────────────────────────

function ObserverSection({ showToast }: { showToast: (t: string, ok?: boolean) => void }) {
  const { data, isLoading, isError, refetch } = useObserverSettings();
  const toggleScanning = useToggleScanning();
  const triggerScan = useTriggerScan();
  const qc = useQueryClient();

  const [ownerTag, setOwnerTag] = useState<string>("");

  useEffect(() => {
    if (data) {
      setOwnerTag(data.owner_campaign_tag ?? "");
    }
  }, [data]);

  if (isLoading) {
    return (
      <Section eyebrow="OBSERVER" num="06">
        <div className="py-2 space-y-3">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      </Section>
    );
  }

  if (isError || !data) {
    return (
      <Section eyebrow="OBSERVER" num="06">
        <ErrorState message="Не удалось загрузить настройки" onRetry={() => void refetch()} />
      </Section>
    );
  }

  const cfg = data;

  const handleToggle = async () => {
    haptic.impact("medium");
    try {
      await toggleScanning.mutateAsync({ enabled: !cfg.is_scanning_enabled });
      haptic.notify("success");
      showToast(cfg.is_scanning_enabled ? "Сканирование отключено" : "Сканирование включено");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  const handleScanNow = async () => {
    haptic.impact("medium");
    try {
      await triggerScan.mutateAsync();
      haptic.notify("success");
      showToast("Сканирование запущено");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка", false);
    }
  };

  const handleSaveTag = async () => {
    haptic.impact("light");
    try {
      await fetchJson("/settings/observer", {
        method: "PUT",
        body: JSON.stringify({
          is_scanning_enabled: cfg.is_scanning_enabled,
          default_interval_seconds: cfg.default_interval_seconds,
          auto_enable_recommendations: cfg.auto_enable_recommendations,
          owner_campaign_tag: ownerTag.trim() || null,
        }),
      });
      void qc.invalidateQueries({ queryKey: QK.observerSettings });
      haptic.notify("success");
      showToast("Тег сохранён");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка сохранения", false);
    }
  };

  return (
    <Section eyebrow="OBSERVER" num="06">
      <FieldRow label="Сканирование" hint="Observer периодически сканирует объявления">
        <Switch
          checked={cfg.is_scanning_enabled}
          onChange={() => void handleToggle()}
          disabled={toggleScanning.isPending}
        />
      </FieldRow>

      <FieldRow label="Owner Campaign Tag" hint="Пусто — все кампании; несколько через запятую" noBorder>
        <div className="flex items-center gap-2">
          <Input
            aria-label="Owner Campaign Tag"
            placeholder="MV,ABC"
            value={ownerTag}
            onChange={(e) => setOwnerTag(e.target.value)}
            className="w-[120px] min-h-[36px] text-[13px]"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void handleSaveTag()}
            className="shrink-0"
          >
            Сохр.
          </Button>
        </div>
      </FieldRow>

      <div className="py-3">
        <Button
          variant="secondary"
          fullWidth
          onClick={() => void handleScanNow()}
          disabled={triggerScan.isPending}
          loading={triggerScan.isPending}
          aria-label="Сканировать сейчас"
        >
          <RefreshCw
            size={15}
            strokeWidth={1.6}
            className={cn("shrink-0", triggerScan.isPending && "animate-spin")}
          />
          Сканировать сейчас
        </Button>
      </div>
    </Section>
  );
}

// ─── Секция TELEGRAM ──────────────────────────────────────────────────────

function TelegramSection() {
  const { data, isLoading, isError, refetch } = useTelegramSettings();

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
        <ErrorState message="Не удалось загрузить" onRetry={() => void refetch()} />
      </Section>
    );
  }

  const authVariant = data?.is_authorized ? "done" : "neutral";
  const pollerVariant = data?.poller_status === "ONLINE" ? "running" : "neutral";

  return (
    <Section eyebrow="TELEGRAM" num="07">
      <FieldRow label="Авторизация">
        <Badge variant={authVariant}>{data?.is_authorized ? "Активен" : "Не настроен"}</Badge>
      </FieldRow>

      {data?.bot_username ? (
        <FieldRow label="Бот">
          <span className="font-mono text-[12px] text-bg-10">@{data.bot_username}</span>
        </FieldRow>
      ) : null}

      <FieldRow label="Poller" noBorder>
        <Badge variant={data?.poller_status ? pollerVariant : "neutral"}>
          {data?.poller_status ?? "—"}
        </Badge>
      </FieldRow>
    </Section>
  );
}

// ─── Секция VISION ────────────────────────────────────────────────────────

function VisionSection() {
  const { data, isLoading, isError, refetch } = useVisionSettings();

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
        <ErrorState message="Не удалось загрузить" onRetry={() => void refetch()} />
      </Section>
    );
  }

  const statusVariant = data?.cdp_ready ? "running" : data?.has_token ? "warning" : "neutral";
  const statusLabel = data?.cdp_ready
    ? "CDP готов"
    : data?.has_token
      ? "Токен есть"
      : "Не настроен";

  return (
    <Section eyebrow="VISION" num="08">
      <FieldRow label="Статус CDP">
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
          <span className="text-[12px] text-bg-7">—</span>
        </FieldRow>
      )}
    </Section>
  );
}

// ─── Секция АВТОСТАРТ КАБИНЕТА ─────────────────────────────────────────────

const pad2 = (n: number) => String(n).padStart(2, "0");

function CabinetAutostartSection({
  showToast,
}: {
  showToast: (t: string, ok?: boolean) => void;
}) {
  const { data, isLoading, isError, refetch } = useCabinetAutostart();
  const { data: campaigns, isLoading: campsLoading } = useObserverCampaigns();
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(false);
  const [time, setTime] = useState("06:00");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data) {
      setEnabled(data.enabled);
      setTime(`${pad2(data.hour_utc)}:${pad2(data.minute_utc)}`);
      setSelected(new Set(data.campaign_ids ?? []));
    }
  }, [data]);

  if (isLoading) {
    return (
      <Section eyebrow="АВТОСТАРТ КАБИНЕТА" num="09">
        <div className="py-2 space-y-3">
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-full" />
        </div>
      </Section>
    );
  }
  if (isError || !data) {
    return (
      <Section eyebrow="АВТОСТАРТ КАБИНЕТА" num="09">
        <ErrorState message="Не удалось загрузить" onRetry={() => void refetch()} />
      </Section>
    );
  }

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const handleSave = async () => {
    const [hh, mm] = time.split(":");
    const hour = Number(hh);
    const minute = Number(mm);
    if (
      !Number.isInteger(hour) ||
      hour < 0 ||
      hour > 23 ||
      !Number.isInteger(minute) ||
      minute < 0 ||
      minute > 59
    ) {
      showToast("Время: формат ЧЧ:ММ (UTC)", false);
      return;
    }
    haptic.impact("light");
    setSaving(true);
    try {
      await fetchJson("/tma/cabinet-autostart", {
        method: "PUT",
        body: JSON.stringify({
          enabled,
          hour_utc: hour,
          minute_utc: minute,
          campaign_ids: [...selected],
        }),
      });
      void qc.invalidateQueries({ queryKey: QK.cabinetAutostart });
      haptic.notify("success");
      showToast("Автостарт сохранён");
    } catch (e: unknown) {
      haptic.notify("error");
      showToast((e as Error).message ?? "Ошибка сохранения", false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section eyebrow="АВТОСТАРТ КАБИНЕТА" num="09">
      <FieldRow label="Включить" hint="В заданное время (UTC) включит отмеченные кампании">
        <Switch checked={enabled} onChange={() => setEnabled((v) => !v)} />
      </FieldRow>

      <FieldRow label="Время (UTC)" hint="Час запуска расписания" noBorder>
        <Input
          type="time"
          aria-label="Время автостарта (UTC)"
          value={time}
          onChange={(e) => setTime(e.target.value)}
          className="w-[110px] min-h-[36px] text-[13px]"
        />
      </FieldRow>

      <div className="pt-2">
        <p className="text-[11px] text-bg-8 mb-2">Кампании ({selected.size})</p>
        {campsLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : !campaigns || campaigns.length === 0 ? (
          <p className="text-[12px] text-bg-8 py-2">
            Список кампаний пуст — обнови его на десктопе (Кампании → Обновить список).
          </p>
        ) : (
          <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden max-h-[260px] overflow-y-auto">
            {campaigns.map((c) => {
              const isSel = selected.has(c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  role="checkbox"
                  aria-checked={isSel}
                  aria-label={c.name || c.id}
                  onClick={() => toggle(c.id)}
                  className={cn(
                    "w-full flex items-center gap-2.5 px-3 py-2.5 text-left text-[13px]",
                    "border-b border-[var(--hairline)] last:border-b-0",
                    isSel ? "bg-accent-bg text-bg-11" : "text-bg-10",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "inline-flex items-center justify-center shrink-0 size-4 rounded-[var(--radius-1)] border-[1.5px]",
                      isSel ? "bg-accent border-accent text-bg-0" : "bg-bg-2 border-bg-7",
                    )}
                  >
                    {isSel && <Check size={11} strokeWidth={3} />}
                  </span>
                  <span className="truncate">{c.name || c.id}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="py-3">
        <Button variant="primary" fullWidth onClick={() => void handleSave()} loading={saving}>
          Сохранить
        </Button>
      </div>
    </Section>
  );
}

// ─── SettingsPage ─────────────────────────────────────────────────────────

function SettingsPage() {
  const navigate = useNavigate();
  const { toast, show: showToast } = useToast();

  const navTo = (to: string) => {
    haptic.selection();
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
        {/* ── РАЗДЕЛЫ ── */}
        <section>
          <Eyebrow num="05" className="mb-2.5 flex">
            РАЗДЕЛЫ
          </Eyebrow>
          <div className="border border-[var(--hairline)] bg-bg-1 px-4 rounded-[var(--radius-3)]">
            <NavRow
              icon={<Heart size={16} strokeWidth={1.5} />}
              label="Здоровье воркеров"
              onClick={() => navTo("/health")}
            />
            <NavRow
              icon={<FileCode size={16} strokeWidth={1.5} />}
              label="Скрипты кампаний"
              onClick={() => navTo("/scripts")}
            />
            <NavRow
              icon={<FileText size={16} strokeWidth={1.5} />}
              label="Офферы"
              onClick={() => navTo("/offers")}
              noBorder
            />
          </div>
        </section>

        {/* ── Конфигурационные секции ── */}
        <ObserverSection showToast={showToast} />
        <TelegramSection />
        <VisionSection />
        <CabinetAutostartSection showToast={showToast} />
      </div>

      {/* Toast */}
      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            "fixed bottom-[80px] left-4 right-4 max-w-[440px] mx-auto z-50 px-4 py-3 text-[13px] border rounded-[var(--radius-2)]",
            toast.ok
              ? "bg-success-bg text-success border-success"
              : "bg-danger-bg text-danger border-danger",
          )}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}
