import { useEffect, useRef, useState } from "react";
import { askAI } from "../api.js";

const STORAGE_KEY = "tma_ai_chat_history_v1";

function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(messages) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-30)));
  } catch {
    /* ignore */
  }
}

export default function ChatPage() {
  const [messages, setMessages] = useState(loadHistory());
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    saveHistory(messages);
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;
    const userMsg = { role: "user", content: text };
    const next = [...messages, userMsg];
    setMessages(next);
    setInput("");
    setError(null);
    setLoading(true);
    try {
      const result = await askAI(
        next.map((m) => ({ role: m.role, content: m.content })),
        true,
      );
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.answer || "(пустой ответ)",
          tool_calls: result.tool_calls || [],
        },
      ]);
    } catch (e) {
      setError(e.message || "Ошибка запроса");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="chat-page">
      <div className="chat-header">
        <h2>🤖 AI-помощник</h2>
        <button
          className="btn-secondary"
          onClick={() => setMessages([])}
          disabled={loading}
        >
          Очистить
        </button>
      </div>

      <div className="chat-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-empty">
            Спроси что-нибудь — я могу прочитать логи, проверить состояние
            воркеров, перезапустить процесс.
          </div>
        )}
        {messages.map((msg, idx) => (
          <div key={idx} className={`chat-msg chat-msg-${msg.role}`}>
            <div className="chat-msg-content">{msg.content}</div>
            {msg.tool_calls && msg.tool_calls.length > 0 && (
              <details className="chat-tool-calls">
                <summary>
                  Инструменты: {msg.tool_calls.length}
                </summary>
                {msg.tool_calls.map((tc, i) => (
                  <div key={i} className="chat-tool">
                    <code>
                      {tc.name}({JSON.stringify(tc.args)})
                    </code>
                    {tc.error ? (
                      <div className="chat-tool-error">{tc.error}</div>
                    ) : (
                      <pre className="chat-tool-result">
                        {(tc.result || "").slice(0, 600)}
                      </pre>
                    )}
                  </div>
                ))}
              </details>
            )}
          </div>
        ))}
        {loading && <div className="chat-msg chat-msg-assistant">думаю…</div>}
        {error && <div className="chat-error">{error}</div>}
      </div>

      <div className="chat-input">
        <textarea
          rows={2}
          placeholder="Сообщение…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button
          className="btn-primary"
          onClick={send}
          disabled={loading || !input.trim()}
        >
          {loading ? "…" : "→"}
        </button>
      </div>
    </div>
  );
}
