import {
  createContext,
  startTransition,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  createProfileLaunch,
  fetchProfileLaunches,
  fetchProfiles,
  renameProfileLaunch,
} from "../lib/api";
import type {
  ProfileItem,
  ProfileLaunchActionResponse,
  ProfileLaunchItem,
} from "../types";

type OperatorScopeContextValue = {
  profiles: ProfileItem[];
  launches: ProfileLaunchItem[];
  selectedProfileId: string | null;
  selectedProfile: ProfileItem | null;
  selectedLaunchId: string | null;
  selectedLaunch: ProfileLaunchItem | null;
  loading: boolean;
  error: string | null;
  selectProfile: (profileId: string | null) => void;
  selectLaunch: (launchId: string | null) => void;
  refreshScope: () => Promise<void>;
  createLaunch: (name?: string | null) => Promise<ProfileLaunchActionResponse>;
  renameLaunch: (launchId: string, name: string) => Promise<ProfileLaunchActionResponse>;
};

const STORAGE_PROFILE_KEY = "fb_agent_selected_profile_id";
const STORAGE_LAUNCH_KEY = "fb_agent_selected_launch_id";

const OperatorScopeContext = createContext<OperatorScopeContextValue | null>(null);

function readStoredValue(key: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  const storage = window.localStorage as Storage | undefined;
  if (!storage || typeof storage.getItem !== "function") {
    return null;
  }
  return storage.getItem(key);
}

function writeStoredValue(key: string, value: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  const storage = window.localStorage as Storage | undefined;
  if (!storage || typeof storage.setItem !== "function" || typeof storage.removeItem !== "function") {
    return;
  }
  if (value) {
    storage.setItem(key, value);
  } else {
    storage.removeItem(key);
  }
}

function pickInitialProfile(
  profiles: ProfileItem[],
  storedProfileId: string | null,
): string | null {
  if (storedProfileId && profiles.some((item) => item.profile_id === storedProfileId)) {
    return storedProfileId;
  }
  if (profiles.length === 1) {
    return profiles[0]?.profile_id ?? null;
  }
  return null;
}

function pickInitialLaunch(
  launches: ProfileLaunchItem[],
  storedLaunchId: string | null,
): string | null {
  if (storedLaunchId && launches.some((item) => item.id === storedLaunchId)) {
    return storedLaunchId;
  }
  return launches.find((item) => item.is_active)?.id ?? launches[0]?.id ?? null;
}

export function OperatorScopeProvider({ children }: { children: ReactNode }) {
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [launches, setLaunches] = useState<ProfileLaunchItem[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(() =>
    readStoredValue(STORAGE_PROFILE_KEY),
  );
  const [selectedLaunchId, setSelectedLaunchId] = useState<string | null>(() =>
    readStoredValue(STORAGE_LAUNCH_KEY),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadProfiles() {
    const profileItems = await fetchProfiles();
    const nextProfileId = pickInitialProfile(
      profileItems,
      readStoredValue(STORAGE_PROFILE_KEY),
    );
    startTransition(() => {
      setProfiles(profileItems);
      setSelectedProfileId(nextProfileId);
    });
  }

  async function refreshScope() {
    setError(null);
    setLoading(true);
    try {
      await loadProfiles();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить рабочий контекст");
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshScope();
  }, []);

  useEffect(() => {
    if (!selectedProfileId) {
      startTransition(() => {
        setLaunches([]);
        setSelectedLaunchId(null);
        setLoading(false);
      });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchProfileLaunches(selectedProfileId)
      .then((launchItems) => {
        if (cancelled) {
          return;
        }
        const nextLaunchId = pickInitialLaunch(
          launchItems,
          readStoredValue(STORAGE_LAUNCH_KEY),
        );
        startTransition(() => {
          setLaunches(launchItems);
          setSelectedLaunchId(nextLaunchId);
          setLoading(false);
        });
      })
      .catch((err) => {
        if (cancelled) {
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось загрузить запуски профиля");
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedProfileId]);

  useEffect(() => {
    writeStoredValue(STORAGE_PROFILE_KEY, selectedProfileId);
  }, [selectedProfileId]);

  useEffect(() => {
    writeStoredValue(STORAGE_LAUNCH_KEY, selectedLaunchId);
  }, [selectedLaunchId]);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.profile_id === selectedProfileId) ?? null,
    [profiles, selectedProfileId],
  );
  const selectedLaunch = useMemo(
    () => launches.find((item) => item.id === selectedLaunchId) ?? null,
    [launches, selectedLaunchId],
  );

  async function handleCreateLaunch(name?: string | null) {
    if (!selectedProfileId) {
      throw new Error("Сначала выберите профиль");
    }
    const response = await createProfileLaunch({ profileId: selectedProfileId, name });
    const nextLaunches = await fetchProfileLaunches(selectedProfileId);
    startTransition(() => {
      setLaunches(nextLaunches);
      setSelectedLaunchId(response.launch.id);
    });
    return response;
  }

  async function handleRenameLaunch(launchId: string, name: string) {
    const response = await renameProfileLaunch(launchId, name);
    if (selectedProfileId) {
      const nextLaunches = await fetchProfileLaunches(selectedProfileId);
      startTransition(() => {
        setLaunches(nextLaunches);
      });
    }
    return response;
  }

  const value = useMemo<OperatorScopeContextValue>(
    () => ({
      profiles,
      launches,
      selectedProfileId,
      selectedProfile,
      selectedLaunchId,
      selectedLaunch,
      loading,
      error,
      selectProfile: setSelectedProfileId,
      selectLaunch: setSelectedLaunchId,
      refreshScope,
      createLaunch: handleCreateLaunch,
      renameLaunch: handleRenameLaunch,
    }),
    [
      profiles,
      launches,
      selectedProfileId,
      selectedProfile,
      selectedLaunchId,
      selectedLaunch,
      loading,
      error,
    ],
  );

  return <OperatorScopeContext.Provider value={value}>{children}</OperatorScopeContext.Provider>;
}

export function useOperatorScope(): OperatorScopeContextValue | null {
  return useContext(OperatorScopeContext);
}
