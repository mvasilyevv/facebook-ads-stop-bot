/**
 * ObserverTab — настройки наблюдателя.
 *
 * Тоглы (сканирование / auto-enable reco) — авто-сейв через точечный PATCH.
 * Справа — карточка ДЕЙСТВИЯ (перезапуск observer).
 *
 * Owner Campaign Tag и отслеживаемые кампании вынесены на страницу «Кампании»
 * (блок 01 OPERATE). Статус observer'а и список воркеров — в табе Health.
 * «Сканировать сейчас» — на главной Панели (тут дубль убран).
 */

import { useState, useEffect, type FC } from "react";
import { RefreshCw } from "lucide-react";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useObserverSettings,
  useUpdateObserverSettings,
  useToggleScanning,
  useToggleAutoEnable,
  useRestartObserver,
} from "@/lib/api/settings";
import type { ObserverConfig } from "@fb/shared";

// ─── Field — строка формы (label 180px + control) ────────────────────────────

interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

function Field({ label, hint, children }: FieldProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "180px 1fr",
        gap: 16,
        alignItems: "center",
        padding: "12px 0",
        borderBottom: "1px solid var(--hairline)",
      }}
    >
      <div>
        <div className="text-[13px]" style={{ color: "var(--bg-10)" }}>{label}</div>
        {hint && (
          <div className="text-[11px] mt-0.5" style={{ color: "var(--bg-8)" }}>{hint}</div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

// ─── Основной компонент ───────────────────────────────────────────────────────

export const ObserverTab: FC = () => {
  const { data, isLoading, error, refetch } = useObserverSettings();
  const updateMut = useUpdateObserverSettings();
  const toggleScanningMut = useToggleScanning();
  const toggleAutoEnableMut = useToggleAutoEnable();
  const restartMut = useRestartObserver();

  const [form, setForm] = useState<Partial<ObserverConfig>>({});

  useEffect(() => {
    if (data) {
      setForm({
        is_scanning_enabled: data.is_scanning_enabled,
        auto_enable_recommendations: data.auto_enable_recommendations,
      });
    }
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-3" style={{ maxWidth: 800 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  // Тоглы — точечный PATCH (scanning/auto-enable), не partial PUT (иначе 422).
  const handleToggle = async (
    field: "is_scanning_enabled" | "auto_enable_recommendations",
    value: boolean,
  ) => {
    setForm((f) => ({ ...f, [field]: value }));
    try {
      if (field === "is_scanning_enabled") {
        await toggleScanningMut.mutateAsync(value);
      } else {
        await toggleAutoEnableMut.mutateAsync(value);
      }
      toast.success("Настройки сохранены");
    } catch (e) {
      setForm((f) => ({ ...f, [field]: !value }));
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  const handleRestart = async () => {
    try {
      await restartMut.mutateAsync();
      toast.success("Сигнал перезапуска observer отправлен");
    } catch (e) {
      toast.error("Ошибка перезапуска", e instanceof Error ? e.message : String(e));
    }
  };

  // updateMut оставлен на будущее (PUT с полным body); сейчас тоглы идут через PATCH.
  void updateMut;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(260px,2fr)]">
      {/* ── Левая колонка: параметры ── */}
      <div>
        <div
          className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
          style={{ marginBottom: 8, display: "inline-block" }}
        >
          МОНИТОРИНГ · ПАРАМЕТРЫ
        </div>

        <Field label="Включить сканирование" hint="Наблюдатель периодически сканирует объявления">
          <Switch
            checked={form.is_scanning_enabled ?? false}
            onChange={() => handleToggle("is_scanning_enabled", !form.is_scanning_enabled)}
            label="Включить сканирование"
          />
        </Field>

        <Field
          label="Рекомендации на включение"
          hint="Только предлагает восстановление; запуск всегда подтверждает оператор"
        >
          <Switch
            checked={form.auto_enable_recommendations ?? false}
            onChange={() =>
              handleToggle("auto_enable_recommendations", !form.auto_enable_recommendations)
            }
            label="Формировать рекомендации на включение"
          />
        </Field>
      </div>

      {/* ── Правая колонка: действия ── */}
      <div>
        <div
          className="bg-bg-1 border border-[var(--hairline)] rounded-[var(--radius-3)]"
          style={{ padding: "var(--s-5)" }}
        >
          <div
            className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
            style={{ marginBottom: 14 }}
          >
            ДЕЙСТВИЯ
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--s-3)" }}>
            <Button
              variant="secondary"
              leftIcon={<RefreshCw size={14} />}
              onClick={() => void handleRestart()}
              loading={restartMut.isPending}
              style={{ justifyContent: "flex-start" }}
            >
              Перезапустить мониторинг
            </Button>

          </div>
        </div>
      </div>
    </div>
  );
};
