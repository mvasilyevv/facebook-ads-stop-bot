import { useEffect, useState } from "react";
import { safeApiProblemMessage } from "@fb/operator-api";

import { Badge, Button, EmptyState, Input, Skeleton } from "@/components/ui";
import {
  useReconnectVision,
  useUpdateVisionSettings,
  useVisionSettings,
} from "@/lib/api";
import { haptic } from "@/lib/tg";

export function VisionSettings({ canEdit }: { canEdit: boolean }) {
  const settingsQuery = useVisionSettings();
  const updateSettings = useUpdateVisionSettings();
  const reconnect = useReconnectVision();
  const [token, setToken] = useState("");
  const [profileId, setProfileId] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  useEffect(() => {
    setProfileId(settingsQuery.data?.profile_id ?? "");
  }, [settingsQuery.data?.profile_id]);

  if (settingsQuery.isLoading) {
    return (
      <div className="space-y-3 pb-4" aria-label="Загрузка настроек Vision">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <EmptyState
        title="Vision недоступен"
        description={safeApiProblemMessage(
          settingsQuery.error,
          "Не удалось получить настройки Vision",
        )}
        action={{
          label: "Повторить",
          onClick: () => void settingsQuery.refetch(),
        }}
      />
    );
  }

  const settings = settingsQuery.data;
  const status = settings.channel_status;
  const statusVariant =
    status === "READY"
      ? "done"
      : status === "DEGRADED" || status === "UNAVAILABLE"
        ? "warning"
        : "neutral";
  const statusLabel =
    status === "READY"
      ? "Канал готов"
      : status === "DEGRADED"
        ? "Канал деградирован"
        : status === "UNAVAILABLE"
          ? "Канал недоступен"
          : "Готовность не подтверждена";

  function fail(error: unknown, fallback: string) {
    haptic.notify("error");
    setReceipt(null);
    setProblem(safeApiProblemMessage(error, fallback));
  }

  async function handleSave() {
    if (!canEdit) return;
    try {
      await updateSettings.mutateAsync({
        profile_id: profileId.trim() || null,
        ...(token.trim() ? { x_token: token.trim() } : {}),
      });
      setToken("");
      setProblem(null);
      setReceipt("Конфигурация Vision сохранена");
      haptic.notify("success");
    } catch (error) {
      fail(error, "Конфигурация Vision не сохранена");
    }
  }

  async function handleReconnect() {
    if (!canEdit) return;
    try {
      await reconnect.mutateAsync();
      setProblem(null);
      setReceipt("Vision переподключён. Статус канала обновляется.");
      haptic.notify("success");
    } catch (error) {
      fail(error, "Vision не переподключён");
    }
  }

  return (
    <div className="space-y-5 pb-4">
      {!canEdit ? (
        <p
          role="status"
          className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] text-warning"
        >
          Управлять Vision может только владелец.
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

      <section aria-labelledby="mini-vision-status">
        <h3
          id="mini-vision-status"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Готовность канала
        </h3>
        <div className="mt-3 border-y border-[var(--color-hairline)]">
          <StatusRow label="Browser channel">
            <Badge variant={statusVariant}>{statusLabel}</Badge>
          </StatusRow>
          <StatusRow label="X-Token">
            <Badge variant={settings.has_token ? "neutral" : "warning"}>
              {settings.has_token ? "Задан" : "Не задан"}
            </Badge>
          </StatusRow>
          <StatusRow label="Контракт браузера" noBorder>
            <Badge
              variant={
                settings.browser_contract_compatible ? "neutral" : "warning"
              }
            >
              {settings.browser_contract_compatible
                ? "Совместим"
                : "Не подтверждён"}
            </Badge>
          </StatusRow>
        </div>
        <p className="m-0 mt-3 text-[13px] leading-5 text-bg-8">
          {status === "READY"
            ? "Канал и browser contract подтверждены."
            : "Готовность не подтверждена: денежные операции не считаются доступными."}
        </p>
        <Button
          className="mt-3"
          variant="secondary"
          fullWidth
          disabled={!canEdit}
          loading={reconnect.isPending}
          onClick={() => void handleReconnect()}
        >
          Переподключить Vision
        </Button>
      </section>

      <section aria-labelledby="mini-vision-config">
        <h3
          id="mini-vision-config"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Конфигурация
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          Секрет хранится зашифрованным и после сохранения больше не
          показывается.
        </p>
        <div className="mt-3 space-y-4 border-y border-[var(--color-hairline)] py-4">
          <Input
            label="Новый X-Token"
            type="password"
            value={token}
            disabled={!canEdit}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="new-password"
            spellCheck={false}
          />
          <Input
            label="Profile ID"
            value={profileId}
            disabled={!canEdit}
            onChange={(event) => setProfileId(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            fullWidth
            disabled={!canEdit}
            loading={updateSettings.isPending}
            onClick={() => void handleSave()}
          >
            Сохранить Vision
          </Button>
        </div>
      </section>
    </div>
  );
}

function StatusRow({
  label,
  children,
  noBorder = false,
}: {
  label: string;
  children: React.ReactNode;
  noBorder?: boolean;
}) {
  return (
    <div
      className={`flex min-h-11 items-center justify-between gap-3 py-2.5 ${
        noBorder ? "" : "border-b border-[var(--color-hairline)]"
      }`}
    >
      <span className="text-[14px] text-bg-10">{label}</span>
      <span className="shrink-0">{children}</span>
    </div>
  );
}
