import React, { useState } from "react";
import { getAIAnalysis } from "../../api.js";
import { renderMarkdown } from "../../utils/markdown.js";

/**
 * Карточка глобального AI брифинга для Telegram Mini App.
 * Neo Control Room мобильный дизайн.
 */
export default function AIBriefingCard({ clientData = null }) {
  const [loading, setLoading] = useState(false);
  const [content, setContent] = useState("");
  const [cachedAt, setCachedAt] = useState(null);
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState(null);

  const fetchBriefing = async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAIAnalysis("briefing", "global", force, clientData);
      setContent(data.content);
      setCachedAt(data.cached_at);
      setWarning(data.warning);
    } catch (err) {
      console.error(err);
      setError("Не удалось загрузить AI брифинг. Пожалуйста, попробуйте позже.");
    } finally {
      setLoading(false);
    }
  };


  // Форматирование времени
  const formatTime = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    return date.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  return (
    <div className="card" style={{ padding: "16px", marginBottom: "14px", borderLeft: "3px solid var(--ops-accent)" }}>
      {/* Шапка брифинга */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px", borderBottom: "1px solid var(--border)", paddingBottom: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span 
            className="pulse-led" 
            style={{ 
              display: "inline-block",
              width: "8px", 
              height: "8px", 
              borderRadius: "50%", 
              backgroundColor: "var(--ops-accent)",
              boxShadow: "0 0 8px var(--ops-accent)"
            }} 
          />
          <span style={{ fontFamily: "monospace", fontSize: "11px", fontWeight: "600", textTransform: "uppercase", letterSpacing: "1px", color: "var(--tg-text-color)" }}>
            Global AI Briefing
          </span>
        </div>

        <button
          onClick={() => fetchBriefing(true)}
          disabled={loading}
          className="btn btn-secondary btn-sm"
          style={{ 
            fontSize: "11px", 
            padding: "4px 8px", 
            minHeight: "28px", 
            margin: "0", 
            fontFamily: "monospace", 
            color: "var(--ops-accent)", 
            borderColor: "var(--ops-accent)" 
          }}
        >
          {loading ? "Анализ..." : "Сводка ✦"}
        </button>
      </div>

      {/* Оповещение о предупреждении (демо-режим / отсутствие API-ключа) */}
      {warning && (
        <div style={{ 
          marginBottom: "10px", 
          borderRadius: "6px", 
          border: "1px solid rgba(255, 176, 32, 0.3)", 
          backgroundColor: "rgba(255, 176, 32, 0.08)", 
          padding: "8px 10px", 
          fontSize: "11px", 
          color: "var(--color-warning)", 
          fontFamily: "monospace",
          lineHeight: "1.4"
        }}>
          ⚠️ {warning}
        </div>
      )}

      {/* Ошибки */}
      {error && (
        <div style={{ 
          marginBottom: "10px", 
          borderRadius: "6px", 
          border: "1px solid rgba(255, 59, 59, 0.3)", 
          backgroundColor: "rgba(255, 59, 59, 0.08)", 
          padding: "8px 10px", 
          fontSize: "11px", 
          color: "var(--color-danger)", 
          fontFamily: "monospace",
          lineHeight: "1.4"
        }}>
          {error}
        </div>
      )}

      {/* Содержимое сводки */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "24px 0", gap: "10px" }}>
          <div className="spinner" style={{ width: "24px", height: "24px" }} />
          <span style={{ fontSize: "11px", color: "var(--tg-hint-color)", fontFamily: "monospace" }}>
            Формирование сводки...
          </span>
        </div>
      ) : content ? (
        <div 
          style={{ 
            fontSize: "13px", 
            color: "var(--tg-text-color)", 
            lineHeight: "1.6", 
            whiteSpace: "pre-wrap", 
            fontFamily: "sans-serif" 
          }}
          dangerouslySetInnerHTML={{ __html: renderMarkdown(content, { theme: "tg" }) }}
        />
      ) : (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "16px 0", fontSize: "11px", color: "var(--tg-hint-color)", fontFamily: "monospace" }}>
          Нажмите кнопку «Сводка», чтобы получить AI сводку.
        </div>
      )}

      {/* Нижняя мета-информация */}
      {cachedAt && !loading && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "12px", borderTop: "1px solid var(--border)", paddingTop: "8px" }}>
          <span style={{ fontFamily: "monospace", fontSize: "9px", color: "var(--tg-hint-color)" }}>
            Актуально на: {formatTime(cachedAt)} (кэш 5м)
          </span>
        </div>
      )}
    </div>
  );
}
