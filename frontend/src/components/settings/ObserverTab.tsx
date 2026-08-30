import { useEffect, useState, type FC, type ReactNode } from "react";
import {
  normalizeOwnerCampaignTags,
  OBSERVER_INTERVAL_MAX_SECONDS,
  OBSERVER_INTERVAL_MIN_SECONDS,
  validateObserverInterval,
} from "@fb/features/settings";
import { safeApiProblemMessage } from "@fb/operator-api";
import { RefreshCw, RotateCcw, ScanSearch } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { toast } from "@/components/ui/Toast";
import {
  useObserverCampaigns,
  useObserverSettings,
  useRefreshObserverCampaigns,
  useScanObserverNow,
  useSetCampaignAllowlist,
  useToggleScanning,
  useUpdateAdsManagerColumns,
  useUpdateObserverInterval,
  useUpdateOwnerTag,
} from "@/lib/api/settings";

interface SettingRowProps {
  label: string;
  hint: string;
  children: ReactNode;
}

function SettingRow({ label, hint, children }: SettingRowProps) {
  return (
    <div className="grid gap-3 border-b border-[var(--color-hairline)] py-4 last:border-b-0 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)] sm:items-center">
      <div>
        <div className="text-[14px] font-medium text-bg-11">{label}</div>
        <div className="mt-1 max-w-[54ch] text-[13px] leading-5 text-bg-8">{hint}</div>
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export const ObserverTab: FC = () => {
  const settingsQuery = useObserverSettings();
  const [includeStale, setIncludeStale] = useState(false);
  const campaignsQuery = useObserverCampaigns(includeStale);
  const updateInterval = useUpdateObserverInterval();
  const toggleScanning = useToggleScanning();
  const updateOwnerTag = useUpdateOwnerTag();
  const setAllowlist = useSetCampaignAllowlist();
  const refreshCampaigns = useRefreshObserverCampaigns();
  const scanNow = useScanObserverNow();
  const updateAdsManagerColumns = useUpdateAdsManagerColumns();

  const [interval, setInterval] = useState("60");
  const [ownerTags, setOwnerTags] = useState("");
  const [selectedCampaigns, setSelectedCampaigns] = useState<string[]>([]);
  const [selectedAmColumns, setSelectedAmColumns] = useState<string[]>([]);
  const [intervalError, setIntervalError] = useState<string | null>(null);
  const [stopScanningConfirmOpen, setStopScanningConfirmOpen] = useState(false);

  useEffect(() => {
    if (!settingsQuery.data) return;
    setInterval(String(settingsQuery.data.default_interval_seconds));
    setOwnerTags(settingsQuery.data.owner_campaign_tag ?? "");
    setSelectedCampaigns(settingsQuery.data.campaign_ids ?? []);
    setSelectedAmColumns(settingsQuery.data.am_columns ?? []);
  }, [settingsQuery.data]);

  if (settingsQuery.isLoading) {
    return (
      <div className="max-w-4xl space-y-3" aria-label="Загрузка настроек Observer">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (settingsQuery.error || !settingsQuery.data) {
    return (
      <div role="alert" className="max-w-3xl border-y border-[var(--color-hairline)] py-5">
        <p className="m-0 text-[14px] text-danger">
          {safeApiProblemMessage(settingsQuery.error, "Настройки Observer временно недоступны")}
        </p>
        <Button className="mt-4" variant="secondary" onClick={() => void settingsQuery.refetch()}>
          Повторить
        </Button>
      </div>
    );
  }

  const settings = settingsQuery.data;
  const campaigns = campaignsQuery.data ?? [];

  async function saveInterval() {
    const validationError = validateObserverInterval(interval);
    setIntervalError(validationError);
    if (validationError) return;
    try {
      await updateInterval.mutateAsync(Number(interval));
      toast.success("Интервал Observer сохранён");
    } catch (error) {
      toast.error(
        "Не удалось сохранить интервал",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  async function saveOwnerTags() {
    try {
      await updateOwnerTag.mutateAsync(normalizeOwnerCampaignTags(ownerTags));
      toast.success("Теги владельца сохранены");
    } catch (error) {
      toast.error("Не удалось сохранить теги", safeApiProblemMessage(error, "Повторите попытку"));
    }
  }

  async function saveAllowlist() {
    try {
      await setAllowlist.mutateAsync(selectedCampaigns);
      toast.success(
        selectedCampaigns.length
          ? "Список отслеживаемых кампаний сохранён"
          : "Фильтр кампаний очищен",
      );
    } catch (error) {
      toast.error(
        "Не удалось сохранить кампании",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  /** false — состояние не изменилось; причина уже показана тостом. */
  async function applyScanning(next: boolean): Promise<boolean> {
    try {
      await toggleScanning.mutateAsync(next);
      toast.success(next ? "Сканирование включено" : "Сканирование остановлено");
      return true;
    } catch (error) {
      toast.error(
        "Состояние сканирования не изменено",
        safeApiProblemMessage(error, "Проверьте готовность Observer"),
      );
      return false;
    }
  }

  async function confirmStopScanning() {
    // ConfirmDialog закрывается только при успехе: неудача оставляет диалог открытым.
    if (!(await applyScanning(false))) throw new Error("scanning-not-stopped");
  }

  function handleToggleScanning() {
    // Выключение ослепляет авто-стоп, поэтому проходит через подтверждение;
    // включение возвращает наблюдение и подтверждения не требует.
    if (settings.is_scanning_enabled) {
      setStopScanningConfirmOpen(true);
      return;
    }
    void applyScanning(true);
  }

  async function handleScanNow() {
    try {
      await scanNow.mutateAsync();
      // 202 = queued: результат сканирования ещё не подтверждён, зелёный тон запрещён.
      toast.info(
        "Сканирование поставлено в очередь",
        "Завершение ещё не подтверждено. Дождитесь обновления снимка.",
      );
    } catch (error) {
      toast.error(
        "Сканирование не поставлено в очередь",
        safeApiProblemMessage(error, "Проверьте готовность Observer"),
      );
    }
  }

  async function handleRefreshCampaigns() {
    try {
      await refreshCampaigns.mutateAsync(includeStale);
      toast.success("Список кампаний обновлён");
    } catch (error) {
      toast.error(
        "Не удалось обновить кампании",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  async function saveAdsManagerColumns() {
    if (!selectedAmColumns.length) return;
    try {
      await updateAdsManagerColumns.mutateAsync(selectedAmColumns);
      toast.success("Колонки Ads Manager сохранены");
    } catch (error) {
      toast.error(
        "Не удалось сохранить колонки",
        safeApiProblemMessage(error, "Повторите попытку"),
      );
    }
  }

  async function resetAdsManagerColumns() {
    try {
      const updated = await updateAdsManagerColumns.mutateAsync(null);
      setSelectedAmColumns(updated.am_columns);
      toast.success("Восстановлен системный набор колонок");
    } catch (error) {
      toast.error("Не удалось сбросить колонки", safeApiProblemMessage(error, "Повторите попытку"));
    }
  }

  return (
    <div className="max-w-4xl space-y-8">
      <section aria-labelledby="observer-runtime-heading">
        <div className="mb-2 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="observer-runtime-heading" className="m-0 text-[16px] font-medium text-bg-11">
              Ритм наблюдения
            </h2>
            <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
              Автопауза остаётся разрешена только при свежем полном snapshot и активном
              детерминированном правиле.
            </p>
          </div>
          <span className="text-[13px] text-bg-9" role="status">
            {settings.is_scanning_enabled ? "Сканирование включено" : "Сканирование остановлено"}
          </span>
        </div>

        <div className="border-y border-[var(--color-hairline)]">
          <SettingRow
            label="Периодическое сканирование"
            hint="Остановка прекращает новые циклы Observer. Она не активирует объявления и не отменяет уже подтверждённые команды."
          >
            <Switch
              checked={settings.is_scanning_enabled}
              onChange={handleToggleScanning}
              disabled={toggleScanning.isPending}
              label={
                settings.is_scanning_enabled
                  ? "Остановить периодическое сканирование"
                  : "Включить периодическое сканирование"
              }
              visualLabel={settings.is_scanning_enabled ? "Включено" : "Остановлено"}
            />
          </SettingRow>

          <SettingRow
            label="Интервал"
            hint={`Допустимый диапазон: ${OBSERVER_INTERVAL_MIN_SECONDS}–${OBSERVER_INTERVAL_MAX_SECONDS} секунд.`}
          >
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
              <Input
                id="observer-interval"
                aria-label="Интервал сканирования в секундах"
                type="number"
                min={OBSERVER_INTERVAL_MIN_SECONDS}
                max={OBSERVER_INTERVAL_MAX_SECONDS}
                inputMode="numeric"
                value={interval}
                errorMessage={intervalError ?? undefined}
                onChange={(event) => {
                  setInterval(event.target.value);
                  if (intervalError) setIntervalError(null);
                }}
                className="sm:max-w-40"
              />
              <Button
                variant="secondary"
                onClick={() => void saveInterval()}
                loading={updateInterval.isPending}
              >
                Сохранить интервал
              </Button>
            </div>
          </SettingRow>

          <SettingRow
            label="Сканировать сейчас"
            hint="Создаёт отдельную задачу. Ответ означает только постановку в очередь, не завершённый scan."
          >
            <Button
              variant="secondary"
              leftIcon={<ScanSearch size={15} aria-hidden="true" />}
              onClick={() => void handleScanNow()}
              loading={scanNow.isPending}
            >
              Поставить scan в очередь
            </Button>
          </SettingRow>
        </div>
      </section>

      <section aria-labelledby="ads-manager-columns-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="ads-manager-columns-heading" className="m-0 text-[16px] font-medium text-bg-11">
              Вкладка Ads Manager
            </h2>
            <p className="m-0 mt-1 max-w-[70ch] text-[13px] leading-5 text-bg-8">
              Эти галочки меняют только колонки, которые видны человеку. Метрики сканирования и
              правила автостопа используют отдельный фиксированный набор. Изменение применится к
              вкладке при следующем scan.
            </p>
          </div>
          <span className="text-[13px] text-bg-9" role="status">
            {settings.am_columns_use_default ? "Fallback browser-agent" : "Свой набор"}
          </span>
        </div>

        {settings.am_columns_use_default ? (
          <p className="m-0 mt-2 max-w-[70ch] text-[13px] leading-5 text-bg-8">
            Активен системный fallback: сначала env-настройка browser-agent, затем встроенный набор.
            Галочки ниже показывают встроенный набор; точное env-переопределение API не видит.
          </p>
        ) : null}

        <fieldset className="mt-3">
          <legend className="sr-only">Колонки видимой вкладки Ads Manager</legend>
          <div className="grid border-y border-[var(--color-hairline)] sm:grid-cols-2">
            {settings.am_column_options.map((column, index) => {
              const checked = selectedAmColumns.includes(column.id);
              return (
                <label
                  key={column.id}
                  className={`flex min-h-11 cursor-pointer items-center gap-3 py-2.5 text-[14px] text-bg-10 ${
                    index % 2 === 0
                      ? "sm:border-r sm:border-[var(--color-hairline)] sm:pr-4"
                      : "sm:pl-4"
                  } border-b border-[var(--color-hairline)]`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setSelectedAmColumns((current) =>
                        checked
                          ? current.filter((id) => id !== column.id)
                          : [...current, column.id],
                      )
                    }
                    className="size-4 shrink-0 accent-[var(--color-accent)]"
                  />
                  <span>{column.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="m-0 text-[13px] text-bg-8">
            Выбрано: {selectedAmColumns.length} из {settings.am_column_options.length}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="ghost"
              leftIcon={<RotateCcw size={14} aria-hidden="true" />}
              onClick={() => void resetAdsManagerColumns()}
              loading={updateAdsManagerColumns.isPending}
            >
              Сбросить к дефолту
            </Button>
            <Button
              variant="primary"
              disabled={!selectedAmColumns.length}
              onClick={() => void saveAdsManagerColumns()}
              loading={updateAdsManagerColumns.isPending}
            >
              Сохранить колонки
            </Button>
          </div>
        </div>
      </section>

      <section aria-labelledby="observer-scope-heading">
        <h2 id="observer-scope-heading" className="m-0 text-[16px] font-medium text-bg-11">
          Область сканирования
        </h2>
        <p className="m-0 mt-1 max-w-[70ch] text-[13px] leading-5 text-bg-8">
          Теги ограничивают владельца кампаний, allowlist — конкретные кампании. Пустое значение
          снимает соответствующий фильтр.
        </p>

        <div className="mt-3 border-y border-[var(--color-hairline)]">
          <SettingRow
            label="Теги владельца"
            hint="Один или несколько тегов через запятую. Например: MV,ABC."
          >
            <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
              <Input
                id="observer-owner-tags"
                aria-label="Теги владельца кампаний"
                value={ownerTags}
                placeholder="MV,ABC"
                onChange={(event) => setOwnerTags(event.target.value)}
              />
              <Button
                variant="secondary"
                onClick={() => void saveOwnerTags()}
                loading={updateOwnerTag.isPending}
              >
                Сохранить теги
              </Button>
            </div>
          </SettingRow>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          <Switch
            checked={includeStale}
            onChange={() => setIncludeStale((value) => !value)}
            label="Показывать старые кампании"
            visualLabel="Показывать кампании старше 14 дней"
          />
          <Button
            variant="ghost"
            leftIcon={<RefreshCw size={14} aria-hidden="true" />}
            onClick={() => void handleRefreshCampaigns()}
            loading={refreshCampaigns.isPending}
          >
            Обновить список
          </Button>
        </div>

        {campaignsQuery.isError ? (
          <div role="alert" className="mt-4 border-y border-[var(--color-hairline)] py-4">
            <p className="m-0 text-[14px] text-danger">
              {safeApiProblemMessage(campaignsQuery.error, "Список кампаний временно недоступен")}
            </p>
            <Button
              className="mt-3"
              variant="secondary"
              onClick={() => void campaignsQuery.refetch()}
            >
              Повторить
            </Button>
          </div>
        ) : campaignsQuery.isLoading ? (
          <div className="mt-4 space-y-2">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : campaigns.length ? (
          <fieldset className="mt-4">
            <legend className="sr-only">Кампании для сканирования</legend>
            <div className="max-h-[360px] overflow-y-auto border-y border-[var(--color-hairline)]">
              {campaigns.map((campaign) => {
                const checked = selectedCampaigns.includes(campaign.id);
                return (
                  <label
                    key={campaign.id}
                    className="flex min-h-11 cursor-pointer items-center gap-3 border-b border-[var(--color-hairline)] py-2.5 last:border-b-0"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setSelectedCampaigns((current) =>
                          checked
                            ? current.filter((id) => id !== campaign.id)
                            : [...current, campaign.id],
                        )
                      }
                      className="size-4 shrink-0 accent-[var(--color-accent)]"
                    />
                    <span className="min-w-0 break-words text-[14px] text-bg-10">
                      {campaign.name}
                    </span>
                  </label>
                );
              })}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <p className="m-0 text-[13px] text-bg-8">
                {selectedCampaigns.length
                  ? `Выбрано: ${selectedCampaigns.length}`
                  : "Allowlist пуст: фильтр по кампаниям отключён"}
              </p>
              <Button
                variant="primary"
                onClick={() => void saveAllowlist()}
                loading={setAllowlist.isPending}
              >
                Сохранить allowlist
              </Button>
            </div>
          </fieldset>
        ) : (
          <div className="mt-4 border-y border-[var(--color-hairline)] py-5 text-[14px] text-bg-9">
            Кампании не найдены. Обновите список после проверки кабинетов и активных офферов.
          </div>
        )}
      </section>

      <ConfirmDialog
        open={stopScanningConfirmOpen}
        onOpenChange={setStopScanningConfirmOpen}
        title="Выключить сканирование?"
        description="Авто-стоп перестанет следить за кабинетами до включения."
        confirmLabel="Выключить"
        confirmVariant="warning"
        onConfirm={confirmStopScanning}
      />
    </div>
  );
};
