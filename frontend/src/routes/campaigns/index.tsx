/**
 * Страница «Кампании» — скоуп наблюдения.
 *
 * Вынесено из Settings → Observer: Owner Campaign Tag (какие кампании «мои»)
 * + отслеживаемые кампании (allowlist). Определяет, что именно сканирует бот.
 */

import { useState, useEffect, type FC } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { RefreshCw, Check } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card } from "@/components/ui/Card";
import { TagListInput } from "@/components/ui/TagListInput";
import { Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { toast } from "@/components/ui/Toast";
import { useOperatorRealtimeStatus } from "@fb/operator-api";
import { snapshotForRealtimeState } from "@fb/shared/operator/viewModel";
import { useOperatorSnapshot } from "@/lib/api/operator";
import {
  useObserverSettings,
  useUpdateOwnerTag,
  useObserverCampaigns,
  useRefreshObserverCampaigns,
  useSetCampaignAllowlist,
  useCabinetAutostart,
  useUpdateCabinetAutostart,
} from "@/lib/api/settings";

export const Route = createFileRoute("/campaigns/")({
  component: CampaignsPage,
});

function CampaignsPage() {
  return (
    <>
      <PageHeader
        eyebrowNum="01"
        eyebrow="OPERATE · КОНТУР МОНИТОРИНГА"
        title="Контур кампаний"
        subtitle="Какие кампании бот отслеживает и когда запускает кабинет"
      />
      <div className="space-y-6" style={{ maxWidth: 820 }}>
        <ScopeCard />
        <CabinetAutostartCard />
      </div>
    </>
  );
}

// ─── Скоуп наблюдения: Owner Tag + Отслеживаемые кампании в одном блоке ────────

const ScopeCard: FC = () => {
  return (
    <Card padded>
      <OwnerTagSection />
      <div className="my-5 border-t border-[var(--color-hairline)]" />
      <CampaignAllowlistSection />
    </Card>
  );
};

// ─── Owner Campaign Tag ───────────────────────────────────────────────────────

const OwnerTagSection: FC = () => {
  const { data, isLoading, error } = useObserverSettings();
  const updateMut = useUpdateOwnerTag();
  // Тэги как список (на бэке хранятся одной строкой через запятую).
  const [tags, setTags] = useState<string[]>([]);

  useEffect(() => {
    if (data) {
      const raw = data.owner_campaign_tag ?? "";
      setTags(
        raw
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
      );
    }
  }, [data]);

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (error) return <ErrorState error={error} onRetry={() => void 0} />;

  // Точечный PATCH: full-PUT из кэша молча откатывал is_scanning_enabled (аудит C-1).
  const handleSave = async () => {
    try {
      await updateMut.mutateAsync(tags.length ? tags.join(",") : null);
      toast.success("Теги владельца сохранены");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <div className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8 mb-2">
        ТЕГИ ВЛАДЕЛЬЦА
      </div>
      <div className="text-[12px] text-bg-9 mb-3">
        Метки в названии кампании, по которым бот отделяет ваши кампании от остальных. Добавляйте по
        одной через Enter. Если оставить поле пустым, фильтрации по владельцу не будет.
      </div>
      <TagListInput
        id="owner-tag"
        aria-label="Теги владельца кампаний"
        placeholder="MV + Enter"
        values={tags}
        onChange={setTags}
      />
      <div className="flex justify-end mt-3">
        <Button
          variant="primary"
          onClick={() => void handleSave()}
          loading={updateMut.isPending}
          disabled={tags.join(",") === (data?.owner_campaign_tag ?? "")}
        >
          Сохранить теги
        </Button>
      </div>
    </div>
  );
};

// ─── Автостарт кабинета по расписанию ─────────────────────────────────────────

const pad2 = (n: number) => String(n).padStart(2, "0");

const CabinetAutostartCard: FC = () => {
  const { data, isLoading, error } = useCabinetAutostart();
  const updateMut = useUpdateCabinetAutostart();
  const [enabled, setEnabled] = useState(false);
  const [time, setTime] = useState("06:00");
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (data) {
      setEnabled(data.enabled);
      setTime(`${pad2(data.hour_utc)}:${pad2(data.minute_utc)}`);
    }
  }, [data]);

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (error) return <ErrorState error={error} onRetry={() => void 0} />;

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
      toast.error("Время некорректно", "Формат ЧЧ:ММ (UTC)");
      return;
    }
    try {
      await updateMut.mutateAsync({ enabled, hour_utc: hour, minute_utc: minute });
      toast.success(enabled ? "Автостарт включён" : "Автостарт выключен");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  const isDirty =
    enabled !== (data?.enabled ?? false) ||
    time !== `${pad2(data?.hour_utc ?? 0)}:${pad2(data?.minute_utc ?? 0)}`;
  const requestSave = () => {
    if (enabled && !data?.enabled) setConfirmOpen(true);
    else void handleSave();
  };

  return (
    <>
      <Card eyebrow="АВТОСТАРТ КАБИНЕТА" padded>
        <div className="text-[12px] text-bg-9 mb-3">
          В заданное время бот автоматически включит объявления выбранных кампаний и запустит
          мониторинг. Перед первым включением потребуется подтверждение.
        </div>

        <Switch
          checked={enabled}
          onChange={() => setEnabled((v) => !v)}
          label="Включить автостарт"
          visualLabel="Статус"
        />

        <div className="mt-4 flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div style={{ maxWidth: 160 }} className="flex-1">
            <Input
              id="autostart-time"
              type="time"
              label="Время (UTC)"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
            <p className="mt-1.5 text-[12px] text-bg-9">
              Локальное время: {localTimeForUtc(time)} (
              {Intl.DateTimeFormat().resolvedOptions().timeZone})
            </p>
          </div>
          <Button
            variant="primary"
            onClick={requestSave}
            loading={updateMut.isPending}
            disabled={!isDirty}
          >
            Сохранить расписание
          </Button>
        </div>
      </Card>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Включить автоматический запуск?"
        description={`Каждый день в ${time} UTC (${localTimeForUtc(time)} по локальному времени) бот включит объявления выбранных кампаний без дополнительного подтверждения.`}
        confirmLabel="Включить автостарт"
        onConfirm={handleSave}
      />
    </>
  );
};

function localTimeForUtc(value: string): string {
  const parts = value.split(":");
  const hours = Number(parts[0]);
  const minutes = Number(parts[1]);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return "—";
  const date = new Date();
  date.setUTCHours(hours, minutes, 0, 0);
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

// ─── Отслеживаемые кампании (allowlist) ───────────────────────────────────────

/** Визуальный чекбокс-индикатор (кликабелен сам ряд, своей кнопки нет). */
const CheckBox: FC<{ state: boolean | "mixed" }> = ({ state }) => (
  <span
    aria-hidden="true"
    className={cn(
      "inline-flex items-center justify-center shrink-0 size-4 rounded-[var(--radius-1)]",
      "border-[1.5px] transition-colors duration-[120ms]",
      state === false ? "bg-bg-2 border-bg-7" : "bg-accent border-accent text-bg-0",
    )}
  >
    {state === true && <Check size={11} strokeWidth={3} />}
    {state === "mixed" && <span className="block w-2 h-[2px] rounded-full bg-bg-0" />}
  </span>
);

/** Имя кампании сегментами по «|»: owner-тег приглушён, разделители тонкие. */
const CampaignName: FC<{ name: string }> = ({ name }) => {
  const parts = name.split(/\s*\|\s*/).filter(Boolean);
  if (parts.length <= 1) return <span className="text-bg-11">{name || "—"}</span>;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1 gap-y-0.5 leading-tight">
      {parts.map((p, i) => (
        <span key={`${i}-${p}`} className="inline-flex items-baseline">
          {i > 0 && (
            <span aria-hidden="true" className="text-bg-8 mr-1">
              /
            </span>
          )}
          <span className={i === 0 ? "text-bg-9" : "text-bg-11"}>{p}</span>
        </span>
      ))}
    </span>
  );
};

const CampaignAllowlistSection: FC = () => {
  const realtimeStatus = useOperatorRealtimeStatus();
  const operatorSnapshotQuery = useOperatorSnapshot({ window: "today" });
  const operatorSnapshot = operatorSnapshotQuery.data
    ? snapshotForRealtimeState(operatorSnapshotQuery.data, realtimeStatus === "connected")
    : null;
  // Старые кампании (дата в имени старше 14 дней, не выбранные) по умолчанию скрыты —
  // бэк фильтрует по дате из названия; тумблер ниже списка показывает всё.
  const [showStale, setShowStale] = useState(false);
  const {
    data: campaigns,
    isLoading,
    isError: campaignsUnavailable,
    error: campaignsError,
    refetch: refetchCampaigns,
  } = useObserverCampaigns(showStale);
  const refreshMut = useRefreshObserverCampaigns();
  const saveMut = useSetCampaignAllowlist();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (campaigns) {
      setSelected(new Set(campaigns.filter((c) => c.selected).map((c) => c.id)));
    }
  }, [campaigns]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const allIds = campaigns?.map((c) => c.id) ?? [];
  const allSelected = allIds.length > 0 && allIds.every((id) => selected.has(id));
  const someSelected = allIds.some((id) => selected.has(id));
  const headerState: boolean | "mixed" = allSelected ? true : someSelected ? "mixed" : false;

  const toggleAll = () => setSelected(() => (allSelected ? new Set() : new Set(allIds)));

  const handleRefresh = async () => {
    try {
      const data = await refreshMut.mutateAsync(showStale);
      // Честное сообщение: показываем число. 0 — не «успех», а подсказка почему пусто
      // (нет кампаний с тегом в кабинете ИЛИ Vision-канал недоступен).
      if (data.length === 0) {
        toast.warning(
          "Обновлено: 0 кампаний",
          "В кабинетах офферов нет кампаний с этим Owner Tag, либо Vision-канал недоступен.",
        );
      } else {
        toast.success(`Обновлено: ${data.length} кампаний`);
      }
    } catch (e) {
      toast.error("Не удалось обновить список", e instanceof Error ? e.message : String(e));
    }
  };

  const handleSaveAllowlist = async () => {
    try {
      await saveMut.mutateAsync([...selected]);
      toast.success("Выбор кампаний сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения выбора", e instanceof Error ? e.message : String(e));
    }
  };

  const hasCampaigns = !!campaigns && campaigns.length > 0;
  const initialSelected = new Set(
    campaigns?.filter((campaign) => campaign.selected).map((campaign) => campaign.id) ?? [],
  );
  const hasSelectionChanges =
    selected.size !== initialSelected.size || [...selected].some((id) => !initialSelected.has(id));
  const refreshUnavailable =
    operatorSnapshotQuery.isLoading ||
    operatorSnapshotQuery.isError ||
    operatorSnapshot === null ||
    operatorSnapshot.system.state === "stale" ||
    operatorSnapshot.system.state === "unavailable" ||
    operatorSnapshot.system.data?.severity === "critical";

  if (campaignsUnavailable) {
    return (
      <ErrorState
        title="Список кампаний недоступен"
        error={campaignsError}
        onRetry={() => void refetchCampaigns()}
      />
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div className="font-display text-[12px] tracking-[0.12em] uppercase text-bg-8">
          КАМПАНИИ ПОД КОНТРОЛЕМ
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw size={13} />}
          onClick={() => void handleRefresh()}
          loading={refreshMut.isPending}
          disabled={refreshUnavailable}
          title={refreshUnavailable ? "Сначала восстановите контур мониторинга" : undefined}
        >
          Обновить из кабинета
        </Button>
      </div>
      <div className="text-[12px] text-bg-8 mb-3">
        Выбранные кампании участвуют в мониторинге, авто-стопе и расписании запуска. Если ничего не
        выбрано, бот не будет отслеживать и включать объявления.
      </div>
      {refreshUnavailable ? (
        <p className="mb-3 text-[12px] text-warning" role="status">
          Обновление из кабинета недоступно, пока контур мониторинга не восстановлен.
        </p>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !hasCampaigns ? (
        <div
          className="text-[12px] text-bg-8 border border-[var(--color-hairline)] rounded-[var(--radius-2)]"
          style={{ padding: "var(--space-4)" }}
        >
          Кампаний нет. Проверьте теги владельца и обновите список из кабинета.
        </div>
      ) : (
        <div className="border border-[var(--color-hairline)] rounded-[var(--radius-2)] overflow-hidden">
          {/* Шапка: выбрать/снять все (tri-state) */}
          <button
            type="button"
            role="checkbox"
            aria-checked={headerState === "mixed" ? "mixed" : headerState}
            onClick={toggleAll}
            className={cn(
              "min-h-11 w-full flex items-center gap-2.5 px-3 py-2 text-left",
              "bg-bg-1 border-b border-[var(--color-hairline)] cursor-pointer",
              "hover:bg-bg-2 transition-colors",
            )}
          >
            <CheckBox state={headerState} />
            <span className="font-display text-[12px] tracking-wider uppercase text-bg-9">
              {allSelected ? "Снять все" : "Выбрать все"}
            </span>
            <span className="ml-auto font-display tabular-nums text-[12px] text-bg-8">
              {selected.size} / {allIds.length}
            </span>
          </button>

          {/* Ряды кампаний */}
          <div style={{ maxHeight: 360, overflowY: "auto" }}>
            {campaigns!.map((c) => {
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
                    "min-h-11 w-full flex items-center gap-2.5 px-3 py-2.5 text-left text-[13px]",
                    "border-b border-l-2 border-b-[var(--color-hairline)] last:border-b-0 cursor-pointer",
                    "transition-colors",
                    isSel ? "bg-accent-bg border-l-accent" : "border-l-transparent hover:bg-bg-2",
                  )}
                >
                  <CheckBox state={isSel} />
                  <CampaignName name={c.name || c.id} />
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div
        className="flex items-center justify-between gap-4"
        style={{ marginTop: "var(--space-4)" }}
      >
        <Button
          variant="primary"
          onClick={() => void handleSaveAllowlist()}
          loading={saveMut.isPending}
          disabled={!hasCampaigns || !hasSelectionChanges}
        >
          Сохранить выбор ({selected.size})
        </Button>
        <button
          type="button"
          onClick={() => setShowStale((v) => !v)}
          className="inline-flex min-h-11 items-center px-2 text-[12px] text-bg-9 hover:text-bg-11 underline underline-offset-4 transition-colors"
        >
          {showStale ? "Скрыть старые кампании" : "Показать старые (>14 дней)"}
        </button>
      </div>
    </div>
  );
};
