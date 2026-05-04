import { useState, useRef, useEffect } from 'react';
import { askAI } from '../api.js';

const STORAGE_KEY = 'ai_chat_history_v1';

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
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [allowTools, setAllowTools] = useState(true);
  const scrollRef = useRef(null);

  useEffect(() => {
    saveHistory(messages);
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    const userMsg = { role: 'user', content: text };
    const next = [...messages, userMsg];
    setMessages(next);
    setInput('');
    setError(null);
    setLoading(true);
    try {
      const result = await askAI(
        next.map((m) => ({ role: m.role, content: m.content })),
        allowTools,
      );
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: result.answer || '(пустой ответ)',
          tool_calls: result.tool_calls || [],
        },
      ]);
    } catch (e) {
      setError(e.message || 'Ошибка запроса к AI');
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = () => {
    setMessages([]);
    setError(null);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)] gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-primary">🤖 AI-помощник</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={allowTools}
              onChange={(e) => setAllowTools(e.target.checked)}
            />
            <span>Разрешить инструменты</span>
          </label>
          <button
            className="text-sm text-secondary hover:text-primary px-3 py-1 rounded-md border border-border hover:bg-elevated"
            onClick={handleClear}
            disabled={loading}
          >
            Очистить
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto rounded-lg border border-border bg-surface p-4 space-y-4"
      >
        {messages.length === 0 && (
          <div className="text-sm text-muted text-center py-12">
            Спроси что-нибудь — я могу прочитать логи, проверить состояние воркеров,
            перезапустить процесс.
          </div>
        )}
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`
                max-w-[80%] rounded-lg px-4 py-3 text-sm whitespace-pre-wrap
                ${msg.role === 'user'
                  ? 'bg-accent-muted text-accent'
                  : 'bg-elevated text-primary'}
              `}
            >
              {msg.content}
              {msg.tool_calls && msg.tool_calls.length > 0 && (
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer">
                    Использовано инструментов: {msg.tool_calls.length}
                  </summary>
                  <div className="mt-2 space-y-2">
                    {msg.tool_calls.map((tc, i) => (
                      <div key={i} className="rounded bg-base p-2">
                        <div className="font-mono">
                          {tc.name}({JSON.stringify(tc.args)})
                        </div>
                        {tc.error ? (
                          <div className="text-red-400 mt-1">ошибка: {tc.error}</div>
                        ) : (
                          <pre className="mt-1 whitespace-pre-wrap text-2xs">
                            {tc.result?.slice(0, 800)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-lg px-4 py-3 bg-elevated text-muted text-sm">
              думаю…
            </div>
          </div>
        )}
        {error && (
          <div className="rounded-lg px-4 py-3 bg-red-900/30 text-red-300 text-sm">
            {error}
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <textarea
          className="flex-1 rounded-md border border-border bg-surface p-3 text-sm text-primary resize-none"
          rows={2}
          placeholder="Сообщение… (Ctrl/Cmd+Enter — отправить)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <button
          className="rounded-md bg-accent px-5 py-3 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          onClick={handleSend}
          disabled={loading || !input.trim()}
        >
          {loading ? '…' : 'Отправить'}
        </button>
      </div>
    </div>
  );
}
