import React, { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { getStoredToken, loginToBackend, logout } from "./auth.js";
import HealthBar from "./components/HealthBar.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import HealthPage from "./pages/HealthPage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import AdsPage from "./pages/AdsPage.jsx";
import OffersPage from "./pages/OffersPage.jsx";
import ScriptsPage from "./pages/ScriptsPage.jsx";
import HistoryPage from "./pages/HistoryPage.jsx";
import AdDetailPage from "./pages/AdDetailPage.jsx";

// Гард аутентификации — выполняет логин при первом рендере
function AuthGuard({ children }) {
  const [status, setStatus] = useState("loading");

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
    return (
      <div className="loader-wrap">
        <div className="spinner" />
        <span>Авторизация...</span>
      </div>
    );
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

// Управление MainButton Telegram на странице настроек
function TelegramBackButton() {
  const location = useLocation();
  const navigate = useNavigate();
  const tg = window.Telegram?.WebApp;

  useEffect(() => {
    if (!tg) return;
    // BackButton показываем только на вложенных страницах (health, scripts)
    const showBack = ["/health", "/scripts"].includes(location.pathname) || /^\/ads\/[^/]+$/.test(location.pathname);
    if (showBack) {
      tg.BackButton.show();
      const handler = () => navigate(-1);
      tg.BackButton.onClick(handler);
      return () => {
        tg.BackButton.offClick(handler);
        tg.BackButton.hide();
      };
    } else {
      tg.BackButton.hide();
    }
  }, [location.pathname, navigate, tg]);

  return null;
}

// Пользовательский хук для отслеживания онлайн-статуса
export function useNetworkStatus() {
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  return isOnline;
}

export default function App() {
  const isOnline = useNetworkStatus();

  return (
    <BrowserRouter basename="/tma">
      <AuthGuard>
        <TelegramBackButton />
        {!isOnline && (
          <div className="offline-banner">
            ⚠️ Отсутствует подключение к сети. Используются кэшированные данные.
          </div>
        )}
        <HealthBar />
        <div className={`container page-content${!isOnline ? " has-offline-banner" : ""}`}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/ads" element={<AdsPage />} />
            <Route path="/offers" element={<OffersPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/scripts" element={<ScriptsPage />} />
            <Route path="/ads/:fbAdId" element={<AdDetailPage />} />
          </Routes>
        </div>
        <TabBar />
      </AuthGuard>
    </BrowserRouter>
  );
}

