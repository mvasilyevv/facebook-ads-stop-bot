import React, { useEffect, useState, useCallback } from "react";
import { fetchJson } from "../api.js";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Card from "../components/Card.jsx";
import { haptic } from "../theme.js";

// Страница скриптов — просмотр сгенерированных скриптов кампаний
export default function ScriptsPage() {
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [genResult, setGenResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJson("/scripts/list").catch(() => []);
      setScripts(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleGenerate = async () => {
    setGenerating(true);
    setGenResult(null);
    haptic.impact("medium");
    try {
      const res = await fetchJson("/scripts/generate", { method: "POST", body: JSON.stringify({}) });
      haptic.notify("success");
      setGenResult({ type: "ok", text: res?.message || "Генерация запущена" });
      // Обновляем список через 1.5с
      setTimeout(load, 1500);
    } catch (err) {
      haptic.notify("error");
      setGenResult({ type: "err", text: err.message });
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div>
      <h1>Скрипты</h1>

      {/* Действия */}
      <Card title="Генерация скриптов">
        <p className="hint" style={{ marginBottom: 10 }}>
          Создание именованных скриптов кампаний по активным офферам.
        </p>
        {genResult && (
          <p
            className={genResult.type === "ok" ? "status-ok" : "status-error"}
            style={{ marginBottom: 8, fontSize: 13 }}
          >
            {genResult.text}
          </p>
        )}
        <button className="btn" onClick={handleGenerate} disabled={generating}>
          {generating ? "Генерирую..." : "⚡ Генерировать скрипты"}
        </button>
      </Card>

      {/* Список скриптов */}
      {loading && <Loader text="Загрузка скриптов..." />}
      {error && <ErrorBox message={error} onRetry={load} />}

      {!loading && !error && scripts.length === 0 && (
        <Card>
          <EmptyState
            icon="📝"
            title="Скриптов нет"
            subtitle="Нажмите «Генерировать скрипты» для создания"
          />
        </Card>
      )}

      {scripts.map((s, i) => (
        <Card key={s.id ?? s.name ?? i}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: "monospace", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                {s.name ?? s.offer_code ?? `Скрипт ${i + 1}`}
              </div>
              {s.created_at && (
                <div className="hint">
                  {new Date(s.created_at).toLocaleDateString("ru-RU", {
                    day: "2-digit",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
              )}
              {s.status && (
                <span className={s.status === "done" ? "status-ok" : "hint"} style={{ fontSize: 12 }}>
                  {s.status}
                </span>
              )}
            </div>
            {s.url && (
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-secondary btn-sm"
                style={{ flexShrink: 0 }}
              >
                Открыть
              </a>
            )}
          </div>
          {s.content && (
            <pre
              style={{
                marginTop: 10,
                fontSize: 11,
                background: "rgba(255,255,255,0.04)",
                borderRadius: 6,
                padding: "8px 10px",
                overflow: "auto",
                maxHeight: 140,
                color: "var(--tg-text-color)",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {s.content}
            </pre>
          )}
        </Card>
      ))}

      {/* Ссылка на полный интерфейс */}
      <Card title="Полные инструменты">
        <p className="hint" style={{ marginBottom: 8 }}>
          Уникализация креативов и создание кампаний из папки — только в веб-версии.
        </p>
        <a
          href={`${window.location.origin}/scripts`}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-secondary"
          style={{ textDecoration: "none", textAlign: "center" }}
        >
          🖥️ Открыть в браузере
        </a>
      </Card>
    </div>
  );
}
