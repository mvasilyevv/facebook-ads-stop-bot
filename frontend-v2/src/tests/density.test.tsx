// Тест: toggle density меняет состояние Zustand store + CSS variable.
import { describe, it, expect, beforeEach } from "vitest";
import { useUiStore, DENSITY_ROW_HEIGHT } from "@/stores/ui";

describe("density toggle", () => {
  beforeEach(() => {
    // Сбрасываем store к дефолту перед каждым тестом.
    useUiStore.setState({ density: "comfortable", sidebarCollapsed: false });
  });

  // Тест: дефолтная плотность — comfortable, высота 32px.
  it("дефолт comfortable, 32px", () => {
    const state = useUiStore.getState();
    expect(state.density).toBe("comfortable");
    expect(DENSITY_ROW_HEIGHT[state.density]).toBe(32);
  });

  // Тест: toggle переключает между comfortable и compact.
  it("toggle переключает", () => {
    useUiStore.getState().toggleDensity();
    expect(useUiStore.getState().density).toBe("compact");
    expect(DENSITY_ROW_HEIGHT[useUiStore.getState().density]).toBe(24);
    useUiStore.getState().toggleDensity();
    expect(useUiStore.getState().density).toBe("comfortable");
  });

  // Тест: setDensity напрямую устанавливает значение.
  it("setDensity ставит явное значение", () => {
    useUiStore.getState().setDensity("compact");
    expect(useUiStore.getState().density).toBe("compact");
  });

  // Тест: CSS variable обновляется при изменении density.
  it("обновляет CSS variable --table-row-height", () => {
    useUiStore.getState().setDensity("compact");
    expect(document.documentElement.style.getPropertyValue("--table-row-height")).toBe("24px");
    useUiStore.getState().setDensity("comfortable");
    expect(document.documentElement.style.getPropertyValue("--table-row-height")).toBe("32px");
  });
});
