/**
 * ScannerControls — карточка управления сканером на Панели.
 * Понятные пользователю тумблеры: Сканирование (вкл/выкл) и Авто-включение
 * (предлагать включить восстановившиеся). + индикатор: чьи кампании наблюдаем.
 */

import { useState } from "react";
import { Switch } from "@/components/ui/Switch";
import { Skeleton } from "@/components/ui/Skeleton";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import {
  useObserverSettings,
  useToggleScanning,
  useToggleAutoEnable,
  useToggleActViaApi,
} from "@/lib/api/settings";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface ControlRowProps {
  label: string;
  desc: string;
  checked: boolean;
  loading: boolean;
  pending: boolean;
  onChange: () => void;
}

function ControlRow({ label, desc, checked, loading, pending, onChange }: ControlRowProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="text-[13px] text-bg-11 font-medium">{label}</div>
        <div className="text-[11px] text-bg-9 mt-0.5">{desc}</div>
      </div>
      {loading ? (
        <Skeleton width={44} height={24} />
      ) : (
        <Switch checked={checked} onChange={onChange} disabled={pending} label={label} />
      )}
    </div>
  );
}

export function ScannerControls() {
  const query = useObserverSettings();
  const s = query.data;
  const loading = query.isLoading;

  const toggleScanning = useToggleScanning();
  const toggleAutoEnable = useToggleAutoEnable();
  const toggleActViaApi = useToggleActViaApi();

  // Канал авто-стопа — money-критичный, переключаем только через подтверждение.
  const actViaApi = s?.act_via_api ?? true;
  const [pendingActViaApi, setPendingActViaApi] = useState<boolean | null>(null);

  function confirmActViaApi() {
    if (pendingActViaApi === null) return;
    toggleActViaApi.mutate(pendingActViaApi, {
      onSuccess: () =>
        toast.success(
          pendingActViaApi ? "Канал: Marketing API" : "Канал: клик в браузере",
          "Канал авто-стопа изменён.",
        ),
      onError: (e) => toast.error("Ошибка", errMsg(e)),
    });
    setPendingActViaApi(null);
  }

  return (
    <section className="border border-bg-5 bg-bg-1 p-5 mb-10">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
          Управление сканером
        </h3>
        {!loading && (
          <span
            className="text-[11px] text-bg-9"
            title={
              s?.owner_campaign_tag
                ? `Бот следит только за кампаниями с тегом «${s.owner_campaign_tag}» в названии.`
                : "Тег владельца не задан — бот следит за всеми кампаниями кабинета (риск чужих кампаний)."
            }
          >
            Наблюдаем:{" "}
            <Badge variant={s?.owner_campaign_tag ? "neutral" : "warning"} size="sm">
              {s?.owner_campaign_tag ? `кампании ${s.owner_campaign_tag}` : "весь кабинет"}
            </Badge>
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-6">
        <ControlRow
          label="Сканирование"
          desc="Автоматически следить за объявлениями."
          checked={s?.is_scanning_enabled ?? false}
          loading={loading}
          pending={toggleScanning.isPending}
          onChange={() => {
            if (!s) return;
            toggleScanning.mutate(!s.is_scanning_enabled, {
              onSuccess: () =>
                toast.success(
                  s.is_scanning_enabled ? "Сканирование остановлено" : "Сканирование запущено",
                ),
              onError: (e) => toast.error("Ошибка", errMsg(e)),
            });
          }}
        />
        <ControlRow
          label="Авто-включение"
          desc="Предлагать включить восстановившиеся объявления."
          checked={s?.auto_enable_recommendations ?? false}
          loading={loading}
          pending={toggleAutoEnable.isPending}
          onChange={() => {
            if (!s) return;
            toggleAutoEnable.mutate(!s.auto_enable_recommendations, {
              onSuccess: () => toast.success("Настройка сохранена"),
              onError: (e) => toast.error("Ошибка", errMsg(e)),
            });
          }}
        />
      </div>

      {/* Канал авто-стопа — money-критичный тогл с подтверждением. */}
      <div className="mt-6 pt-5 border-t border-bg-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-bg-11 font-medium">Канал авто-стопа</span>
            {!loading && (
              <Badge variant={actViaApi ? "info" : "neutral"} size="sm" withDot={false}>
                {actViaApi ? "Marketing API" : "Клик в браузере"}
              </Badge>
            )}
          </div>
          <div className="text-[11px] text-bg-9 mt-0.5 max-w-md">
            Как отключать рекламу при стоп-правиле: через Marketing API (точно по ad_id, основной)
            или кликом в браузере (DOM, резерв).
          </div>
        </div>
        {loading ? (
          <Skeleton width={44} height={24} />
        ) : (
          <Switch
            checked={actViaApi}
            onChange={() => setPendingActViaApi(!actViaApi)}
            disabled={toggleActViaApi.isPending}
            label="Канал авто-стопа"
          />
        )}
      </div>

      <ConfirmDialog
        open={pendingActViaApi !== null}
        onOpenChange={(o) => {
          if (!o) setPendingActViaApi(null);
        }}
        title="Сменить канал авто-стопа?"
        description={
          pendingActViaApi
            ? "Отключение рекламы пойдёт через Marketing API — точно по ad_id, без промахов по кнопке."
            : "Отключение рекламы пойдёт через клик в браузере (DOM) — резервный канал."
        }
        confirmLabel="Сменить"
        confirmVariant="primary"
        onConfirm={confirmActViaApi}
      />
    </section>
  );
}
