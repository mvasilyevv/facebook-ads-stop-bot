import { create } from "zustand";

/**
 * Стор командной палитры (⌘K). Глобальное open-состояние:
 * триггерится из TopBar (кнопка/горячая клавиша), рендерится в Shell.
 */
interface CommandPaletteState {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

export const useCommandPalette = create<CommandPaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
  toggle: () => set((s) => ({ open: !s.open })),
}));
