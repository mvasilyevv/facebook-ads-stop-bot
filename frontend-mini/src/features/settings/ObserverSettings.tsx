import { useEffect, useState } from "react";
import {
  normalizeOwnerCampaignTags,
  OBSERVER_INTERVAL_MAX_SECONDS,
  OBSERVER_INTERVAL_MIN_SECONDS,
  validateObserverInterval,
} from "@fb/features/settings";
import { safeApiProblemMessage } from "@fb/operator-api";
import { RefreshCw, RotateCcw, ScanSearch } from "lucide-react";

import { Button, EmptyState, Input, Skeleton, Switch } from "@/components/ui";
import {
  useObserverCampaigns,
  useObserverSettings,
  useRefreshObserverCampaigns,
  useScanObserverNow,
  useSetObserverCampaignAllowlist,
  useToggleObserverScanning,
  useUpdateAdsManagerColumns,
  useUpdateObserverOwnerTag,
  useUpdateObserverInterval,
} from "@/lib/api";
import { haptic } from "@/lib/tg";

export function ObserverSettings({ canEdit }: { canEdit: boolean }) {
  const settingsQuery = useObserverSettings();
  const [includeStale, setIncludeStale] = useState(false);
  const campaignsQuery = useObserverCampaigns(includeStale);
  const toggleScanning = useToggleObserverScanning();
  const updateInterval = useUpdateObserverInterval();
  const updateOwnerTag = useUpdateObserverOwnerTag();
  const setAllowlist = useSetObserverCampaignAllowlist();
  const refreshCampaigns = useRefreshObserverCampaigns();
  const scanNow = useScanObserverNow();
  const updateAdsManagerColumns = useUpdateAdsManagerColumns();

  const [interval, setInterval] = useState("60");
  const [ownerTags, setOwnerTags] = useState("");
  const [selectedCampaigns, setSelectedCampaigns] = useState<string[]>([]);
  const [selectedAmColumns, setSelectedAmColumns] = useState<string[]>([]);
  const [intervalError, setIntervalError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  useEffect(() => {
    if (!settingsQuery.data) return;
    setInterval(String(settingsQuery.data.default_interval_seconds));
    setOwnerTags(settingsQuery.data.owner_campaign_tag ?? "");
    setSelectedCampaigns(settingsQuery.data.campaign_ids ?? []);
    setSelectedAmColumns(settingsQuery.data.am_columns ?? []);
  }, [settingsQuery.data]);

  function mutationProblem(error: unknown, fallback: string) {
    haptic.notify("error");
    setReceipt(null);
    setProblem(safeApiProblemMessage(error, fallback));
  }

  function mutationSuccess(message: string) {
    haptic.notify("success");
    setProblem(null);
    setReceipt(message);
  }

  if (settingsQuery.isLoading) {
    return (
      <div className="space-y-3 pb-4" aria-label="Загрузка настроек Observer">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <EmptyState
        title="Observer недоступен"
        description={safeApiProblemMessage(
          settingsQuery.error,
          "Не удалось получить настройки Observer",
        )}
        action={{
          label: "Повторить",
          onClick: () => void settingsQuery.refetch(),
        }}
      />
    );
  }

  const settings = settingsQuery.data;

  async function handleToggleScanning() {
    if (!canEdit) return;
    const enabled = !settings.is_scanning_enabled;
    try {
      await toggleScanning.mutateAsync(enabled);
      mutationSuccess(
        enabled ? "Сканирование включено" : "Сканирование остановлено",
      );
    } catch (error) {
      mutationProblem(error, "Состояние сканирования не изменено");
    }
  }

  async function saveInterval() {
    if (!canEdit) return;
    const validationError = validateObserverInterval(interval);
    setIntervalError(validationError);
    if (validationError) return;
    try {
      await updateInterval.mutateAsync(Number(interval));
      mutationSuccess("Интервал сохранён");
    } catch (error) {
      mutationProblem(error, "Интервал не сохранён");
    }
  }

  async function saveOwnerTags() {
    if (!canEdit) return;
    try {
      await updateOwnerTag.mutateAsync(normalizeOwnerCampaignTags(ownerTags));
      mutationSuccess("Теги владельца сохранены");
    } catch (error) {
      mutationProblem(error, "Теги не сохранены");
    }
  }

  async function handleScanNow() {
    if (!canEdit) return;
    try {
      await scanNow.mutateAsync();
      mutationSuccess(
        "Scan поставлен в очередь. Это ещё не завершённое сканирование.",
      );
    } catch (error) {
      mutationProblem(error, "Scan не поставлен в очередь");
    }
  }

  async function handleRefreshCampaigns() {
    if (!canEdit) return;
    try {
      await refreshCampaigns.mutateAsync(includeStale);
      mutationSuccess("Список кампаний обновлён");
    } catch (error) {
      mutationProblem(error, "Список кампаний не обновлён");
    }
  }

  async function saveCampaigns() {
    if (!canEdit) return;
    try {
      await setAllowlist.mutateAsync(selectedCampaigns);
      mutationSuccess(
        selectedCampaigns.length
          ? "Allowlist кампаний сохранён"
          : "Фильтр по кампаниям отключён",
      );
    } catch (error) {
      mutationProblem(error, "Allowlist не сохранён");
    }
  }

  async function saveAdsManagerColumns() {
    if (!canEdit || !selectedAmColumns.length) return;
    try {
      await updateAdsManagerColumns.mutateAsync(selectedAmColumns);
      mutationSuccess("Колонки Ads Manager сохранены");
    } catch (error) {
      mutationProblem(error, "Колонки Ads Manager не сохранены");
    }
  }

  async function resetAdsManagerColumns() {
    if (!canEdit) return;
    try {
      const updated = await updateAdsManagerColumns.mutateAsync(null);
      setSelectedAmColumns(updated.am_columns);
      mutationSuccess("Восстановлен системный набор колонок");
    } catch (error) {
      mutationProblem(error, "Колонки Ads Manager не сброшены");
    }
  }

  return (
    <div className="space-y-6 pb-4">
      {!canEdit ? (
        <p
          role="status"
          className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] text-warning"
        >
          Управлять Observer может только владелец.
        </p>
      ) : null}

      {problem ? (
        <p
          role="alert"
          className="m-0 border-y border-danger/40 py-3 text-[14px] leading-5 text-danger"
        >
          {problem}
        </p>
      ) : null}
      {receipt ? (
        <p
          role="status"
          className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] leading-5 text-bg-10"
        >
          {receipt}
        </p>
      ) : null}

      <section aria-labelledby="mini-observer-runtime">
        <h3
          id="mini-observer-runtime"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Ритм наблюдения
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          Автопауза возможна только по свежим полным данным. Другие
          money-действия автоматически не выполняются.
        </p>

        <div className="mt-3 border-y border-[var(--color-hairline)]">
          <Switch
            label={
              settings.is_scanning_enabled
                ? "Остановить периодическое сканирование"
                : "Включить периодическое сканирование"
            }
            checked={settings.is_scanning_enabled}
            disabled={!canEdit || toggleScanning.isPending}
            onChange={() => void handleToggleScanning()}
          />

          <div className="border-t border-[var(--color-hairline)] py-4">
            <Input
              label="Интервал, секунд"
              type="number"
              min={OBSERVER_INTERVAL_MIN_SECONDS}
              max={OBSERVER_INTERVAL_MAX_SECONDS}
              inputMode="numeric"
              value={interval}
              disabled={!canEdit}
              errorMessage={intervalError ?? undefined}
              onChange={(event) => {
                setInterval(event.target.value);
                if (intervalError) setIntervalError(null);
              }}
            />
            <Button
              className="mt-3"
              variant="secondary"
              fullWidth
              disabled={!canEdit}
              loading={updateInterval.isPending}
              onClick={() => void saveInterval()}
            >
              Сохранить интервал
            </Button>
          </div>

          <div className="border-t border-[var(--color-hairline)] py-4">
            <Button
              variant="secondary"
              fullWidth
              disabled={!canEdit}
              loading={scanNow.isPending}
              onClick={() => void handleScanNow()}
            >
              <ScanSearch size={16} aria-hidden="true" />
              Поставить scan в очередь
            </Button>
          </div>
        </div>
      </section>

      <section aria-labelledby="mini-ads-manager-columns">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h3
              id="mini-ads-manager-columns"
              className="m-0 text-[15px] font-medium text-bg-11"
            >
              Вкладка Ads Manager
            </h3>
            <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
              Меняет только видимые человеку колонки. Метрики скана и автостопа
              остаются фиксированными. Применится при следующем scan.
            </p>
          </div>
          <span className="shrink-0 text-[12px] text-bg-8" role="status">
            {settings.am_columns_use_default
              ? "Fallback browser-agent"
              : "Свой набор"}
          </span>
        </div>

        {settings.am_columns_use_default ? (
          <p className="m-0 mt-2 text-[13px] leading-5 text-bg-8">
            Сначала используется env browser-agent, затем встроенный набор.
            Галочки показывают встроенный набор; точное env-переопределение
            здесь недоступно.
          </p>
        ) : null}

        <fieldset className="mt-3">
          <legend className="sr-only">
            Колонки видимой вкладки Ads Manager
          </legend>
          <div className="max-h-[360px] overflow-y-auto border-y border-[var(--color-hairline)]">
            {settings.am_column_options.map((column) => {
              const checked = selectedAmColumns.includes(column.id);
              return (
                <label
                  key={column.id}
                  className="flex min-h-11 items-center gap-3 border-b border-[var(--color-hairline)] py-2.5 last:border-b-0"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!canEdit}
                    onChange={() =>
                      setSelectedAmColumns((current) =>
                        checked
                          ? current.filter((id) => id !== column.id)
                          : [...current, column.id],
                      )
                    }
                    className="size-4 shrink-0 accent-[var(--color-accent)]"
                  />
                  <span className="min-w-0 text-[14px] leading-5 text-bg-10">
                    {column.label}
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <p className="m-0 mt-2 text-[13px] text-bg-8">
          Выбрано: {selectedAmColumns.length} из{" "}
          {settings.am_column_options.length}
        </p>
        <div className="mt-3 grid gap-2">
          <Button
            fullWidth
            disabled={!canEdit || !selectedAmColumns.length}
            loading={updateAdsManagerColumns.isPending}
            onClick={() => void saveAdsManagerColumns()}
          >
            Сохранить колонки
          </Button>
          <Button
            variant="ghost"
            fullWidth
            disabled={!canEdit}
            loading={updateAdsManagerColumns.isPending}
            onClick={() => void resetAdsManagerColumns()}
          >
            <RotateCcw size={15} aria-hidden="true" />
            Сбросить к дефолту
          </Button>
        </div>
      </section>

      <section aria-labelledby="mini-observer-scope">
        <h3
          id="mini-observer-scope"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Область сканирования
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          Пустой tag или allowlist снимает соответствующий фильтр.
        </p>

        <div className="mt-3 border-y border-[var(--color-hairline)] py-4">
          <Input
            label="Теги владельца"
            placeholder="MV,ABC"
            value={ownerTags}
            disabled={!canEdit}
            onChange={(event) => setOwnerTags(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            className="mt-3"
            variant="secondary"
            fullWidth
            disabled={!canEdit}
            loading={updateOwnerTag.isPending}
            onClick={() => void saveOwnerTags()}
          >
            Сохранить теги
          </Button>
        </div>

        <div className="flex min-h-11 items-center justify-between gap-3 border-b border-[var(--color-hairline)] py-2">
          <Switch
            label="Показывать кампании старше 14 дней"
            checked={includeStale}
            disabled={!canEdit}
            onChange={(event) => setIncludeStale(event.target.checked)}
          />
        </div>

        <Button
          className="mt-3"
          variant="ghost"
          fullWidth
          disabled={!canEdit}
          loading={refreshCampaigns.isPending}
          onClick={() => void handleRefreshCampaigns()}
        >
          <RefreshCw size={15} aria-hidden="true" />
          Обновить список кампаний
        </Button>

        {campaignsQuery.isLoading ? (
          <div className="mt-3 space-y-2">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        ) : campaignsQuery.isError ? (
          <EmptyState
            title="Кампании недоступны"
            description={safeApiProblemMessage(
              campaignsQuery.error,
              "Не удалось получить список кампаний",
            )}
            action={{
              label: "Повторить",
              onClick: () => void campaignsQuery.refetch(),
            }}
          />
        ) : campaignsQuery.data?.length ? (
          <fieldset className="mt-3">
            <legend className="sr-only">Кампании для сканирования</legend>
            <div className="max-h-[320px] overflow-y-auto border-y border-[var(--color-hairline)]">
              {campaignsQuery.data.map((campaign) => {
                const checked = selectedCampaigns.includes(campaign.id);
                return (
                  <label
                    key={campaign.id}
                    className="flex min-h-11 items-center gap-3 border-b border-[var(--color-hairline)] py-2.5 last:border-b-0"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!canEdit}
                      onChange={() =>
                        setSelectedCampaigns((current) =>
                          checked
                            ? current.filter((id) => id !== campaign.id)
                            : [...current, campaign.id],
                        )
                      }
                      className="size-4 shrink-0 accent-[var(--color-accent)]"
                    />
                    <span className="min-w-0 break-words text-[14px] leading-5 text-bg-10">
                      {campaign.name}
                    </span>
                  </label>
                );
              })}
            </div>
            <p className="m-0 mt-2 text-[13px] text-bg-8">
              {selectedCampaigns.length
                ? `Выбрано: ${selectedCampaigns.length}`
                : "Allowlist пуст: сканируются все подходящие кампании"}
            </p>
            <Button
              className="mt-3"
              fullWidth
              disabled={!canEdit}
              loading={setAllowlist.isPending}
              onClick={() => void saveCampaigns()}
            >
              Сохранить allowlist
            </Button>
          </fieldset>
        ) : (
          <p className="m-0 mt-3 border-y border-[var(--color-hairline)] py-4 text-[14px] leading-5 text-bg-9">
            Кампании не найдены. Проверьте кабинеты активных офферов и обновите
            список.
          </p>
        )}
      </section>
    </div>
  );
}
