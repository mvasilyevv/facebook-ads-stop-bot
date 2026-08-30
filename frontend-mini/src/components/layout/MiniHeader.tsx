/**
 * MiniHeader — шапка экрана канона.
 * Eyebrow (num + текст) + h1 mono 26px weight 500 letter-spacing -0.02em.
 * Без точки и ghost-числа (канон). Правый слот — счётчик/действие.
 */
import type { ReactNode } from "react";
import { Eyebrow } from "@fb/operator-ui";
import { cn } from "@/lib/cn";

interface MiniHeaderProps {
  /** Номер-маркер eyebrow (например "04"). */
  eyebrowNum?: string;
  /** Текст eyebrow (например "УПРАВЛЕНИЕ"). */
  eyebrow?: string;
  title: string;
  /** Правый слот: счётчик, кнопка действия. */
  right?: ReactNode;
  className?: string;
}

export function MiniHeader({ eyebrowNum, eyebrow, title, right, className }: MiniHeaderProps) {
  return (
    <header
      className={cn(
        "px-4 pt-2 pb-3 border-b border-[var(--color-hairline)] bg-bg-0",
        className,
      )}
    >
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? <Eyebrow num={eyebrowNum}>{eyebrow}</Eyebrow> : null}
          <h1
            className="font-display font-medium text-bg-11 m-0 mt-1 leading-[1.05]"
            style={{ fontSize: 26, letterSpacing: "-0.02em" }}
          >
            {title}
          </h1>
        </div>
        {right ? <div className="shrink-0">{right}</div> : null}
      </div>
    </header>
  );
}
