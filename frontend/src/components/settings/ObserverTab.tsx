/**
 * ObserverTab — вкладка настроек Observer:
 *   - Интервал сканирования.
 *   - Таблица последних scan-runs с filter-selector + статус observer.
 * Тумблеры (вкл/выкл, авто-стоп через API, auto-enable) вынесены на страницу «Панель»
 * (карточка «Управление сканером»), чтобы не было дублей.
 */

import { useState, type ChangeEvent } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import { RefreshCcw, Play } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { Select } from "@/components/ui/Select";
import { Table } from "@/components/data/Table";
import { toast } from "@/components/ui/Toast";
import { formatRelativeTime, formatDateTime, formatDuration } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import type { ScanRun } from "@/lib/types/api";

import {
  useObserverSettings,
  useObserverStatus,
  useScanRuns,
  useUpdateObserver,
  useTriggerScanNowSettings,
  useObserverCampaigns,
  useSetObserverCampaigns,
  useRefreshCampaigns,
  type CampaignOption,
} from "@/lib/api/settings";

// Локализация статуса observer (backend: running | paused | unknown).
const STATUS_LABEL: Record<string, string> = {
  running: "работает",
  paused: "на паузе",
  unknown: "неизвестно",
};

// Локализация и цвет outcome скана (backend: success | empty | error | paused).
const OUTCOME_LABEL: Record<string, string> = {
  success: "успех",
  empty: "пусто",
  error: "ошибка",
  paused: "пауза",
};
const OUTCOME_VARIANT: Record<string, "success" | "stop" | "warning" | "neutral"> = {
  success: "success",
  empty: "neutral",
  error: "stop",
  paused: "warning",
};

// Колонки таблицы scan-runs (поля backend ScanRunRow).
const SCAN_COLUMNS: ColumnDef<ScanRun, unknown>[] = [
  {
    id: "started_at",
    header: "Начало",
    accessorKey: "started_at",
    cell: ({ getValue }) => (
      <span className="font-numeric text-[12px]">{formatDateTime(getValue() as string)}</span>
    ),
  },
  {
    id: "outcome",
    header: "Результат",
    accessorKey: "outcome",
    cell: ({ row }) => {
      const v = (row.original.outcome ?? "") as string;
      const variant = OUTCOME_VARIANT[v] ?? "neutral";
      const label = OUTCOME_LABEL[v] ?? (v || "—");
      return (
        <Badge variant={variant} size="sm" title={row.original.error_message ?? undefined}>
          {label}
        </Badge>
      );
    },
  },
  {
    id: "alerts",
    header: "Алерты",
    cell: ({ row }) => {
      const w = row.original.alerts_warning ?? 0;
      const s = row.original.alerts_stop ?? 0;
      if (w + s === 0) return <span className="text-bg-8 text-[13px]">—</span>;
      return (
        <span className="font-numeric text-[13px] flex items-center gap-2">
          {w > 0 ? <span className="text-warning">{w} warn</span> : null}
          {s > 0 ? <span className="text-danger">{s} stop</span> : null}
        </span>
      );
    },
  },
  {
    id: "duration_ms",
    header: "Длительность",
    accessorKey: "duration_ms",
    cell: ({ getValue }) => {
      const ms = getValue() as number | null;
      return (
        <span className="font-numeric text-[12px] text-bg-10">
          {ms != null ? formatDuration(ms / 1000) : "—"}
        </span>
      );
    },
  },
];

type ScanFilter = "all" | "errors" | "slow" | "with_alerts";

const FILTER_OPTIONS: { value: ScanFilter; label: string }[] = [
  { value: "all", label: "Все" },
  { value: "errors", label: "Только ошибки" },
  { value: "slow", label: "Только медленные" },
  { value: "with_alerts", label: "С алертами" },
];

/** Стилизованный чекбокс в духе дизайн-системы (визуал как в таблице Ads). */
function Check({ checked }: { checked: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "size-4 border inline-flex items-center justify-center transition-colors shrink-0",
        checked
          ? "bg-accent border-accent text-bg-0"
          : "bg-bg-2 border-bg-7 group-hover:border-bg-9",
      )}
    >
      {checked ? (
        <svg
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          width={11}
          height={11}
        >
          <polyline points="2 6 5 9 10 3" />
        </svg>
      ) : null}
    </span>
  );
}

/** Выбор кампаний для allowlist. Lazy-init из selected; key пересоздаёт при смене данных. */
function CampaignSelector({
  campaigns,
  saving,
  onSave,
}: {
  campaigns: CampaignOption[];
  saving: boolean;
  onSave: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(campaigns.filter((c) => c.selected).map((c) => c.id)),
  );

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allSelected = campaigns.length > 0 && selected.size === campaigns.length;

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(campaigns.map((c) => c.id)));
  }

  return (
    <div className="border border-bg-5 bg-bg-1">
      {/* Шапка: выбрать все + счётчик. */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-bg-4">
        <button
          type="button"
          onClick={toggleAll}
          className="text-[11px] text-bg-9 hover:text-bg-11 transition-colors"
        >
          {allSelected ? "Снять все" : "Выбрать все"}
        </button>
        <span className="text-[11px] font-numeric text-bg-9">
          {selected.size > 0
            ? `${selected.size} из ${campaigns.length}`
            : `все по тегу · ${campaigns.length}`}
        </span>
      </div>

      {/* Список кампаний. */}
      <div className="flex flex-col max-h-72 overflow-y-auto divide-y divide-bg-3">
        {campaigns.map((c) => {
          const isOn = selected.has(c.id);
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => toggle(c.id)}
              aria-pressed={isOn}
              className={cn(
                "group flex items-center gap-3 px-3 py-2.5 text-left transition-colors",
                isOn ? "bg-bg-2" : "hover:bg-bg-2",
              )}
            >
              <Check checked={isOn} />
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] text-bg-11 truncate">{c.name}</div>
                <div className="text-[10.5px] font-numeric text-bg-7 truncate">{c.id}</div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Футер: сохранить выбор. */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-t border-bg-4">
        <Button size="sm" variant="primary" loading={saving} onClick={() => onSave([...selected])}>
          Сохранить выбор
        </Button>
        <span className="text-[11px] text-bg-9 ml-auto">
          {selected.size > 0 ? `сканируем ${selected.size}` : "сканируем все по тегу"}
        </span>
      </div>
    </div>
  );
}

export function ObserverTab() {
  const [scanFilter, setScanFilter] = useState<ScanFilter>("all");
  // Локальное состояние для редактирования интервала.
  const [intervalDraft, setIntervalDraft] = useState<string>("");
  const [editingInterval, setEditingInterval] = useState(false);
  // Локальное состояние для редактирования тега владельца.
  const [ownerDraft, setOwnerDraft] = useState<string>("");
  const [editingOwner, setEditingOwner] = useState(false);

  const settingsQuery = useObserverSettings();
  const statusQuery = useObserverStatus();
  const scanRunsQuery = useScanRuns(50, scanFilter);

  const updateObserver = useUpdateObserver();
  const scanNow = useTriggerScanNowSettings();
  const campaignsQuery = useObserverCampaigns();
  const setCampaigns = useSetObserverCampaigns();
  const refreshCampaigns = useRefreshCampaigns();

  /** Live-резолв всех кампаний по owner-тегу (подтягивает новые из кабинета). */
  function handleRefreshCampaigns() {
    refreshCampaigns.mutate(undefined, {
      onSuccess: (list) =>
        toast.success("Список обновлён", `Найдено кампаний: ${list?.length ?? 0}`),
      onError: (e) =>
        toast.error("Не удалось обновить", e instanceof Error ? e.message : String(e)),
    });
  }

  const settings = settingsQuery.data;
  const status = statusQuery.data;

  /**
   * Полный консистентный PUT. owner_campaign_tag отправляем ВСЕГДА текущий —
   * бэк присваивает это поле безусловно (default=None), поэтому неполный PUT
   * затирает owner-фильтр в NULL (money-баг: бот начнёт следить за чужими кампаниями).
   */
  function persistObserver(
    overrides: Partial<{
      is_scanning_enabled: boolean;
      default_interval_seconds: number;
      auto_enable_recommendations: boolean;
      owner_campaign_tag: string | null;
    }>,
    onSuccess: () => void,
  ) {
    updateObserver.mutate(
      {
        is_scanning_enabled: settings?.is_scanning_enabled ?? true,
        default_interval_seconds: settings?.default_interval_seconds ?? 60,
        auto_enable_recommendations: settings?.auto_enable_recommendations ?? false,
        owner_campaign_tag: settings?.owner_campaign_tag ?? null,
        ...overrides,
      },
      {
        onSuccess,
        onError: (err) =>
          toast.error("Ошибка", err instanceof Error ? err.message : String(err)),
      },
    );
  }

  /** Сохранить новый интервал скана (валидация совпадает с бэком: 30–600 сек). */
  function handleSaveInterval() {
    const n = parseInt(intervalDraft, 10);
    if (Number.isNaN(n) || n < 30 || n > 600) {
      toast.error("Некорректный интервал", "Допустимо 30–600 секунд.");
      return;
    }
    persistObserver({ default_interval_seconds: n }, () => {
      setEditingInterval(false);
      toast.success("Интервал сохранён");
    });
  }

  function handleStartEditInterval() {
    setIntervalDraft(String(settings?.default_interval_seconds ?? ""));
    setEditingInterval(true);
  }

  function handleStartEditOwner() {
    setOwnerDraft(settings?.owner_campaign_tag ?? "");
    setEditingOwner(true);
  }

  /** Сохранить тег владельца. Пусто → null (наблюдаем весь кабинет). */
  function handleSaveOwner() {
    const tag = ownerDraft.trim();
    persistObserver({ owner_campaign_tag: tag || null }, () => {
      setEditingOwner(false);
      toast.success(tag ? "Тег владельца сохранён" : "Фильтр снят — наблюдаем весь кабинет");
    });
  }

  /** Запустить скан немедленно. */
  function handleScanNow() {
    scanNow.mutate(undefined, {
      onSuccess: () => toast.success("Сканирование запущено", "Observer запустит цикл сканирования."),
      onError: (err) =>
        toast.error("Не удалось запустить скан", err instanceof Error ? err.message : String(err)),
    });
  }

  if (settingsQuery.isError) {
    return (
      <ErrorState
        title="Не удалось загрузить настройки observer."
        error={settingsQuery.error}
        onRetry={() => settingsQuery.refetch()}
      />
    );
  }

  return (
    <div className="grid grid-cols-[1fr_320px] gap-8">
      {/* Левая колонка: интервал + таблица сканов. */}
      <div className="space-y-6">
        <section>
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Настройки сканирования
          </h3>
          <p className="text-[12px] text-bg-9 mb-2">
            Вкл/выкл сканера, канал авто-стопа и авто-включение —{" "}
            <a
              href="/"
              className="text-accent hover:text-accent-muted underline underline-offset-2"
            >
              на странице «Панель»
            </a>
            .
          </p>

          {/* Интервал скана. */}
          <div className="flex items-center justify-between py-3">
            <div>
              <div className="text-[13px] text-bg-11 font-medium">Интервал скана</div>
              <div className="text-[11px] text-bg-9 mt-0.5">Секунды между сканами (30–600).</div>
            </div>
            {settingsQuery.isLoading ? (
              <Skeleton width={100} height={28} />
            ) : editingInterval ? (
              <div className="flex items-center gap-2">
                <Input
                  size="sm"
                  type="number"
                  value={intervalDraft}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setIntervalDraft(e.target.value)}
                  className="w-20"
                  aria-label="Интервал в секундах"
                />
                <Button
                  size="sm"
                  variant="primary"
                  loading={updateObserver.isPending}
                  onClick={handleSaveInterval}
                >
                  Сохранить
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="Отменить редактирование"
                  onClick={() => setEditingInterval(false)}
                >
                  Отмена
                </Button>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleStartEditInterval}
                title="Нажмите, чтобы изменить интервал"
                className="font-numeric text-[14px] text-accent hover:text-accent-muted transition-colors underline decoration-dotted underline-offset-4"
              >
                {settings?.default_interval_seconds ?? "—"} сек
              </button>
            )}
          </div>

          {/* Тег владельца — money-фильтр чужих кампаний (PUT пишет поле всегда). */}
          <div className="flex items-start justify-between gap-3 py-3 border-t border-bg-3">
            <div className="max-w-md">
              <div className="text-[13px] text-bg-11 font-medium">Тег владельца</div>
              <div className="text-[11px] text-bg-9 mt-0.5 leading-relaxed">
                Бот следит только за кампаниями с этим тегом в названии (несколько — через запятую).
                Пусто — весь кабинет, включая чужие кампании.
              </div>
            </div>
            {settingsQuery.isLoading ? (
              <Skeleton width={100} height={28} />
            ) : editingOwner ? (
              <div className="flex items-center gap-2 shrink-0">
                <Input
                  size="sm"
                  value={ownerDraft}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setOwnerDraft(e.target.value)}
                  className="w-40"
                  placeholder="MV или MV,ABC"
                  aria-label="Тег владельца"
                />
                <Button
                  size="sm"
                  variant="primary"
                  loading={updateObserver.isPending}
                  onClick={handleSaveOwner}
                >
                  Сохранить
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingOwner(false)}>
                  Отмена
                </Button>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleStartEditOwner}
                title="Нажмите, чтобы изменить"
                className="font-numeric text-[14px] text-accent hover:text-accent-muted transition-colors underline decoration-dotted underline-offset-4 shrink-0"
              >
                {settings?.owner_campaign_tag || "весь кабинет"}
              </button>
            )}
          </div>
        </section>

        {/* Кампании для сканирования (allowlist #3). */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
              Кампании для сканирования
            </h3>
            <Button
              size="sm"
              variant="ghost"
              leftIcon={<RefreshCcw size={12} aria-hidden="true" />}
              loading={refreshCampaigns.isPending}
              onClick={handleRefreshCampaigns}
            >
              Обновить список
            </Button>
          </div>
          <p className="text-[11px] text-bg-9 mb-3 leading-relaxed">
            Отметьте конкретные кампании, чтобы сканировать только их. Пусто — сканируем все
            кампании по тегу владельца. «Обновить список» подтянет новые кампании прямо из
            кабинета (даже те, что ещё не сканировались).
          </p>
          {campaignsQuery.isLoading ? (
            <Skeleton height={120} />
          ) : campaignsQuery.isError ? (
            <ErrorState
              title="Не удалось загрузить кампании."
              error={campaignsQuery.error}
              onRetry={() => campaignsQuery.refetch()}
            />
          ) : (campaignsQuery.data?.length ?? 0) === 0 ? (
            <p className="text-[12px] text-bg-9">
              Кампаний пока нет — нажмите «Обновить список», чтобы подтянуть их из кабинета.
            </p>
          ) : (
            <CampaignSelector
              key={campaignsQuery.data!.map((c) => `${c.id}:${c.selected}`).join("|")}
              campaigns={campaignsQuery.data!}
              saving={setCampaigns.isPending}
              onSave={(ids) =>
                setCampaigns.mutate(ids, {
                  onSuccess: () =>
                    toast.success(
                      ids.length ? `Выбрано кампаний: ${ids.length}` : "Сканируем все по тегу",
                    ),
                  onError: (e) => toast.error("Ошибка", e instanceof Error ? e.message : String(e)),
                })
              }
            />
          )}
        </section>

        {/* Таблица scan-runs. */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9">
              Последние сканы
            </h3>
            <Select
              value={scanFilter}
              onChange={(e) => setScanFilter(e.target.value as ScanFilter)}
              options={FILTER_OPTIONS}
              size="sm"
              className="w-36"
            />
          </div>

          {scanRunsQuery.isError ? (
            <ErrorState
              title="Не удалось загрузить историю сканов."
              error={scanRunsQuery.error}
              onRetry={() => scanRunsQuery.refetch()}
            />
          ) : (
            <Table
              data={scanRunsQuery.data ?? []}
              columns={SCAN_COLUMNS}
              virtualized={false}
              loading={scanRunsQuery.isLoading}
              getRowKey={(row) => String(row.id)}
              emptyState={
                <span className="text-bg-9 text-[13px]">
                  Нет данных для выбранного фильтра.
                </span>
              }
            />
          )}
        </section>
      </div>

      {/* Правая колонка: статус + действия. */}
      <div className="space-y-6">
        <section className="border border-bg-5 bg-bg-1 p-5">
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Статус
          </h3>

          {statusQuery.isLoading ? (
            <div className="space-y-3">
              <Skeleton height={18} />
              <Skeleton height={14} width="70%" />
            </div>
          ) : statusQuery.isError ? (
            <ErrorState
              title="Статус недоступен."
              error={statusQuery.error}
              onRetry={() => statusQuery.refetch()}
            />
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    status?.status === "running"
                      ? "success"
                      : status?.status === "paused"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {STATUS_LABEL[status?.status ?? "unknown"] ?? status?.status ?? "неизвестно"}
                </Badge>
              </div>
              <div className="text-[12px] text-bg-9">
                Последний скан:{" "}
                <span className="text-bg-11 font-numeric">
                  {formatRelativeTime(status?.last_scan_at)}
                </span>
                {status?.last_scan_outcome ? (
                  <span className="text-bg-8"> · {OUTCOME_LABEL[status.last_scan_outcome] ?? status.last_scan_outcome}</span>
                ) : null}
              </div>
              <div className="text-[12px] text-bg-9">
                Сканов сегодня:{" "}
                <span className="text-bg-11 font-numeric">{status?.scans_today ?? "—"}</span>
              </div>
            </div>
          )}
        </section>

        <section className="border border-bg-5 bg-bg-1 p-5 space-y-3">
          <h3 className="font-display text-[10px] uppercase tracking-widest text-bg-9 mb-4">
            Действия
          </h3>
          <Button
            variant="secondary"
            size="sm"
            fullWidth
            leftIcon={<Play size={13} aria-hidden="true" />}
            loading={scanNow.isPending}
            onClick={handleScanNow}
          >
            Сканировать
          </Button>
          <Button
            variant="secondary"
            size="sm"
            fullWidth
            leftIcon={<RefreshCcw size={13} aria-hidden="true" />}
            disabled
          >
            Новый день кабинета
          </Button>
          <p className="text-[10px] text-bg-8 leading-relaxed">
            Скоро — ручной запуск нового дня кабинета. Пока автостарт идёт по расписанию
            (Telegram-команда <span className="font-numeric">/autostart</span>).
          </p>
        </section>
      </div>
    </div>
  );
}
