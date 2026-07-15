/**
 * ChatMessageItem — одна реплика ленты AssistantWidget.
 *
 * kind="user"/"assistant" — обычный пузырь (user справа, assistant слева),
 * под ответом ассистента — строка "проверил: <tool>, <tool>" если были tool_calls
 * (упавшие — зачёркнуты, title = текст ошибки).
 * kind="notification" — 🔔 алерт из WS, левая рамка danger (stop) / warning.
 */
import { Fragment } from "react";
import { cn } from "@/lib/utils/cn";
import type { ChatWidgetMessage } from "@/stores/chatWidget";

/** Лёгкий markdown построчно: "- " → буллет, **text** → bold. Без полноценного рендера. */
function renderContent(content: string) {
  const lines = content.split("\n");
  return lines.map((line, lineIdx) => {
    const trimmed = line.trimStart();
    const isBullet = trimmed.startsWith("- ");
    const text = isBullet ? trimmed.slice(2) : line;
    const boldParts = text.split(/(\*\*[^*]+\*\*)/g);
    return (
      <span key={lineIdx} className="block">
        {isBullet ? "• " : ""}
        {boldParts.map((part, partIdx) =>
          part.startsWith("**") && part.endsWith("**") && part.length > 3 ? (
            <strong key={partIdx}>{part.slice(2, -2)}</strong>
          ) : (
            <Fragment key={partIdx}>{part}</Fragment>
          ),
        )}
      </span>
    );
  });
}

interface ChatMessageItemProps {
  message: ChatWidgetMessage;
}

export function ChatMessageItem({ message }: ChatMessageItemProps) {
  if (message.kind === "notification") {
    const isStop = message.stage === "stop";
    return (
      <div
        role="status"
        className={cn(
          "px-3 py-2 border-l-2 bg-bg-2 text-[12.5px] text-bg-11 leading-[1.4]",
          isStop ? "border-l-danger" : "border-l-warning",
        )}
      >
        {renderContent(message.content)}
      </div>
    );
  }

  const isUser = message.kind === "user";
  return (
    <div
      className={cn(
        "flex flex-col gap-1 max-w-[85%]",
        isUser ? "self-end items-end" : "self-start items-start",
      )}
    >
      <div
        className={cn(
          "px-3 py-2 text-[13px] leading-[1.45] border rounded-[var(--radius-2)]",
          isUser
            ? "bg-bg-3 border-[var(--hairline-strong)] text-bg-11"
            : "bg-bg-2 border-[var(--hairline)] text-bg-11",
        )}
      >
        {renderContent(message.content)}
      </div>
      {!isUser && message.toolCalls && message.toolCalls.length > 0 ? (
        <div className="text-[10.5px] text-bg-8 font-display px-1">
          проверил:{" "}
          {message.toolCalls.map((tool, idx) => (
            <span key={`${tool.name}_${idx}`}>
              {idx > 0 ? ", " : ""}
              <span
                className={cn(tool.error && "line-through text-bg-7")}
                title={tool.error ?? undefined}
              >
                {tool.name}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
