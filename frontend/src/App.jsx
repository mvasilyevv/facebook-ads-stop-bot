import { useState, useCallback, useEffect } from 'react';
import DashboardPage from './pages/DashboardPage.jsx';
import AdsPage from './pages/AdsPage.jsx';
import OffersPage from './pages/OffersPage.jsx';
import SettingsPage from './pages/SettingsPage.jsx';

const PAGES = [
  { id: 'dashboard', label: 'Мониторинг', code: '01' },
  { id: 'ads', label: 'Объявления', code: '02' },
  { id: 'offers', label: 'Офферы', code: '03' },
  { id: 'settings', label: 'Настройки', code: '04' },
];

export default function App() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [adsInitialView, setAdsInitialView] = useState('active');
  const [adsInitialState, setAdsInitialState] = useState('');

  /* navigate принимает строку вида 'ads', '/ads?view=active', '/ads?state=WARNING_SENT' */
  const navigate = useCallback((target) => {
    const path = String(target);
    if (path.startsWith('/ads') || path === 'ads') {
      try {
        const url = new URL('http://x' + (path.startsWith('/') ? path : '/' + path));
        setAdsInitialView(url.searchParams.get('view') || 'active');
        setAdsInitialState(url.searchParams.get('state') || '');
      } catch (_) {}
      setCurrentPage('ads');
    } else {
      setCurrentPage(path.replace(/^\//, ''));
    }
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
        return <DashboardPage onNavigate={navigate} />;
      case 'ads':
        return <AdsPage initialView={adsInitialView} initialState={adsInitialState} />;
      case 'offers':
        return <OffersPage />;
      case 'settings':
        return <SettingsPage />;
      default:
        return <DashboardPage onNavigate={navigate} />;
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
          <span className="sidebar-logo__mark">◈</span>
          <div className="sidebar-logo__text">
            <span className="sidebar-logo__brand">AdGuard</span>
            <span className="sidebar-logo__sub">FB Bot</span>
          </div>
        </div>
        <nav>
          {PAGES.map((page) => (
            <button
              key={page.id}
              className={`nav-item ${currentPage === page.id ? 'active' : ''}`}
              onClick={() => navigate(page.id)}
              aria-current={currentPage === page.id ? 'page' : undefined}
            >
              <span className="nav-code" aria-hidden="true">{page.code}</span>
              {page.label}
            </button>
          ))}
        </nav>
        <div style={{ flex: 1 }} />
        <div className="sidebar-footer">v0.1.0</div>
      </aside>

      {/* Основной контент */}
      <main className="main-content" id="main-content" role="main">
        {renderPage()}
      </main>
    </div>
  );
}
