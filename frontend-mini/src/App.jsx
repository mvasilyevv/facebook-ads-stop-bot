import React, { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { getStoredToken, loginToBackend, logout } from "./auth.js";
import DashboardPage from "./pages/DashboardPage.jsx";
import HealthPage from "./pages/HealthPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import AdsPage from "./pages/AdsPage.jsx";
import OffersPage from "./pages/OffersPage.jsx";
import ScriptsPage from "./pages/ScriptsPage.jsx";
import HistoryPage from "./pages/HistoryPage.jsx";

// Гард аутентификации — выполняет логин при первом рендере
function AuthGuard({ children }) {
  const [status, setStatus] = useState("loading");
  const navigate = useNavigate();

  useEffect(() => {
    if (getStoredToken()) {
      setStatus("ok");
      return;
    }
    loginToBackend()
      .then(() => setStatus("ok"))
      .catch((err) => {
        console.error("Ошибка TMA-аутентификации:", err);
        logout();
        setStatus("error");
      });
  }, []);

  if (status === "loading") {
    return <div className="loading">Авторизация...</div>;
  }
  if (status === "error") {
    return (
      <div className="error-screen">
        <p>Не авторизован</p>
        <p className="hint">Откройте приложение через Telegram-бота.</p>
      </div>
    );
  }
  return children;
}

// Нижний tab-bar с эмодзи
function TabBar() {
  const tabs = [
    { to: "/", icon: "📊", label: "Дашборд", end: true },
    { to: "/ads", icon: "📢", label: "Объявления" },
    { to: "/offers", icon: "🎯", label: "Офферы" },
    { to: "/history", icon: "📅", label: "История" },
    { to: "/settings", icon: "⚙️", label: "Настройки" },
  ];

  return (
    <nav className="tab-bar">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) => (isActive ? "active" : "")}
        >
          <span className="tab-icon">{tab.icon}</span>
          <span>{tab.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/tma">
      <AuthGuard>
        <div className="container page-content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/ads" element={<AdsPage />} />
            <Route path="/offers" element={<OffersPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/scripts" element={<ScriptsPage />} />
          </Routes>
        </div>
        <TabBar />
      </AuthGuard>
    </BrowserRouter>
  );
}
