import { useEffect, useState } from "react";
import { isSafeTelegramWebAppUrl } from "@fb/features/settings";
import { safeApiProblemMessage } from "@fb/operator-api";
import { Copy, UserPlus } from "lucide-react";

import { Badge, Button, EmptyState, Input, Skeleton } from "@/components/ui";
import {
  useCreateTelegramOwnerInvite,
  useCreateTelegramRecipientInvite,
  useDeleteTelegramRecipient,
  useDeleteTelegramToken,
  useTelegramNotificationDiagnostics,
  useTelegramRecipients,
  useTelegramSettings,
  useUpdateTelegramToken,
  useUpdateTelegramWebAppUrl,
  type TelegramRecipient,
} from "@/lib/api";
import { haptic } from "@/lib/tg";
import { TelegramRecipientPreferences } from "./TelegramRecipientPreferences";

type Invite = {
  activation_command: string;
  auth_deep_link?: string | null;
  expires_at: string;
  role: string;
};

export function TelegramSettings({ canEdit }: { canEdit: boolean }) {
  const settingsQuery = useTelegramSettings();
  const diagnosticsQuery = useTelegramNotificationDiagnostics();
  const recipientsQuery = useTelegramRecipients();
  const updateToken = useUpdateTelegramToken();
  const deleteToken = useDeleteTelegramToken();
  const updateWebAppUrl = useUpdateTelegramWebAppUrl();
  const createOwnerInvite = useCreateTelegramOwnerInvite();
  const createRecipientInvite = useCreateTelegramRecipientInvite();
  const deleteRecipient = useDeleteTelegramRecipient();

  const [token, setToken] = useState("");
  const [webAppUrl, setWebAppUrl] = useState("");
  const [webAppUrlError, setWebAppUrlError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [selectedRecipient, setSelectedRecipient] =
    useState<TelegramRecipient | null>(null);
  const [deleteTokenArmed, setDeleteTokenArmed] = useState(false);
  const [removeRecipientId, setRemoveRecipientId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    setWebAppUrl(settingsQuery.data?.web_app_url ?? "");
  }, [settingsQuery.data?.web_app_url]);

  if (selectedRecipient) {
    return (
      <TelegramRecipientPreferences
        recipient={selectedRecipient}
        label={recipientLabel(selectedRecipient, 0)}
        canEdit={canEdit}
        onBack={() => setSelectedRecipient(null)}
      />
    );
  }

  if (settingsQuery.isLoading) {
    return (
      <div className="space-y-3 pb-4" aria-label="Загрузка настроек Telegram">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (settingsQuery.isError || !settingsQuery.data) {
    return (
      <EmptyState
        title="Telegram недоступен"
        description={safeApiProblemMessage(
          settingsQuery.error,
          "Не удалось получить настройки Telegram",
        )}
        action={{
          label: "Повторить",
          onClick: () => void settingsQuery.refetch(),
        }}
      />
    );
  }

  const settings = settingsQuery.data;
  const diagnostics = diagnosticsQuery.data;
  const activeOutbox = diagnostics
    ? [
        diagnostics.inbox_counts,
        diagnostics.delivery_counts,
        diagnostics.command_reply_counts,
      ].reduce(
        (total, counts) =>
          total +
          (counts.pending ?? 0) +
          (counts.retry ?? 0) +
          (counts.leased ?? 0),
        0,
      )
    : null;
  const failedOutbox = diagnostics
    ? [
        diagnostics.inbox_counts,
        diagnostics.delivery_counts,
        diagnostics.command_reply_counts,
      ].reduce(
        (total, counts) => total + (counts.dead ?? 0) + (counts.unknown ?? 0),
        0,
      )
    : null;

  function fail(error: unknown, fallback: string) {
    haptic.notify("error");
    setReceipt(null);
    setProblem(safeApiProblemMessage(error, fallback));
  }

  function success(message: string) {
    haptic.notify("success");
    setProblem(null);
    setReceipt(message);
  }

  async function handleSaveToken() {
    if (!canEdit || !token.trim()) return;
    try {
      await updateToken.mutateAsync(token.trim());
      setToken("");
      success(
        "Токен сохранён. Webhook применяется отдельно и отражается в диагностике.",
      );
    } catch (error) {
      fail(error, "Токен не сохранён");
    }
  }

  async function handleDeleteToken() {
    if (!canEdit) return;
    if (!deleteTokenArmed) {
      setDeleteTokenArmed(true);
      setReceipt("Удаление остановит доставку. Нажмите подтверждение ещё раз.");
      return;
    }
    try {
      await deleteToken.mutateAsync();
      setDeleteTokenArmed(false);
      success("Токен удалён, доставка Telegram остановлена");
    } catch (error) {
      fail(error, "Токен не удалён");
    }
  }

  async function handleSaveWebAppUrl() {
    if (!canEdit) return;
    if (!isSafeTelegramWebAppUrl(webAppUrl)) {
      setWebAppUrlError("Нужен корректный HTTPS URL без credentials");
      return;
    }
    setWebAppUrlError(null);
    try {
      await updateWebAppUrl.mutateAsync(webAppUrl.trim() || null);
      success(
        webAppUrl.trim() ? "Mini App URL сохранён" : "Mini App URL очищен",
      );
    } catch (error) {
      fail(error, "Mini App URL не сохранён");
    }
  }

  async function handleOwnerInvite() {
    if (!canEdit) return;
    try {
      const created = await createOwnerInvite.mutateAsync();
      setInvite(created);
      success("Одноразовое подключение владельца готово");
    } catch (error) {
      fail(error, "Подключение владельца не создано");
    }
  }

  async function handleRecipientInvite() {
    if (!canEdit) return;
    try {
      const created = await createRecipientInvite.mutateAsync();
      setInvite(created);
      success("Одноразовое подключение получателя готово");
    } catch (error) {
      fail(error, "Подключение получателя не создано");
    }
  }

  async function copyInvite() {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.activation_command);
      success("Команда подключения скопирована");
    } catch {
      setProblem("Не удалось скопировать. Выделите команду вручную.");
    }
  }

  async function handleRemoveRecipient(recipientId: string) {
    if (!canEdit) return;
    if (removeRecipientId !== recipientId) {
      setRemoveRecipientId(recipientId);
      setReceipt(
        "Нажмите «Подтвердить отзыв», чтобы остановить доставку этому получателю.",
      );
      return;
    }
    try {
      await deleteRecipient.mutateAsync(recipientId);
      setRemoveRecipientId(null);
      success("Доступ получателя отозван");
    } catch (error) {
      fail(error, "Доступ получателя не отозван");
    }
  }

  return (
    <div className="space-y-6 pb-4">
      {!canEdit ? (
        <p
          role="status"
          className="m-0 border-y border-[var(--color-hairline)] py-3 text-[14px] text-warning"
        >
          Менять Telegram-конфигурацию может только владелец.
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

      <section aria-labelledby="mini-telegram-status">
        <h3
          id="mini-telegram-status"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Доставка
        </h3>
        <div className="mt-3 border-y border-[var(--color-hairline)]">
          <StatusRow label="Токен">
            <Badge variant={settings.is_authorized ? "neutral" : "warning"}>
              {settings.is_authorized ? "Настроен" : "Не настроен"}
            </Badge>
          </StatusRow>
          <StatusRow label="Webhook">
            <Badge
              variant={
                diagnostics?.webhook_state === "configured"
                  ? "neutral"
                  : "warning"
              }
            >
              {diagnosticsQuery.isError
                ? "Недоступен"
                : diagnostics?.webhook_state === "configured"
                  ? "Настроен"
                  : diagnostics?.webhook_state === "failed"
                    ? "Ошибка"
                    : "Не подтверждён"}
            </Badge>
          </StatusRow>
          <StatusRow label="Gateway">
            <Badge
              variant={
                diagnostics?.gateway_state === "configured"
                  ? "neutral"
                  : "warning"
              }
            >
              {diagnosticsQuery.isError
                ? "Недоступен"
                : diagnostics?.gateway_state === "configured"
                  ? "Настроен"
                  : diagnostics?.gateway_state === "auth_error"
                    ? "Ошибка авторизации"
                    : "Не настроен"}
            </Badge>
          </StatusRow>
          <StatusRow label="Outbox" noBorder>
            <Badge
              variant={
                diagnostics?.outbox_state === "degraded"
                  ? "failed"
                  : diagnostics?.outbox_state === "active"
                    ? "warning"
                    : "neutral"
              }
            >
              {diagnosticsQuery.isError
                ? "Недоступен"
                : diagnostics?.outbox_state === "degraded"
                  ? `${failedOutbox ?? 0} ошибок`
                  : diagnostics?.outbox_state === "active"
                    ? `${activeOutbox ?? 0} в работе`
                    : diagnostics?.outbox_state === "idle"
                      ? "Очередь пуста"
                      : "Проверка"}
            </Badge>
          </StatusRow>
        </div>
      </section>

      <section aria-labelledby="mini-telegram-token">
        <h3
          id="mini-telegram-token"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Бот и Mini App
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          Секреты принимаются только в password-поле и после сохранения не
          показываются.
        </p>
        <div className="mt-3 space-y-4 border-y border-[var(--color-hairline)] py-4">
          <Input
            label="Новый Bot Token"
            type="password"
            value={token}
            disabled={!canEdit}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="new-password"
            spellCheck={false}
          />
          <Button
            fullWidth
            disabled={!canEdit || !token.trim()}
            loading={updateToken.isPending}
            onClick={() => void handleSaveToken()}
          >
            Сохранить токен
          </Button>
          {settings.is_authorized ? (
            <Button
              variant="danger"
              fullWidth
              disabled={!canEdit}
              loading={deleteToken.isPending}
              onClick={() => void handleDeleteToken()}
            >
              {deleteTokenArmed
                ? "Подтвердить удаление токена"
                : "Удалить токен"}
            </Button>
          ) : null}
          <Input
            label="Mini App HTTPS URL"
            value={webAppUrl}
            disabled={!canEdit}
            errorMessage={webAppUrlError ?? undefined}
            onChange={(event) => {
              setWebAppUrl(event.target.value);
              if (webAppUrlError) setWebAppUrlError(null);
            }}
            inputMode="url"
            autoComplete="url"
            spellCheck={false}
          />
          <Button
            variant="secondary"
            fullWidth
            disabled={!canEdit}
            loading={updateWebAppUrl.isPending}
            onClick={() => void handleSaveWebAppUrl()}
          >
            Сохранить Mini App URL
          </Button>
        </div>
      </section>

      <section aria-labelledby="mini-telegram-access">
        <h3
          id="mini-telegram-access"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Подключение
        </h3>
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Button
            variant="secondary"
            fullWidth
            disabled={!canEdit}
            loading={createOwnerInvite.isPending}
            onClick={() => void handleOwnerInvite()}
          >
            Подключить владельца
          </Button>
          <Button
            variant="secondary"
            fullWidth
            disabled={!canEdit}
            loading={createRecipientInvite.isPending}
            onClick={() => void handleRecipientInvite()}
          >
            <UserPlus size={15} aria-hidden="true" />
            Добавить получателя
          </Button>
        </div>
        {invite ? (
          <div className="mt-3 border-y border-[var(--color-hairline)] py-4">
            <p className="m-0 text-[13px] leading-5 text-bg-8">
              Одноразовая команда. Передайте её только нужному{" "}
              {invite.role === "owner" ? "владельцу" : "получателю"}.
            </p>
            <code className="mt-2 block break-all bg-bg-2 px-3 py-3 text-[13px] text-bg-11">
              {invite.activation_command}
            </code>
            <Button
              className="mt-3"
              variant="secondary"
              fullWidth
              onClick={() => void copyInvite()}
            >
              <Copy size={15} aria-hidden="true" />
              Скопировать команду
            </Button>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="mini-telegram-recipients">
        <h3
          id="mini-telegram-recipients"
          className="m-0 text-[15px] font-medium text-bg-11"
        >
          Получатели
        </h3>
        <p className="m-0 mt-1 text-[13px] leading-5 text-bg-8">
          Только владелец получает кнопки действий. Остальные сообщения
          read-only.
        </p>
        {recipientsQuery.isLoading ? (
          <div className="mt-3 space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : recipientsQuery.isError ? (
          <EmptyState
            title="Получатели недоступны"
            description={safeApiProblemMessage(
              recipientsQuery.error,
              "Не удалось получить список получателей",
            )}
            action={{
              label: "Повторить",
              onClick: () => void recipientsQuery.refetch(),
            }}
          />
        ) : recipientsQuery.data?.recipients.length ? (
          <div className="mt-3 border-y border-[var(--color-hairline)]">
            {recipientsQuery.data.recipients.map((recipient, index) => (
              <div
                key={recipient.id}
                className="border-b border-[var(--color-hairline)] py-3 last:border-b-0"
              >
                <div className="flex min-h-11 items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="break-words text-[14px] text-bg-11">
                      {recipientLabel(recipient, index)}
                    </div>
                    <div className="mt-0.5 text-[12px] text-bg-8">
                      {recipient.role === "owner"
                        ? "Владелец"
                        : "Только уведомления"}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedRecipient(recipient)}
                  >
                    Настроить
                  </Button>
                </div>
                {recipient.role !== "owner" ? (
                  <Button
                    className="mt-2"
                    variant={
                      removeRecipientId === recipient.id ? "danger" : "ghost"
                    }
                    size="sm"
                    fullWidth
                    disabled={!canEdit}
                    loading={
                      deleteRecipient.isPending &&
                      removeRecipientId === recipient.id
                    }
                    onClick={() => void handleRemoveRecipient(recipient.id)}
                  >
                    {removeRecipientId === recipient.id
                      ? "Подтвердить отзыв"
                      : "Отозвать доступ"}
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="m-0 mt-3 border-y border-[var(--color-hairline)] py-4 text-[14px] text-bg-9">
            Активных получателей нет.
          </p>
        )}
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
      className={`flex min-h-11 items-center justify-between gap-3 py-2.5 ${noBorder ? "" : "border-b border-[var(--color-hairline)]"}`}
    >
      <span className="text-[14px] text-bg-10">{label}</span>
      <span className="min-w-0 shrink-0">{children}</span>
    </div>
  );
}

function recipientLabel(recipient: TelegramRecipient, index: number): string {
  if (recipient.username) return `@${recipient.username.replace(/^@/, "")}`;
  return recipient.role === "owner"
    ? "Владелец Telegram"
    : `Получатель ${index + 1}`;
}
