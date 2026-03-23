import { useState } from "react";
import type { ProfileItem, ProfileLaunchItem } from "../types";

type OperatorScopeBarProps = {
  profiles: ProfileItem[];
  launches: ProfileLaunchItem[];
  selectedProfileId: string | null;
  selectedLaunchId: string | null;
  loading: boolean;
  error: string | null;
  onSelectProfile: (profileId: string | null) => void;
  onSelectLaunch: (launchId: string | null) => void;
  onRenameLaunch: (launchId: string, name: string) => Promise<void>;
};

export function OperatorScopeBar({
  profiles,
  launches,
  selectedProfileId,
  selectedLaunchId,
  loading,
  error,
  onSelectProfile,
  onSelectLaunch,
  onRenameLaunch,
}: OperatorScopeBarProps) {
  const [renaming, setRenaming] = useState(false);
  const selectedLaunch = launches.find((item) => item.id === selectedLaunchId) ?? null;

  async function handleRename() {
    if (!selectedLaunch) {
      return;
    }
    const nextName = window.prompt("Введите новое название запуска", selectedLaunch.name)?.trim();
    if (!nextName || nextName === selectedLaunch.name) {
      return;
    }
    setRenaming(true);
    try {
      await onRenameLaunch(selectedLaunch.id, nextName);
    } finally {
      setRenaming(false);
    }
  }

  return (
    <div className="scope-bar">
      <div className="scope-bar__group">
        <label className="scope-bar__field">
          <span>Профиль</span>
          <select
            value={selectedProfileId ?? ""}
            onChange={(event) => onSelectProfile(event.target.value || null)}
            disabled={loading || profiles.length === 0}
          >
            {profiles.length > 1 ? <option value="">Выберите профиль</option> : null}
            {profiles.length === 0 ? <option value="">Профили не найдены</option> : null}
            {profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.display_name} · {profile.browser_host_id}
              </option>
            ))}
          </select>
        </label>

        <label className="scope-bar__field">
          <span>Запуск</span>
          <select
            value={selectedLaunchId ?? ""}
            onChange={(event) => onSelectLaunch(event.target.value || null)}
            disabled={loading || launches.length === 0}
          >
            {launches.length === 0 ? <option value="">Запуски не найдены</option> : null}
            {launches.map((launch) => (
              <option key={launch.id} value={launch.id}>
                {launch.is_active ? "Текущий" : "Архив"} · {launch.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="scope-bar__actions">
        {selectedLaunch && !selectedLaunch.is_active ? (
          <span className="scope-bar__badge">архивный запуск · только просмотр</span>
        ) : null}
        <button
          type="button"
          className="button button--ghost button--small"
          onClick={() => void handleRename()}
          disabled={!selectedLaunch || !selectedLaunch.is_active || renaming}
        >
          Переименовать запуск
        </button>
      </div>

      {error ? <div className="scope-bar__error">{error}</div> : null}
    </div>
  );
}
