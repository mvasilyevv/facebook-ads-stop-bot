/**
 * TelegramBackButton — управляет нативной кнопкой Telegram BackButton.
 * Показывается только на вложенных экранах (детали, черновики, health и т.п.).
 * Монтируется один раз в __root и реагирует на смену пути.
 */
import { useEffect } from "react";
import { useLocation, useRouter } from "@tanstack/react-router";
import { registerBackButton, hideBackButton } from "@/lib/tg";

/**
 * Пути, на которых показываем BackButton — detail + вторичные экраны «Ещё».
 * Черновики теперь основной таб (back не нужен).
 */
const BACK_BUTTON_PATTERNS: RegExp[] = [
  /^\/ads\/.+$/,
  /^\/offers$/,
  /^\/health$/,
  /^\/scripts$/,
];

function needsBackButton(pathname: string): boolean {
  return BACK_BUTTON_PATTERNS.some((re) => re.test(pathname));
}

export function TelegramBackButton() {
  const router = useRouter();
  const location = useLocation();
  const pathname = location.pathname;

  useEffect(() => {
    if (needsBackButton(pathname)) {
      const cleanup = registerBackButton(() => {
        void router.history.back();
      });
      return cleanup;
    }
    hideBackButton();
    return undefined;
  }, [pathname, router]);

  // Компонент не рендерит DOM — только управляет TG BackButton.
  return null;
}
