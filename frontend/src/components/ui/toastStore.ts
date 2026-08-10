import type { ReactNode } from "react";
import { create } from "zustand";

export type ToastVariant = "success" | "error" | "info" | "warning";

export interface ToastItem {
  id: string;
  title: ReactNode;
  description?: ReactNode;
  variant: ToastVariant;
  /** 0 = не закрывается автоматически. */
  duration: number;
}

interface ToastStore {
  toasts: ToastItem[];
  add: (toast: Omit<ToastItem, "id">) => void;
  remove: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  add: (toast) => {
    const id = `t_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }));
  },
  remove: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((toast) => toast.id !== id),
    })),
}));

export const toast = {
  success: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "success", duration: 4_000 }),
  info: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "info", duration: 4_000 }),
  warning: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "warning", duration: 8_000 }),
  error: (title: ReactNode, description?: ReactNode) =>
    useToastStore.getState().add({ title, description, variant: "error", duration: 0 }),
};
