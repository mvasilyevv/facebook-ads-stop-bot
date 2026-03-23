import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import DecisionsPage from "../src/pages/DecisionsPage";

describe("DecisionsPage", () => {
  // Проверяет, что старый маршрут решений перенаправляет на страницу настроек.
  it("перенаправляет на settings", async () => {
    render(
      <MemoryRouter initialEntries={["/decisions"]}>
        <Routes>
          <Route path="/decisions" element={<DecisionsPage />} />
          <Route path="/settings" element={<div>Страница настроек</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Страница настроек")).toBeInTheDocument();
  });
});
