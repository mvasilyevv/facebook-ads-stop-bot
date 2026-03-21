import { useEffect, useState, startTransition } from "react";
import { Badge } from "../components/Badge";
import { EmptyState } from "../components/EmptyState";
import { SectionCard } from "../components/SectionCard";
import { fetchSessions, startSession } from "../lib/api";
import { formatRelativeStatus } from "../lib/format";
import { getBadgeTone } from "../lib/helpers";
import type { BrowserSessionItem } from "../types";

export default function SessionsPage() {
  const [sessions, setSessions] = useState<BrowserSessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const data = await fetchSessions();
      startTransition(() => {
        setSessions(data);
        setLoading(false);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function runAction(action: () => Promise<unknown>, successMsg: string) {
    setMessage(null);
    try {
      await action();
      setMessage(successMsg);
      await reload(true);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Операция не выполнена");
    }
  }

  const visibleSessions = sessions.filter((session) => {
    const text = `${session.profile_id} ${session.browser_host_id} ${session.status}`.toLowerCase();
    return text.includes(search.toLowerCase());
  });

  if (loading) {
    return <div className="page-loading">Загрузка сессий...</div>;
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Сессии browser host</h1>
          <p className="page-subtitle">Активные профили и состояние автоматизации</p>
        </div>
      </div>

      {message && <div className="message-banner">{message}</div>}
      {error && <div className="inline-error">{error}</div>}

      <SectionCard
        title="Сессии browser host"
        subtitle="Активные профили и состояние автоматизации"
        actions={
          <input
            className="input input--compact"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Поиск по profile ID, host ID или статусу"
          />
        }
      >
        <div className="table-wrap" id="sessions">
          <table className="data-table">
            <thead>
              <tr>
                <th>Профиль</th>
                <th>Хост</th>
                <th>Статус</th>
                <th>Attach</th>
                <th>Последнее сообщение</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {visibleSessions.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <EmptyState title="Сессий нет" description="После запуска browser host они появятся здесь." />
                  </td>
                </tr>
              ) : (
                visibleSessions.map((session) => (
                  <tr key={session.profile_id}>
                    <td className="mono">{session.profile_id}</td>
                    <td className="mono">{session.browser_host_id}</td>
                    <td>
                      <Badge tone={getBadgeTone(session.status)}>{formatRelativeStatus(session.status)}</Badge>
                    </td>
                    <td>
                      <div className="stack stack--tight">
                        <span>{session.cdp_url ? "CDP готов" : "CDP нет"}</span>
                        <span>{session.webdriver_url ? "WebDriver готов" : "WebDriver нет"}</span>
                      </div>
                    </td>
                    <td>{session.last_message ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="button button--small"
                        disabled={session.status.toUpperCase() !== "ACTIVE"}
                        onClick={() =>
                          void runAction(
                            () =>
                              startSession({
                                profileId: session.profile_id,
                                browserHostId: session.browser_host_id,
                                reason: "Перезапуск с автоматизацией",
                              }),
                            `Профиль ${session.profile_id} перезапущен с CDP`,
                          )
                        }
                      >
                        Перезапустить с CDP
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </SectionCard>
    </>
  );
}
