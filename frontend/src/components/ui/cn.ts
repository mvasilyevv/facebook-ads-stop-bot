/**
 * cn — объединение Tailwind-классов с дедупликацией.
 * Локальная копия в ui/ чтобы не зависеть от lib/ (чужой домен).
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
