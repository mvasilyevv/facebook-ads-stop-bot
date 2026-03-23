import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import AdsPage from "./pages/AdsPage";
import SettingsPage from "./pages/SettingsPage";
import DecisionsPage from "./pages/DecisionsPage";
import OffersPage from "./pages/OffersPage";
import SessionsPage from "./pages/SessionsPage";
import ScansPage from "./pages/ScansPage";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/ads" element={<AdsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/decisions" element={<DecisionsPage />} />
          <Route path="/rules" element={<Navigate to="/settings" replace />} />
          <Route path="/offers" element={<OffersPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/scans" element={<ScansPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
