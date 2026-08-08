/**
 * ObserverTab — настройки наблюдателя.
 *
 * Тоглы (сканирование / auto-enable reco) — авто-сейв через точечный PATCH.
 * Owner Campaign Tag и отслеживаемые кампании находятся в разделе «Реклама».
 * Состояние источников и воркеров — в «Система → Источники и воркеры»,
 * а ручной запуск сканирования — на странице «Сейчас».
 */

import { useState, useEffect, type FC } from "react";
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
  useAutoEnableExclusions,
  useRemoveAutoEnableExclusion,
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
        borderBottom: "1px solid var(--color-hairline)",
      }}
    >
      <div>
        <div className="text-[13px]" style={{ color: "var(--color-bg-10)" }}>
          {label}
        </div>
        {hint && (
          <div className="text-[12px] mt-0.5" style={{ color: "var(--color-bg-8)" }}>
            {hint}
          </div>
        )}
      </div>
      <div>{children}</div>
    </div>
  );
}

// ─── Основной компонент ───────────────────────────────────────────────────────

export const ObserverTab: FC = () => {
  const { data, isLoading, error } = useObserverSettings();
  const updateMut = useUpdateObserverSettings();
  const toggleScanningMut = useToggleScanning();
  const toggleAutoEnableMut = useToggleAutoEnable();
  const exclusionsQ = useAutoEnableExclusions();
  const removeExclusion = useRemoveAutoEnableExclusion();

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
    return <ErrorState error={error} onRetry={() => void 0} />;
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

  // updateMut оставлен на будущее (PUT с полным body); сейчас тоглы идут через PATCH.
  void updateMut;

  return (
    <div className="grid gap-8">
      {/* ── Левая колонка: параметры ── */}
      <div>
        <div
          className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8"
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
          label="Auto-enable рекомендаций OK"
          hint="OK включаются автоматически; WARNING и curator всегда требуют ручного решения"
        >
          <Switch
            checked={form.auto_enable_recommendations ?? false}
            onChange={() =>
              handleToggle("auto_enable_recommendations", !form.auto_enable_recommendations)
            }
            label="Автоматически исполнять безопасные рекомендации OK"
          />
        </Field>
      </div>

      <section className="border-t border-[var(--color-hairline)] pt-6">
        <div className="font-display text-[12px] uppercase tracking-[0.1em] text-bg-8">
          Исключения auto-enable
        </div>
        <p className="mt-1 text-[12px] text-bg-8">
          Рекомендации для этих объявлений остаются видимыми, но автоматически не исполняются.
        </p>
        <div className="mt-4 overflow-hidden rounded-[var(--radius-2)] border border-[var(--color-hairline)]">
          {exclusionsQ.isLoading ? (
            <div className="p-3">
              <Skeleton height={34} className="w-full" />
            </div>
          ) : exclusionsQ.data?.length ? (
            exclusionsQ.data.map((item) => (
              <div
                key={item.fb_ad_id}
                className="flex items-center justify-between gap-4 border-b border-[var(--color-hairline)] px-4 py-3 last:border-0"
              >
                <div className="min-w-0">
                  <div className="truncate text-[12px] text-bg-11">
                    {item.ad_name || item.fb_ad_id}
                  </div>
                  <div className="mt-0.5 font-display text-[12px] text-bg-8">
                    {item.fb_ad_id}
                    {item.reason ? ` · ${item.reason}` : ""}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  loading={removeExclusion.isPending && removeExclusion.variables === item.fb_ad_id}
                  onClick={() => removeExclusion.mutate(item.fb_ad_id)}
                >
                  Разрешить auto-enable
                </Button>
              </div>
            ))
          ) : (
            <div className="px-4 py-6 text-center text-[12px] text-bg-8">Исключений нет</div>
          )}
        </div>
      </section>
    </div>
  );
};
