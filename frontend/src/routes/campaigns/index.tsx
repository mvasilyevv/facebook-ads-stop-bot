/**
 * Страница «Кампании» (блок 01 OPERATE) — скоуп наблюдения.
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
import { toast } from "@/components/ui/Toast";
import {
  useObserverSettings,
  useUpdateObserverSettings,
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
      <PageHeader eyebrowNum="01" eyebrow="OPERATE · СКОУП" title="Кампании" />
      <div className="space-y-6" style={{ maxWidth: 720 }}>
        <OwnerTagCard />
        <CabinetAutostartCard />
        <CampaignAllowlist />
      </div>
    </>
  );
}

// ─── Owner Campaign Tag ───────────────────────────────────────────────────────

const OwnerTagCard: FC = () => {
  const { data, isLoading, error, refetch } = useObserverSettings();
  const updateMut = useUpdateObserverSettings();
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
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;

  // PUT требует ПОЛНЫЙ body (все обязательные поля), иначе 422.
  const handleSave = async () => {
    try {
      await updateMut.mutateAsync({
        is_scanning_enabled: data?.is_scanning_enabled ?? false,
        auto_enable_recommendations: data?.auto_enable_recommendations ?? false,
        default_interval_seconds: data?.default_interval_seconds ?? 30,
        owner_campaign_tag: tags.length ? tags.join(",") : null,
      });
      toast.success("Owner Tag сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card eyebrow="OWNER CAMPAIGN TAG" padded>
      <div className="text-[12px] text-bg-9 mb-3">
        Тег(и) в названии кампании, помечающие «мои» кампании в общем кабинете. Добавляй по одному
        (Enter), × — удалить. Пусто — сканируются все кампании.
      </div>
      <TagListInput
        id="owner-tag"
        aria-label="Owner Campaign Tag"
        placeholder="MV + Enter"
        values={tags}
        onChange={setTags}
      />
      <div className="flex justify-end mt-3">
        <Button variant="primary" onClick={() => void handleSave()} loading={updateMut.isPending}>
          Сохранить
        </Button>
      </div>
    </Card>
  );
};

// ─── Автостарт кабинета по расписанию ─────────────────────────────────────────

const pad2 = (n: number) => String(n).padStart(2, "0");

const CabinetAutostartCard: FC = () => {
  const { data, isLoading, error, refetch } = useCabinetAutostart();
  const updateMut = useUpdateCabinetAutostart();
  const [enabled, setEnabled] = useState(false);
  const [time, setTime] = useState("06:00");

  useEffect(() => {
    if (data) {
      setEnabled(data.enabled);
      setTime(`${pad2(data.hour_utc)}:${pad2(data.minute_utc)}`);
    }
  }, [data]);

  if (isLoading) return <Skeleton className="h-32 w-full" />;
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;

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
      toast.success("Автостарт сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card eyebrow="АВТОСТАРТ КАБИНЕТА" padded>
      <div className="text-[12px] text-bg-9 mb-3">
        В заданное время (UTC) бот включит объявления <b className="text-bg-11">отслеживаемых</b>{" "}
        кампаний (список ниже) и запустит скан — без подтверждения. Пустой список отслеживаемых —
        ничего не включается.
      </div>

      <Switch
        checked={enabled}
        onChange={() => setEnabled((v) => !v)}
        label="Включить автостарт"
        visualLabel="Статус"
      />

      <div className="mt-4 flex items-end justify-between gap-3">
        <div style={{ maxWidth: 160 }} className="flex-1">
          <Input
            id="autostart-time"
            type="time"
            label="Время (UTC)"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
        </div>
        <Button variant="primary" onClick={() => void handleSave()} loading={updateMut.isPending}>
          Сохранить
        </Button>
      </div>
    </Card>
  );
};

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
            <span aria-hidden="true" className="text-bg-7 mr-1">
              /
            </span>
          )}
          <span className={i === 0 ? "text-bg-9" : "text-bg-11"}>{p}</span>
        </span>
      ))}
    </span>
  );
};

const CampaignAllowlist: FC = () => {
  const { data: campaigns, isLoading } = useObserverCampaigns();
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
      await refreshMut.mutateAsync();
      toast.success("Список кампаний обновлён");
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

  return (
    <Card padded>
      <div className="flex items-center justify-between mb-2">
        <div className="font-display text-[10px] tracking-[0.12em] uppercase text-bg-8">
          ОТСЛЕЖИВАЕМЫЕ КАМПАНИИ
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<RefreshCw size={13} />}
          onClick={() => void handleRefresh()}
          loading={refreshMut.isPending}
        >
          Обновить список
        </Button>
      </div>
      <div className="text-[11px] text-bg-8 mb-3">
        Пусто (ничего не выбрано) — сканируются все кампании по Owner Tag. Выбор сужает скан до
        отмеченных. «Обновить список» тянет кампании из кабинета живьём через browser-agent.
      </div>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !hasCampaigns ? (
        <div
          className="text-[12px] text-bg-8 border border-[var(--hairline)] rounded-[var(--radius-2)]"
          style={{ padding: "var(--s-4)" }}
        >
          Кампаний нет. Нажми «Обновить список» — резолвим из кабинета по Owner Tag.
        </div>
      ) : (
        <div className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden">
          {/* Шапка: выбрать/снять все (tri-state) */}
          <button
            type="button"
            role="checkbox"
            aria-checked={headerState === "mixed" ? "mixed" : headerState}
            onClick={toggleAll}
            className={cn(
              "w-full flex items-center gap-2.5 px-3 py-2 text-left",
              "bg-bg-1 border-b border-[var(--hairline)] cursor-pointer",
              "hover:bg-bg-2 transition-colors",
            )}
          >
            <CheckBox state={headerState} />
            <span className="font-display text-[10.5px] tracking-wider uppercase text-bg-9">
              {allSelected ? "Снять все" : "Выбрать все"}
            </span>
            <span className="ml-auto font-display tabular-nums text-[11px] text-bg-7">
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
                    "w-full flex items-center gap-2.5 px-3 py-2.5 text-left text-[13px]",
                    "border-b border-l-2 border-b-[var(--hairline)] last:border-b-0 cursor-pointer",
                    "transition-colors",
                    isSel
                      ? "bg-accent-bg border-l-accent"
                      : "border-l-transparent hover:bg-bg-2",
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

      <div style={{ marginTop: "var(--s-4)" }}>
        <Button
          variant="primary"
          onClick={() => void handleSaveAllowlist()}
          loading={saveMut.isPending}
          disabled={!hasCampaigns}
        >
          Сохранить выбор ({selected.size})
        </Button>
      </div>
    </Card>
  );
};
