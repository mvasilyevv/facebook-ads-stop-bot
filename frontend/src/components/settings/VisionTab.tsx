/**
 * VisionTab — настройки Vision anti-detect браузера:
 * x_token/cloud-креды (скрытые), profile_id, статус канала, кнопка Reconnect.
 */

import { useState, useEffect, type FC } from "react";
import { safeApiProblemMessage } from "@fb/operator-api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/ErrorState";
import { toast } from "@/components/ui/Toast";
import { useVisionSettings, useUpdateVisionSettings, useReconnectVision } from "@/lib/api/settings";

export const VisionTab: FC = () => {
  const { data, isLoading, error, refetch } = useVisionSettings();
  const updateMut = useUpdateVisionSettings();
  const reconnectMut = useReconnectVision();

  const [xToken, setXToken] = useState("");
  const [profileId, setProfileId] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [teamId, setTeamId] = useState("");
  const [folderId, setFolderId] = useState("");

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
    return (
      <ErrorState
        error={safeApiProblemMessage(error, "Настройки Vision временно недоступны")}
        onRetry={() => void refetch()}
      />
    );
  }

  const handleSave = async () => {
    const patch: {
      x_token?: string;
      profile_id?: string | null;
      username?: string;
      password?: string;
      team_id?: string;
      folder_id?: string;
    } = {
      profile_id: profileId.trim() || null,
    };
    if (xToken.trim()) patch.x_token = xToken.trim();
    if (username.trim()) patch.username = username.trim();
    if (password.trim()) patch.password = password.trim();
    if (teamId.trim()) patch.team_id = teamId.trim();
    if (folderId.trim()) patch.folder_id = folderId.trim();
    try {
      await updateMut.mutateAsync(patch);
      setXToken("");
      setUsername("");
      setPassword("");
      setTeamId("");
      setFolderId("");
      toast.success("Vision-настройки сохранены");
    } catch (e) {
      toast.error("Ошибка сохранения", safeApiProblemMessage(e, "Проверьте настройки Vision"));
    }
  };

  const handleReconnect = async () => {
    try {
      await reconnectMut.mutateAsync();
      toast.success("Команда Reconnect отправлена");
    } catch (e) {
      toast.error(
        "Ошибка Reconnect",
        safeApiProblemMessage(e, "Проверьте доступность browser channel"),
      );
    }
  };

  const channelStatus = data?.channel_status ?? "UNKNOWN";
  const channelVariant =
    channelStatus === "READY"
      ? ("success" as const)
      : channelStatus === "DEGRADED"
        ? ("warning" as const)
        : channelStatus === "UNAVAILABLE"
          ? ("stop" as const)
        : ("neutral" as const);
  const channelStatusLabel =
    channelStatus === "READY"
      ? "Канал готов"
      : channelStatus === "DEGRADED"
        ? "Канал деградирован"
        : channelStatus === "UNAVAILABLE"
          ? "Канал недоступен"
          : "Готовность не подтверждена";
  const tokenLabel = data?.has_token ? "Задан" : "Не задан";
  const credentialLabel = (value: boolean | undefined) => (value ? "Задан" : "Не задан");

  return (
    <div className="space-y-5 max-w-xl">
      {/* Статус */}
      <Card eyebrow="VISION · СТАТУС" padded>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Токен</span>
            <Badge variant={data?.has_token ? "success" : "neutral"} size="sm">
              {tokenLabel}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Browser channel</span>
            <Badge variant={channelVariant} size="sm">
              {channelStatusLabel}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Cloud-логин</span>
            <Badge variant={data?.has_cloud_username ? "success" : "neutral"} size="sm">
              {credentialLabel(data?.has_cloud_username)}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Cloud-пароль</span>
            <Badge variant={data?.has_cloud_password ? "success" : "neutral"} size="sm">
              {credentialLabel(data?.has_cloud_password)}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Team ID</span>
            <Badge variant={data?.has_team_id ? "success" : "neutral"} size="sm">
              {credentialLabel(data?.has_team_id)}
            </Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-bg-10">Folder ID</span>
            <Badge variant={data?.has_folder_id ? "success" : "neutral"} size="sm">
              {credentialLabel(data?.has_folder_id)}
            </Badge>
          </div>
          <div className="mt-1 text-[13px] leading-5 text-bg-8">
            <p className="m-0">
              {data?.channel_message ?? "Готовность канала Vision не подтверждена."}
            </p>
            {data?.channel_next_step ? (
              <p className="m-0 mt-1">Следующий шаг: {data.channel_next_step}</p>
            ) : null}
          </div>
        </div>

        {/* Reconnect */}
        <div className="mt-4 pt-4 border-t border-[var(--color-hairline)]">
          <Button
            variant="secondary"
            onClick={() => void handleReconnect()}
            loading={reconnectMut.isPending}
          >
            Переподключить Vision
          </Button>
        </div>
      </Card>

      {/* Токен, профиль и cloud-креды */}
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
            autoComplete="new-password"
            spellCheck={false}
          />
          <Input
            id="vision-profile"
            label="Profile ID"
            placeholder="Идентификатор профиля Vision"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <div className="border-t border-[var(--color-hairline)] pt-4">
            <p className="m-0 text-[13px] font-medium text-bg-10">Cloud-креды Vision</p>
            <p className="m-0 mt-1 text-[12px] leading-5 text-bg-8">
              Нужны для автоматического обновления токена. Сохранённые значения сервер не
              возвращает.
            </p>
          </div>
          <Input
            id="vision-cloud-username"
            label="Логин"
            placeholder="Логин Vision"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Input
            id="vision-cloud-password"
            label="Пароль"
            type="password"
            placeholder="Пароль Vision (оставьте пустым чтобы не менять)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="new-password"
            spellCheck={false}
          />
          <Input
            id="vision-team-id"
            label="Team ID"
            placeholder="Необязательно"
            value={teamId}
            onChange={(e) => setTeamId(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <Input
            id="vision-folder-id"
            label="Folder ID"
            placeholder="Необязательно"
            value={folderId}
            onChange={(e) => setFolderId(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="mt-4">
          <Button variant="primary" onClick={() => void handleSave()} loading={updateMut.isPending}>
            Сохранить
          </Button>
        </div>
      </Card>
    </div>
  );
};
