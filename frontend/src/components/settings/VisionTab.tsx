/**
 * VisionTab — настройки Vision anti-detect браузера:
 * x_token (скрытый), profile_id, статус CDP, кнопка Reconnect.
 */

import { useState, useEffect, type FC } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import {
  useVisionSettings,
  useUpdateVisionSettings,
  useReconnectVision,
} from "@/lib/api/settings";

export const VisionTab: FC = () => {
  const { data, isLoading, error, refetch } = useVisionSettings();
  const updateMut = useUpdateVisionSettings();
  const reconnectMut = useReconnectVision();

  const [xToken, setXToken] = useState("");
  const [profileId, setProfileId] = useState("");

  useEffect(() => {
    if (data) {
      setProfileId(data.profile_id ?? "");
    }
  }, [data]);

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

  const handleSave = async () => {
    const patch: { x_token?: string; profile_id?: string } = {};
    if (xToken.trim()) patch.x_token = xToken.trim();
    if (profileId.trim()) patch.profile_id = profileId.trim();
    if (!patch.x_token && !patch.profile_id) {
      toast.warning("Нечего сохранять");
      return;
    }
    try {
      await updateMut.mutateAsync(patch);
      setXToken("");
      toast.success("Vision-настройки сохранены");
    } catch (e) {
      toast.error("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    }
  };

  const handleReconnect = async () => {
    try {
      await reconnectMut.mutateAsync();
      toast.success("Команда Reconnect отправлена");
    } catch (e) {
      toast.error("Ошибка Reconnect", e instanceof Error ? e.message : String(e));
    }
  };

  const cdpStatus = data?.cdp_ready
    ? "READY"
    : data?.runtime_status ?? "OFFLINE";
  const cdpVariant = data?.cdp_ready
    ? ("success" as const)
    : ("neutral" as const);

  return (
    <div className="space-y-5 max-w-xl">
      {/* Статус */}
      <Card eyebrow="Vision Status" padded>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Токен</span>
            <Badge variant={data?.has_token ? "success" : "neutral"} size="sm">
              {data?.has_token ? "Задан" : "Не задан"}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">CDP</span>
            <Badge variant={cdpVariant} size="sm">
              {cdpStatus}
            </Badge>
          </div>
          {data?.cdp_port && (
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-bg-10">CDP Port</span>
              <span className="font-display text-[12px] text-bg-9 tabular-nums">
                {data.cdp_port}
              </span>
            </div>
          )}
          {data?.runtime_status_message && (
            <div className="text-[11px] text-bg-8 mt-1">
              {data.runtime_status_message}
            </div>
          )}
        </div>

        {/* Reconnect */}
        <div className="mt-4 pt-4 border-t border-bg-4">
          <Button
            variant="secondary"
            onClick={() => void handleReconnect()}
            loading={reconnectMut.isPending}
          >
            Reconnect Vision
          </Button>
        </div>
      </Card>

      {/* Токен + Profile ID */}
      <Card eyebrow="Конфигурация" padded>
        <div className="space-y-4">
          <Input
            id="vision-token"
            label="X-Token"
            type="password"
            placeholder="Новый токен Vision (оставьте пустым чтобы не менять)"
            value={xToken}
            onChange={(e) => setXToken(e.target.value)}
            helpText="Токен хранится зашифрованным через Fernet."
          />
          <Input
            id="vision-profile"
            label="Profile ID"
            placeholder="Идентификатор профиля Vision"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          />
        </div>
        <div className="mt-4">
          <Button
            variant="primary"
            onClick={() => void handleSave()}
            loading={updateMut.isPending}
          >
            Сохранить
          </Button>
        </div>
      </Card>
    </div>
  );
};
