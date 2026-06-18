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
  useToggleScanning,
  useToggleAutoEnable,
  useScanNow,
  useObserverStatus,
  useRestartObserver,
  useStartNewCabinetDay,
  useObserverCampaigns,
  useRefreshObserverCampaigns,
  useSetCampaignAllowlist,
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
  const scanMut = useScanNow();
  const restartMut = useRestartObserver();
  const cabinetDayMut = useStartNewCabinetDay();
  const statusQ = useObserverStatus();

  // Локальное состояние формы
  const [form, setForm] = useState<Partial<ObserverConfig>>({});

  useEffect(() => {
    if (data) {
      setForm({
        is_scanning_enabled: data.is_scanning_enabled,
        auto_enable_recommendations: data.auto_enable_recommendations,
        owner_campaign_tag: data.owner_campaign_tag ?? "",
        default_interval_seconds: data.default_interval_seconds,
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

  // Тоглы — через точечные PATCH (scanning/auto-enable), НЕ partial PUT:
  // PUT требует все обязательные поля → partial body падал 422 и не сохранялся.
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
      setForm((f) => ({ ...f, [field]: !value })); // откат при ошибке сервера
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  // owner_tag — PUT с ПОЛНЫМ body (все обязательные поля), иначе 422.
  const handleSave = () => {
    void save({
      is_scanning_enabled: form.is_scanning_enabled ?? false,
      auto_enable_recommendations: form.auto_enable_recommendations ?? false,
      default_interval_seconds: form.default_interval_seconds ?? 30,
      owner_campaign_tag: form.owner_campaign_tag || null,
    });
  };

  const handleScanNow = async () => {
    try {
      await scanMut.mutateAsync();
      toast.success("Сканирование запущено");
    } catch (e) {
      toast.error("Ошибка запуска скана", e instanceof Error ? e.message : String(e));
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

  const handleNewCabinetDay = async () => {
    try {
      const res = await cabinetDayMut.mutateAsync();
      toast.success(`Новый день кабинета: архив за ${res.archived_date}`);
    } catch (e) {
      toast.error("Ошибка старта нового дня", e instanceof Error ? e.message : String(e));
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

  // Мульти-кабинет: прогресс обхода кабинетов из observer:runtime (через extra).
  const statusExtra = (observerStatus?.extra ?? {}) as Record<string, unknown>;
  const accountsTotal =
    typeof statusExtra.accounts_total === "number" ? statusExtra.accounts_total : null;
  const accountsDone =
    typeof statusExtra.accounts_done === "number" ? statusExtra.accounts_done : null;
  const currentAccount =
    typeof statusExtra.current_account_id === "string" ? statusExtra.current_account_id : null;

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

        {/* Отслеживаемые кампании (allowlist) */}
        <CampaignAllowlist />
      </div>

      {/* ── Правая колонка: статус + действия ── */}
      <div>
        {/* Карточка: Статус */}
        <div
          className="bg-bg-1 border border-[var(--hairline)] rounded-[var(--radius-3)]"
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

          {/* Мульти-кабинет: прогресс обхода (только когда кабинетов > 1) */}
          {accountsTotal != null && accountsTotal > 1 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginTop: 10,
              }}
            >
              <span className="text-[13px]" style={{ color: "var(--bg-10)" }}>Кабинеты</span>
              <span
                className="font-display tabular-nums text-[13px]"
                style={{ color: "var(--bg-11)" }}
                title={currentAccount ? `Сканируется кабинет ${currentAccount}` : undefined}
              >
                {currentAccount
                  ? `${Math.min((accountsDone ?? 0) + 1, accountsTotal)}/${accountsTotal} · …${currentAccount.slice(-4)}`
                  : `${accountsTotal} в скане`}
              </span>
            </div>
          )}
        </div>

        {/* Карточка: Действия */}
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
              Перезапустить observer
            </Button>

            <Button
              variant="secondary"
              leftIcon={<ScanLine size={14} />}
              onClick={() => void handleScanNow()}
              loading={scanMut.isPending}
              style={{ justifyContent: "flex-start" }}
            >
              Сканировать сейчас
            </Button>

            <Button
              variant="secondary"
              leftIcon={<Clock size={14} />}
              onClick={() => void handleNewCabinetDay()}
              loading={cabinetDayMut.isPending}
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

// ─── Отслеживаемые кампании (allowlist по owner_campaign_tag) ───────────────────

const CampaignAllowlist: FC = () => {
  const { data: campaigns, isLoading } = useObserverCampaigns();
  const refreshMut = useRefreshObserverCampaigns();
  const saveMut = useSetCampaignAllowlist();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // selected синхронизируется с серверным allowlist (поле selected у кампании).
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

  return (
    <div style={{ marginTop: "var(--s-8)" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
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
      <div className="text-[11px] mb-3" style={{ color: "var(--bg-8)" }}>
        Пусто (ничего не выбрано) — сканируются все кампании по Owner Tag. Выбор сужает скан до
        отмеченных. «Обновить список» тянет кампании из кабинета живьём через browser-agent.
      </div>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : !campaigns || campaigns.length === 0 ? (
        <div
          className="text-[12px] border border-[var(--hairline)] rounded-[var(--radius-2)]"
          style={{ color: "var(--bg-8)", padding: "var(--s-4)" }}
        >
          Кампаний нет. Нажми «Обновить список» — резолвим из кабинета по Owner Tag.
        </div>
      ) : (
        <div
          className="border border-[var(--hairline)] rounded-[var(--radius-2)] overflow-hidden"
          style={{ maxHeight: 260, overflowY: "auto" }}
        >
          {campaigns.map((c) => (
            <label
              key={c.id}
              className="text-[13px]"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 12px",
                borderBottom: "1px solid var(--hairline)",
                cursor: "pointer",
                color: "var(--bg-10)",
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(c.id)}
                onChange={() => toggle(c.id)}
                aria-label={`Отслеживать ${c.name}`}
              />
              <span style={{ flex: 1 }}>{c.name || c.id}</span>
              <span className="font-display tabular-nums text-[11px]" style={{ color: "var(--bg-7)" }}>
                …{c.id.slice(-4)}
              </span>
            </label>
          ))}
        </div>
      )}

      <div style={{ marginTop: "var(--s-4)" }}>
        <Button
          variant="primary"
          onClick={() => void handleSaveAllowlist()}
          loading={saveMut.isPending}
          disabled={!campaigns || campaigns.length === 0}
        >
          Сохранить выбор ({selected.size})
        </Button>
      </div>
    </div>
  );
};
