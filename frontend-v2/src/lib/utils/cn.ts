import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Конкатенирует Tailwind-классы с дедупликацией конфликтующих правил.
 * Используется во всех компонентах при компоновке className.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
