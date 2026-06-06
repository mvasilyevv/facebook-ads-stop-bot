/**
 * ObserverTab — настройки наблюдателя:
 * scanning toggle, auto-enable toggle, owner-tags, scan-now.
 * Форма с оптимистичным сохранением + Toast.
 */

import { useState, useEffect, type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import { useObserverSettings, useUpdateObserverSettings, useScanNow } from "@/lib/api/settings";
import type { ObserverConfig } from "@fb/shared";

export const ObserverTab: FC = () => {
  const { data, isLoading, error, refetch } = useObserverSettings();
  const updateMut = useUpdateObserverSettings();
  const scanMut = useScanNow();

  // Локальное состояние формы
  const [form, setForm] = useState<Partial<ObserverConfig>>({});

  // Синхронизация с данными с сервера
  useEffect(() => {
    if (data) {
      setForm({
        is_scanning_enabled: data.is_scanning_enabled,
        auto_enable_recommendations: data.auto_enable_recommendations,
        owner_campaign_tag: data.owner_campaign_tag ?? "",
        act_via_api: data.act_via_api,
      });
    }
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-3 max-w-xl">
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

  return (
    <div className="space-y-5 max-w-xl">
      {/* Сканирование */}
      <Card eyebrow="Наблюдатель" padded>
        <div className="space-y-4">
          <Switch
            checked={form.is_scanning_enabled ?? false}
            onChange={() =>
              handleToggle("is_scanning_enabled", !form.is_scanning_enabled)
            }
            label="Включить сканирование"
            visualLabel="Сканирование включено"
            description="Наблюдатель периодически сканирует объявления и оценивает правила."
          />

          <div className="border-t border-bg-4 pt-4">
            <Switch
              checked={form.auto_enable_recommendations ?? false}
              onChange={() =>
                handleToggle(
                  "auto_enable_recommendations",
                  !form.auto_enable_recommendations,
                )
              }
              label="Авто-включение рекомендаций"
              visualLabel="Авто-включение рекомендаций"
              description="Рекомендовать восстановление отключённых объявлений при улучшении метрик."
            />
          </div>

          <div className="border-t border-bg-4 pt-4">
            <Switch
              checked={form.act_via_api ?? false}
              onChange={() => handleToggle("act_via_api", !form.act_via_api)}
              label="Отключение через API"
              visualLabel="Отключение через Marketing API"
              description="Использовать Meta Marketing API для отключения (рекомендуется)."
            />
          </div>
        </div>
      </Card>

      {/* Owner tag */}
      <Card eyebrow="Фильтр кампаний" padded>
        <Input
          id="owner-tag"
          label="Owner Campaign Tag"
          placeholder="MV,ABC (несколько — через запятую)"
          value={form.owner_campaign_tag ?? ""}
          onChange={(e) =>
            setForm((f) => ({ ...f, owner_campaign_tag: e.target.value }))
          }
          helpText="Только кампании с этим тегом в названии будут обрабатываться. Пусто — все кампании."
        />
        <div className="mt-4 flex gap-3">
          <Button
            variant="primary"
            onClick={handleSave}
            loading={updateMut.isPending}
          >
            Сохранить тег
          </Button>
        </div>
      </Card>

      {/* Scan now */}
      <Card eyebrow="Действия" padded>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[13px] text-bg-11 font-medium">Запустить скан сейчас</div>
            <div className="text-[11px] text-bg-9 mt-0.5">
              Немедленно запустит цикл сканирования, не дожидаясь интервала.
            </div>
          </div>
          <Button
            variant="secondary"
            onClick={() => void handleScanNow()}
            loading={scanMut.isPending}
          >
            Scan Now
          </Button>
        </div>
      </Card>
    </div>
  );
};
