import { useState, useCallback, useEffect } from 'react';
import DashboardPage from './pages/DashboardPage.jsx';
import AdsPage from './pages/AdsPage.jsx';
import OffersPage from './pages/OffersPage.jsx';
import SettingsPage from './pages/SettingsPage.jsx';

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
  { id: 'ads', label: 'Объявления', icon: '📋' },
  { id: 'offers', label: 'Офферы', icon: '🎯' },
  { id: 'settings', label: 'Настройки', icon: '⚙️' },
];

export default function App() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);

  /* Закрываем мобильный сайдбар при переключении страницы */
  const navigate = useCallback((pageId) => {
    setCurrentPage(pageId);
    setSidebarOpen(false);
  }, []);

  /* Закрываем сайдбар по Escape */
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape' && sidebarOpen) {
        setSidebarOpen(false);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [sidebarOpen]);

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard':
        return <DashboardPage />;
      case 'ads':
        return <AdsPage />;
      case 'offers':
        return <OffersPage />;
      case 'settings':
        return <SettingsPage />;
      default:
        return <DashboardPage />;
    }
  };

  return (
    <div className="app">
      {/* Ссылка «Перейти к контенту» для клавиатурной навигации */}
      <a href="#main-content" className="skip-link">
        Перейти к контенту
      </a>

      {/* Кнопка открытия мобильного меню */}
      <button
        className="mobile-menu-btn"
        onClick={() => setSidebarOpen(!sidebarOpen)}
        aria-label={sidebarOpen ? 'Закрыть меню' : 'Открыть меню'}
        aria-expanded={sidebarOpen}
      >
        {sidebarOpen ? '✕' : '☰'}
      </button>

      {/* Оверлей для закрытия мобильного сайдбара */}
      <div
        className={`sidebar-overlay ${sidebarOpen ? 'visible' : ''}`}
        onClick={() => setSidebarOpen(false)}
        aria-hidden="true"
      />

      {/* Навигация */}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} role="navigation" aria-label="Главное меню">
        <div className="sidebar-logo" aria-hidden="true">
          🛑 <span>Stop Bot</span> v2
        </div>
        <nav>
          {PAGES.map((page) => (
            <button
              key={page.id}
              className={`nav-item ${currentPage === page.id ? 'active' : ''}`}
              onClick={() => navigate(page.id)}
              aria-current={currentPage === page.id ? 'page' : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {page.icon}
              </span>
              {page.label}
            </button>
          ))}
        </nav>
        <div style={{ flex: 1 }} />
        <div style={{ padding: '12px', color: 'var(--text-muted)', fontSize: 11 }}>
          FB Stop Bot v2 • 0.1.0
        </div>
      </aside>

      {/* Основной контент */}
      <main className="main-content" id="main-content" role="main">
        {renderPage()}
      </main>
    </div>
  );
}
