/**
 * Skeleton — shimmer-заглушка при загрузке.
 * Три шейпа: line (текст), block (прямоугольник), row (полная строка таблицы).
 */
import { type HTMLAttributes } from "react";
import { cn } from "./cn";

interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  /** Высота в пикселях. Default 14 (строка текста). */
  height?: number;
  /** Ширина — px или строкой ("60%"). Default 100%. */
  width?: number | string;
  /** Тип: line/block одиночный, row — ряд из нескольких. */
  variant?: "line" | "block" | "row";
}

export function Skeleton({
  height = 14,
  width = "100%",
  variant = "line",
  className,
  style,
  ...rest
}: SkeletonProps) {
  if (variant === "row") {
    // Строка таблицы: 3 колонки разной ширины
    return (
      <div
        role="status"
        aria-label="Загрузка"
        className={cn("flex items-center gap-3 py-2", className)}
        {...rest}
      >
        <div className="bg-bg-3 animate-pulse h-3.5 w-[30%]" />
        <div className="bg-bg-3 animate-pulse h-3.5 flex-1" />
        <div className="bg-bg-3 animate-pulse h-3.5 w-[15%]" />
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-label="Загрузка"
      style={{ height, width, ...style }}
      className={cn("bg-bg-3 animate-pulse", className)}
      {...rest}
    />
  );
}

/** Сопоставленный ряд Skeleton.Row для таблиц. */
export function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} variant="row" />
      ))}
    </>
  );
}
