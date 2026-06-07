/**
 * PulseDot — пульсирующая «дышащая» точка (live-индикатор).
 * Класс .wp-dot (keyframes fbPulse в tokens.css). Negative animation-delay от
 * общего эпоха → все точки дышат в унисон. Порт из web (единый канон).
 */
import { useState, type CSSProperties } from "react";

const PULSE_MS = 2400;
const PULSE_EPOCH = Date.now();

interface PulseDotProps {
  /** Диаметр в px. */
  size?: number;
  /** CSS-цвет (var(--success) и т.п.). */
  color: string;
  className?: string;
  style?: CSSProperties;
}

export function PulseDot({ size = 7, color, className, style }: PulseDotProps) {
  const [animationDelay] = useState(
    () => `-${(Date.now() - PULSE_EPOCH) % PULSE_MS}ms`,
  );

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
