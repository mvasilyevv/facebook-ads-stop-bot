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
        return (
          <AdsPage
            key={`ads:${adsInitialView}:${adsInitialState}`}
            initialView={adsInitialView}
            initialState={adsInitialState}
          />
        );
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
        <div className="sidebar-brand">
          <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
            <path d="M14 2L4 6v8c0 5.5 4.3 10.7 10 12 5.7-1.3 10-6.5 10-12V6L14 2z"
                  fill="#4f6ef7" opacity="0.9"/>
            <ellipse cx="14" cy="14" rx="3.5" ry="4.5" fill="white" opacity="0.95"/>
            <circle cx="14" cy="13" r="1.5" fill="#0e1015"/>
            <path d="M14 15.5 Q14 17 14 18" stroke="#0e1015" strokeWidth="1.2" strokeLinecap="round"/>
          </svg>
          <div className="sidebar-brand-text">
            <span className="sidebar-brand-name">AdGuard FB</span>
            <span className="sidebar-brand-sub">Мониторинг рекламы</span>
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
