/**
 * TelegramTab — настройки Telegram-бота:
 * токен, статус авторизации, deep-link, web-app-url.
 */

import { useState, type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useTelegramSettings,
  useUpdateTelegramToken,
  useDeleteTelegramToken,
} from "@/lib/api/settings";
import { CheckCircle2, XCircle } from "lucide-react";

export const TelegramTab: FC = () => {
  const { data, isLoading, error, refetch } = useTelegramSettings();
  const tokenMut = useUpdateTelegramToken();
  const deleteMut = useDeleteTelegramToken();

  const [newToken, setNewToken] = useState("");

  if (isLoading) {
    return (
      <div className="space-y-3 max-w-xl">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  const handleSaveToken = async () => {
    if (!newToken.trim()) return;
    try {
      await tokenMut.mutateAsync(newToken.trim());
      setNewToken("");
      toast.success("Токен сохранён");
    } catch (e) {
      toast.error("Ошибка сохранения токена", e instanceof Error ? e.message : String(e));
    }
  };

  const handleDeleteToken = async () => {
    try {
      await deleteMut.mutateAsync();
      toast.success("Токен удалён");
    } catch (e) {
      toast.error("Ошибка удаления токена", e instanceof Error ? e.message : String(e));
    }
  };

  const isAuthorized = data?.is_authorized ?? false;
  const pollerOnline = data?.poller_status === "ONLINE";

  return (
    <div className="space-y-5 max-w-xl">
      {/* Статус */}
      <Card eyebrow="Статус бота" padded>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Авторизация</span>
            {isAuthorized ? (
              <Badge variant="success" size="sm">
                <CheckCircle2 size={10} aria-hidden="true" />
                Авторизован
              </Badge>
            ) : (
              <Badge variant="neutral" size="sm">
                <XCircle size={10} aria-hidden="true" />
                Не авторизован
              </Badge>
            )}
          </div>

          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Poller</span>
            <Badge variant={pollerOnline ? "success" : "neutral"} size="sm">
              {data?.poller_status ?? "OFFLINE"}
            </Badge>
          </div>

          {data?.bot_username && (
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-bg-10">Бот</span>
              <span className="font-display text-[12px] text-bg-9">
                @{data.bot_username}
              </span>
            </div>
          )}
        </div>

        {/* Deep link */}
        {data?.auth_deep_link && (
          <div className="mt-4 pt-4 border-t border-[var(--hairline)]">
            <div className="text-[11px] text-bg-8 uppercase tracking-wider mb-2">
              Ссылка авторизации
            </div>
            <div className="font-display text-[12px] text-accent break-all">
              {data.auth_deep_link}
            </div>
            <div className="text-[11px] text-bg-9 mt-1">
              Команда: <code className="text-accent">{data.activation_command}</code>
            </div>
          </div>
        )}
      </Card>

      {/* Токен */}
      <Card eyebrow="Токен бота" padded>
        <Input
          id="tg-token"
          label="Bot Token"
          placeholder="1234567890:ABC..."
          type="password"
          value={newToken}
          onChange={(e) => setNewToken(e.target.value)}
          helpText="Telegram Bot API токен от @BotFather. Хранится зашифрованным."
        />
        <div className="mt-4 flex gap-3">
          <Button
            variant="primary"
            onClick={() => void handleSaveToken()}
            loading={tokenMut.isPending}
            disabled={!newToken.trim()}
          >
            Сохранить токен
          </Button>
          {isAuthorized && (
            <Button
              variant="danger"
              onClick={() => void handleDeleteToken()}
              loading={deleteMut.isPending}
            >
              Удалить токен
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
};
