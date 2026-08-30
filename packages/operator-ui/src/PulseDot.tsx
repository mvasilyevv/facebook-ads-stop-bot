/**
 * PulseDot — пульсирующая «дышащая» точка (live-индикатор).
 *
 * Использует класс .wp-dot из packages/shared/src/tokens/tokens.css
 * (keyframes fbPulse поверх currentColor). Negative animation-delay
 * вычисляется один раз на mount от общего эпоха → все точки на странице
 * дышат в унисон, даже если смонтированы в разное время.
 *
 * Цвет передаётся как CSS-значение (var(--color-success)/var(--color-warning)/…):
 * задаётся и в `background`, и в `color` — fbPulse строит box-shadow от currentColor.
 *
 * Единый источник для обоих фронтов (раньше был файлом-близнецом в
 * frontend/src/components/data и frontend-mini/src/components/data).
 */

import { useState, type CSSProperties } from "react";

const PULSE_MS = 2400;
// Эпоха фиксируется при загрузке модуля — единая фаза для всех инстансов.
const PULSE_EPOCH = Date.now();

export interface PulseDotProps {
  /** Диаметр в px. */
  size?: number;
  /** CSS-цвет (var(--color-success) и т.п.). */
  color: string;
  className?: string;
  style?: CSSProperties;
}

export function PulseDot({ size = 7, color, className, style }: PulseDotProps) {
  // Считаем delay один раз через lazy-init useState — стабильная фаза на весь
  // жизненный цикл точки, читать из state в render безопасно.
  const [animationDelay] = useState(() => `-${(Date.now() - PULSE_EPOCH) % PULSE_MS}ms`);

  return (
    <span
      aria-hidden="true"
      className={className ? `wp-dot ${className}` : "wp-dot"}
      style={{
        width: size,
        height: size,
        borderRadius: 9999,
        background: color,
        color,
        flex: "none",
        animationDelay,
        ...style,
      }}
    />
  );
}
