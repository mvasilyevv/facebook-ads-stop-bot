/**
 * AuthGuard — оборачивает приложение, выполняет TMA-авторизацию при старте.
 * Показывает loader пока идёт auth, ошибку если не авторизован.
 * После успешного auth — рендерит children.
 */
import { type ReactNode, useEffect, useState } from "react";
import { ensureAuthenticated } from "@/lib/auth";
import { initTheme } from "@/lib/tg";

type AuthStatus = "loading" | "ok" | "error";

interface AuthGuardProps {
  children: ReactNode;
}

export function AuthGuard({ children }: AuthGuardProps) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Инициализируем Telegram тему/ready/expand при первом монтировании.
    const cleanupTheme = initTheme();
    let active = true;

    ensureAuthenticated()
      .then(() => {
        if (active) setStatus("ok");
      })
      .catch((err: Error) => {
        if (!active) return;
        console.error("TMA auth error:", err);
        setError(err.message);
        setStatus("error");
      });
    return () => {
      active = false;
      cleanupTheme();
    };
  }, []);

  if (status === "loading") {
    return (
      <div
        role="status"
        aria-label="Авторизация..."
        className="flex flex-col items-center justify-center h-screen gap-4 bg-[var(--color-bg-0)]"
      >
        {/* Spinner */}
        <div
          aria-hidden
          className="w-8 h-8 border-2 border-[var(--color-bg-6)] border-t-[var(--color-accent)] rounded-full animate-spin motion-reduce:animate-none"
        />
        <p className="text-[13px] text-[var(--color-bg-9)] font-body">Авторизация...</p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="flex flex-col items-center justify-center h-screen gap-3 px-6 bg-[var(--color-bg-0)] text-center">
        <p className="text-[16px] font-semibold text-[var(--color-bg-11)] font-display">
          Нет доступа
        </p>
        <p className="text-[13px] text-[var(--color-bg-9)] font-body">
          {error ?? "Откройте приложение через Telegram-бота."}
        </p>
        <p className="text-[12px] text-[var(--color-bg-8)] font-mono mt-2">
          Получите invite у владельца бота.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
