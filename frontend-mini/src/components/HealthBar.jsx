import React, { useState, useEffect, useRef } from "react";
import { getHealthDetails } from "../api.js";

// Порядок отображения воркеров
const WORKER_ORDER = [
  "observer",
  "telegram_poller",
  "disable",
  "enable",
  "health_watchdog",
];

// Краткие метки для компактного отображения
const WORKER_SHORT = {
  observer: "OBS",
  telegram_poller: "TG",
  disable: "DIS",
  enable: "ENA",
  enable_recommendation: "AUTO",
  health_watchdog: "WD",
};

// Человекочитаемые имена для тултипов
const WORKER_LABELS = {
  observer: "Observer",
  telegram_poller: "Telegram Poller",
  disable: "Disable Worker",
  enable: "Enable Worker",
  enable_recommendation: "AutoEnable",
  health_watchdog: "Health Watchdog",
};

function formatAge(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  if (s < 60) return `${s}с`;
  return `${Math.floor(s / 60)}м ${s % 60}с`;
}

function formatTime(isoStr) {
  if (!isoStr) return "—";
  try {
    return new Date(isoStr).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

// Компонент одного точечного индикатора воркера
function WorkerDot({ name, worker }) {
  const label = WORKER_LABELS[name] ?? name;
  const ageStr = worker?.heartbeat_age_seconds != null ? formatAge(worker.heartbeat_age_seconds) : "";
  const tooltip = worker?.healthy
    ? `Воркер ${label}: работает${ageStr ? " · " + ageStr + " назад" : ""}`
    : `Воркер ${label}: не отвечает${ageStr ? " · " + ageStr + " назад" : ""}`;

  let dotBg = "var(--tg-hint-color)";
  if (worker?.healthy === true) dotBg = "var(--color-success)";
  if (worker?.healthy === false) dotBg = "var(--color-danger)";

  return (
    <span
      title={tooltip}
      aria-label={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 10,
        fontFamily: "monospace",
        color: "var(--tg-hint-color)",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: dotBg,
          flexShrink: 0,
          display: "inline-block",
        }}
      />
      {WORKER_SHORT[name] ?? name}
    </span>
  );
}

// Компонент для внешнего сервиса
function ServiceDot({ label, healthy, error }) {
  const tooltip = healthy
    ? `${label}: работает`
    : `${label}: недоступен${error ? " — " + error : ""}`;
  const dotBg = healthy ? "var(--color-success)" : "var(--color-danger)";

  return (
    <span
      title={tooltip}
      aria-label={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 3,
        fontSize: 10,
        fontFamily: "monospace",
        color: "var(--tg-hint-color)",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: dotBg,
          flexShrink: 0,
          display: "inline-block",
        }}
      />
      {label}
    </span>
  );
}

// Полоска здоровья системы — компактная для Telegram Mini App
export default function HealthBar() {
  const [data, setData] = useState(null);
  const [unavailable, setUnavailable] = useState(false);
  const abortRef = useRef(null);
  // Локальный тикер для плавного отсчёта «N с назад» без запросов к API
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const fetchHealth = () => {
    // Отменяем предыдущий запрос если ещё выполняется
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    getHealthDetails()
      .then((result) => {
        setData(result);
        setUnavailable(false);
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        setUnavailable(true);
      })
      .finally(() => {
        abortRef.current = null;
      });
  };

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, 10_000);
    return () => {
      clearInterval(id);
      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const barStyle = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    height: 28,
    padding: "0 12px",
    background: "var(--tg-secondary-bg-color)",
    borderBottom: "1px solid var(--border)",
    overflowX: "auto",
    scrollbarWidth: "none",
    msOverflowStyle: "none",
    fontSize: 10,
    fontFamily: "monospace",
    color: "var(--tg-hint-color)",
    flexShrink: 0,
  };

  if (unavailable && !data) {
    return (
      <div style={barStyle} role="status" aria-label="Статус системы недоступен">
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--tg-hint-color)",
            display: "inline-block",
          }}
        />
        <span>health: недоступно</span>
      </div>
    );
  }

  if (!data) {
    return (
      <div style={barStyle} role="status" aria-label="Загрузка статуса системы">
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--tg-hint-color)",
            display: "inline-block",
          }}
        />
        <span>загрузка…</span>
      </div>
    );
  }

  const workers = data.workers ?? {};
  const queues = data.queues ?? {};
  const lastScan = data.last_successful_scan ?? {};
  const disablePending = (queues.disable_pending ?? 0) + (queues.disable_running ?? 0);

  return (
    <div style={barStyle} role="status" aria-label="Статус системы">
      {/* Воркеры */}
      {WORKER_ORDER.map((key) => {
        const w = workers[key];
        if (w == null) return null;
        return <WorkerDot key={key} name={key} worker={w} />;
      })}

      {/* Разделитель */}
      <span style={{ color: "rgba(255,255,255,0.15)", flexShrink: 0 }}>|</span>

      {/* Внешние сервисы */}
      <ServiceDot
        label="VIS"
        healthy={data.vision?.healthy ?? false}
        error={data.vision?.error}
      />
      <ServiceDot
        label="BR"
        healthy={data.browser_agent?.healthy ?? false}
        error={data.browser_agent?.error}
      />

      {/* Очередь отключений (только если есть) */}
      {disablePending > 0 && (
        <span
          title={`Очередь отключений: ${disablePending}`}
          style={{ color: "var(--color-warning)", flexShrink: 0 }}
        >
          ↓{disablePending}
        </span>
      )}

      {/* Пульс сканирования: вычисляем возраст по локальному тикеру, а не из серверного age_seconds.
          Устраняет скачки — значение монотонно растёт между refetch'ами. */}
      <span style={{ marginLeft: "auto", flexShrink: 0, whiteSpace: "nowrap" }}>
        {lastScan.at
          ? `скан: ${formatAge(Math.floor((now - new Date(lastScan.at).getTime()) / 1000))} назад`
          : "скан: —"}
      </span>
    </div>
  );
}
