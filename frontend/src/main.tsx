import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import AdsPage from "./pages/AdsPage";
import DecisionsPage from "./pages/DecisionsPage";
import RulesPage from "./pages/RulesPage";
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
          <Route path="/decisions" element={<DecisionsPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/offers" element={<OffersPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/scans" element={<ScansPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
