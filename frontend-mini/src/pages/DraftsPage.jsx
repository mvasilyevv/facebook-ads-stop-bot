import React, { useCallback, useEffect, useState } from "react";
import { listDraftTasks, confirmDraftTask, rejectDraftTask, getDraftTask } from "../api.js";
import Card from "../components/Card.jsx";
import Loader from "../components/Loader.jsx";
import ErrorBox from "../components/ErrorBox.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { haptic } from "../theme.js";

// Ключи — реальные mutation_kind из core/meta_api/schemas.py (MUTATION_KINDS).
const KIND_LABELS = {
  pause_ad: "Пауза объявления",
  activate_ad: "Включить объявление",
  pause_campaign: "Пауза кампании",
  activate_campaign: "Включить кампанию",
  set_adset_budget: "Бюджет адсета",
  duplicate_campaign: "Клон кампании",
  bulk_status_change: "Массовая смена статуса",
  create_campaign: "Новая кампания",
  custom_audience: "Аудитория",
  set_ad_creative: "Замена креатива",
};

function formatPayload(kind, payload) {
  // Краткое описание payload — для пользователя, без технических полей
  if (!payload) return null;
  // Бюджет приходит в ЦЕНТАХ (params.daily_budget / lifetime_budget — int).
  if (kind === "set_adset_budget") {
    const cents = payload.daily_budget ?? payload.lifetime_budget ?? 0;
    const type = payload.daily_budget != null ? "дневной" : "lifetime";
    return (
      <>
        <div><b>Тип:</b> {type} бюджет</div>
        <div><b>Новый бюджет:</b> ${(Number(cents) / 100).toFixed(2)}</div>
        {payload.end_time && <div><b>До:</b> {payload.end_time}</div>}
        {payload.reason && <div><b>Причина:</b> {payload.reason}</div>}
      </>
    );
  }
  if (kind === "duplicate_campaign") {
    return (
      <>
        <div><b>Глубина:</b> {payload.deep_copy ? "deep (кампания + адсеты + объявления)" : "shallow"}</div>
        {payload.new_name && <div><b>Имя клона:</b> {payload.new_name}</div>}
        {payload.status_after_clone && <div><b>Статус после:</b> {payload.status_after_clone}</div>}
        {payload.reason && <div><b>Причина:</b> {payload.reason}</div>}
      </>
    );
  }
  if (kind === "bulk_status_change") {
    // Поддерживаем обе формы payload: {ad_ids, action} и {object_ids, status}.
    const ids = payload.ad_ids || payload.object_ids || [];
    const action = payload.action || payload.status || "";
    const preview = ids.slice(0, 5).join(", ");
    return (
      <>
        <div><b>Действие:</b> {action}</div>
        <div><b>Объектов:</b> {ids.length}</div>
        {preview && <div className="hint" style={{ fontSize: 12 }}>{preview}{ids.length > 5 ? `, +${ids.length - 5}` : ""}</div>}
        {payload.reason && <div><b>Причина:</b> {payload.reason}</div>}
      </>
    );
  }
  if (kind === "create_campaign") {
    // Бюджет в params.daily_budget / lifetime_budget — ЦЕНТЫ (int).
    const cents = payload.daily_budget ?? payload.lifetime_budget ?? 0;
    return (
      <>
        {payload.name && <div><b>Имя:</b> {payload.name}</div>}
        {payload.objective && <div><b>Цель:</b> {payload.objective}</div>}
        {Number(cents) > 0 && <div><b>Бюджет:</b> ${(Number(cents) / 100).toFixed(2)}</div>}
        {payload.reason && <div><b>Причина:</b> {payload.reason}</div>}
      </>
    );
  }
  return <pre style={{ fontSize: 12 }}>{JSON.stringify(payload, null, 2)}</pre>;
}

function DraftCard({ task, onConfirm, onReject }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const handleConfirm = async () => {
    setBusy(true);
    setError(null);
    haptic.impact("medium");
    try {
      await onConfirm(task.id);
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      setError(err.message);
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!window.confirm("Отменить эту задачу?")) return;
    setBusy(true);
    setError(null);
    haptic.impact("light");
    try {
      await onReject(task.id);
      haptic.notify("success");
    } catch (err) {
      haptic.notify("error");
      setError(err.message);
      setBusy(false);
    }
  };

  const created = new Date(task.created_at);
  const createdStr = created.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontWeight: 600 }}>
          {KIND_LABELS[task.mutation_kind] ?? task.mutation_kind}
        </div>
        <div className="hint" style={{ fontSize: 12 }}>
          {createdStr}
        </div>
      </div>

      <div className="hint" style={{ fontSize: 12, marginBottom: 8 }}>
        Кабинет: <code>{task.ad_account_id}</code>
        {task.target_id && <> · Объект: <code>{task.target_id}</code></>}
      </div>

      <div style={{ marginBottom: 12 }}>
        {formatPayload(task.mutation_kind, task.payload)}
      </div>

      <div className="hint" style={{ fontSize: 11, marginBottom: 12 }}>
        Запросил: <code>{task.requested_by}</code>
      </div>

      {error && (
        <p className="status-error" style={{ fontSize: 13, marginBottom: 8 }}>
          {error}
        </p>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" onClick={handleConfirm} disabled={busy}>
          {busy ? "..." : "✅ Подтвердить"}
        </button>
        <button className="btn btn-secondary" onClick={handleReject} disabled={busy}>
          ❌ Отменить
        </button>
      </div>
    </Card>
  );
}

export default function DraftsPage() {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const summaries = await listDraftTasks({ status: "DRAFT", limit: 50 });
      // Подгружаем детали (payload) для каждой задачи параллельно
      const details = await Promise.all(summaries.map((s) => getDraftTask(s.id)));
      setTasks(details);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleConfirm = useCallback(
    async (taskId) => {
      await confirmDraftTask(taskId);
      // Перезагружаем список — задача уйдёт из DRAFT
      await load();
    },
    [load],
  );

  const handleReject = useCallback(
    async (taskId) => {
      await rejectDraftTask(taskId, null);
      await load();
    },
    [load],
  );

  if (loading) return <Loader />;
  if (error) return <ErrorBox message={error} onRetry={load} />;

  if (tasks.length === 0) {
    return (
      <div>
        <h1>Черновики задач</h1>
        <EmptyState
          icon="🗒"
          title="Нет черновиков"
          message="Создайте черновик через /clone, /budget или /pause_offer в Telegram-боте."
        />
      </div>
    );
  }

  return (
    <div>
      <h1>Черновики задач ({tasks.length})</h1>
      <p className="hint" style={{ marginBottom: 16, fontSize: 13 }}>
        Подтвердите задачу — она пойдёт в очередь на исполнение через Marketing API.
        Отмена переводит задачу в статус CANCELLED.
      </p>

      {tasks.map((task) => (
        <DraftCard
          key={task.id}
          task={task}
          onConfirm={handleConfirm}
          onReject={handleReject}
        />
      ))}

      <button className="btn btn-secondary" onClick={load} style={{ marginTop: 8 }}>
        Обновить
      </button>
    </div>
  );
}
