/**
 * ObserverTab — настройки наблюдателя.
 *
 * Эталон templates.jsx SettingsTemplate: grid 60% / 40%.
 *   Слева: Field-паттерн (label 180px + control), 1px border-bottom сепараторы.
 *   Справа: карточка СТАТУС (Observer ONLINE/OFFLINE + последний скан) +
 *           карточка ДЕЙСТВИЯ (перезапуск, scan now, новый день кабинета).
 *
 * Тест SettingsPage.test.tsx ожидает:
 *   - switch aria-label "Включить сканирование"
 *   - input aria-label "Owner Campaign Tag" с value из данных
 *   - кнопку "Scan Now"
 *   - updateObserver({ is_scanning_enabled: false }) при клике toggle
 */

import { useState, useEffect, type FC } from "react";
import { RefreshCw, ScanLine, Clock } from "lucide-react";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useObserverSettings,
  useUpdateObserverSettings,
  useScanNow,
  useObserverStatus,
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
        borderBottom: "1px solid var(--bg-5)",
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
  const scanMut = useScanNow();
  const statusQ = useObserverStatus();

  // Локальное состояние формы
  const [form, setForm] = useState<Partial<ObserverConfig>>({});

  useEffect(() => {
    if (data) {
      setForm({
        is_scanning_enabled: data.is_scanning_enabled,
        auto_enable_recommendations: data.auto_enable_recommendations,
        owner_campaign_tag: data.owner_campaign_tag ?? "",
      });
    }
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-3" style={{ maxWidth: 800 }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  const save = async (patch: Partial<ObserverConfig>) => {
    try {
      await updateMut.mutateAsync(patch);
      toast.success("Настройки сохранены");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  const handleToggle = (field: keyof ObserverConfig, value: boolean) => {
    setForm((f) => ({ ...f, [field]: value }));
    void save({ [field]: value });
  };

  const handleSave = () => {
    void save({ owner_campaign_tag: form.owner_campaign_tag || null });
  };

  const handleScanNow = async () => {
    try {
      await scanMut.mutateAsync();
      toast.success("Сканирование запущено");
    } catch (e) {
      toast.error("Ошибка запуска скана", e instanceof Error ? e.message : String(e));
    }
  };

  // Observer status
  const observerStatus = statusQ.data;
  const isOnline = observerStatus?.status === "running";

  // Последний скан — форматируем relative time
  const lastScan = observerStatus?.last_scan_at
    ? (() => {
        const diff = Date.now() - new Date(observerStatus.last_scan_at).getTime();
        const sec = Math.floor(diff / 1000);
        if (sec < 60) return `${sec}с назад`;
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min}м назад`;
        return `${Math.floor(min / 60)}ч назад`;
      })()
    : "—";

  return (
    <div style={{ display: "grid", gridTemplateColumns: "60% 40%", gap: "var(--s-8)" }}>
      {/* ── Левая колонка: форма Field-паттерн ── */}
      <div>
        <div
          className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
          style={{ marginBottom: 8, display: "inline-block" }}
        >
          OBSERVER · ПАРАМЕТРЫ
        </div>

        {/* Сканирование toggle */}
        <Field label="Включить сканирование" hint="Наблюдатель периодически сканирует объявления">
          <Switch
            checked={form.is_scanning_enabled ?? false}
            onChange={() =>
              handleToggle("is_scanning_enabled", !form.is_scanning_enabled)
            }
            label="Включить сканирование"
          />
        </Field>

        {/* Auto-enable reco toggle */}
        <Field
          label="Auto-enable reco"
          hint="рекомендовать восстановление по метрикам"
        >
          <Switch
            checked={form.auto_enable_recommendations ?? false}
            onChange={() =>
              handleToggle(
                "auto_enable_recommendations",
                !form.auto_enable_recommendations,
              )
            }
            label="Авто-включение рекомендаций"
          />
        </Field>

        {/* Owner tag */}
        <Field
          label="Owner Campaign Tag"
          hint="Пусто — все кампании"
        >
          <Input
            id="owner-tag"
            aria-label="Owner Campaign Tag"
            placeholder="MV,ABC (несколько через запятую)"
            value={form.owner_campaign_tag ?? ""}
            onChange={(e) =>
              setForm((f) => ({ ...f, owner_campaign_tag: e.target.value }))
            }
          />
        </Field>

        {/* Кнопка сохранить */}
        <div style={{ marginTop: "var(--s-5)" }}>
          <Button
            variant="primary"
            onClick={handleSave}
            loading={updateMut.isPending}
          >
            Сохранить изменения
          </Button>
        </div>
      </div>

      {/* ── Правая колонка: статус + действия ── */}
      <div>
        {/* Карточка: Статус */}
        <div
          className="bg-bg-1 border border-bg-5"
          style={{ padding: "var(--s-5)", marginBottom: "var(--s-4)" }}
        >
          <div
            className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8"
            style={{ marginBottom: 14 }}
          >
            СТАТУС
          </div>

          {/* Observer ONLINE/OFFLINE */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 10,
            }}
          >
            <span className="text-[13px]" style={{ color: "var(--bg-10)" }}>Observer</span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                fontSize: 13,
                color: isOnline ? "var(--success)" : "var(--danger)",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 999,
                  background: isOnline ? "var(--success)" : "var(--danger)",
                }}
              />
              {isOnline ? "ONLINE" : "OFFLINE"}
            </span>
          </div>

          {/* Последний скан */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span className="text-[13px]" style={{ color: "var(--bg-10)" }}>Последний скан</span>
            <span
              className="font-display tabular-nums text-[13px]"
              style={{ color: "var(--bg-11)" }}
            >
              {lastScan}
            </span>
          </div>
        </div>

        {/* Карточка: Действия */}
        <div
          className="bg-bg-1 border border-bg-5"
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
              style={{ justifyContent: "flex-start" }}
            >
              Перезапустить observer
            </Button>

            <Button
              variant="secondary"
              leftIcon={<ScanLine size={14} />}
              onClick={() => void handleScanNow()}
              loading={scanMut.isPending}
              style={{ justifyContent: "flex-start" }}
            >
              Scan Now
            </Button>

            <Button
              variant="secondary"
              leftIcon={<Clock size={14} />}
              style={{ justifyContent: "flex-start" }}
            >
              Начать новый день кабинета
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
