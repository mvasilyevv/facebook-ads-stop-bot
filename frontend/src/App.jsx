import { useState, useCallback, useEffect, Component, lazy, Suspense } from 'react';
import LoadingSpinner from './components/LoadingSpinner.jsx';
import { CommandPalette, useCommandPaletteHotkey } from './components/CommandPalette.jsx';

// Ленивая загрузка страниц — каждая страница загружается только при первом посещении
const DashboardPage = lazy(() => import('./pages/DashboardPage.jsx'));
const AdsPage = lazy(() => import('./pages/AdsPage.jsx'));
const OffersPage = lazy(() => import('./pages/OffersPage.jsx'));
const SettingsPage = lazy(() => import('./pages/SettingsPage.jsx'));
const HistoryPage = lazy(() => import('./pages/HistoryPage.jsx'));
const NamingTrackerPage = lazy(() => import('./pages/NamingTrackerPage.jsx'));
const ScriptsPage = lazy(() => import('./pages/ScriptsPage.jsx'));
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage.jsx'));
import SystemStatusBar from './components/system/SystemStatusBar.jsx';


/** Error Boundary — ловит ошибки рендера и показывает fallback */
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary поймал ошибку:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center min-h-[200px] p-6">
          <div className="text-center">
            <p className="text-red-400 text-sm font-medium mb-2">
              Произошла ошибка при отображении
            </p>
            <button
              className="text-xs text-accent hover:underline"
              onClick={() => this.setState({ hasError: false, error: null })}
            >
              Попробовать снова
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/* Иконки навигации — SVG inline, 20×20 */
const NAV_ICONS = {
  dashboard: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="7" height="8" rx="1.5" />
      <rect x="11" y="2" width="7" height="5" rx="1.5" />
      <rect x="2" y="12" width="7" height="6" rx="1.5" />
      <rect x="11" y="9" width="7" height="9" rx="1.5" />
    </svg>
  ),
  ads: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="3" width="16" height="14" rx="2" />
      <line x1="2" y1="7" x2="18" y2="7" />
      <line x1="6" y1="7" x2="6" y2="17" />
    </svg>
  ),
  offers: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 4h16v12a2 2 0 01-2 2H4a2 2 0 01-2-2V4z" />
      <path d="M2 4l3-2h10l3 2" />
      <line x1="8" y1="9" x2="12" y2="9" />
    </svg>
  ),
  history: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="8" />
      <polyline points="10,5 10,10 13,12" />
    </svg>
  ),
  naming: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <text x="3" y="15" fontSize="14" fontWeight="bold" fill="currentColor" stroke="none">#</text>
    </svg>
  ),
  settings: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="3" />
      <path d="M10 1v2M10 17v2M18.36 4.64l-1.42 1.42M3.06 13.94l-1.42 1.42M19 10h-2M3 10H1M15.78 15.78l-1.42-1.42M5.64 5.64L4.22 4.22" />
    </svg>
  ),
  scripts: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 3H16V17H4V3Z" />
      <path d="M7 7H13" />
      <path d="M7 10H13" />
      <path d="M7 13H10" />
    </svg>
  ),
  analytics: (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
      <line x1="2" y1="20" x2="2" y2="8" />
    </svg>
  ),
};

const PAGES = [
  { id: 'dashboard', label: 'Мониторинг' },
  { id: 'ads', label: 'Объявления' },
  { id: 'offers', label: 'Офферы' },
  { id: 'analytics', label: 'Аналитика' },
  { id: 'history', label: 'История' },
  { id: 'scripts', label: 'Скрипты' },
  { id: 'settings', label: 'Настройки' },
];

export default function App() {
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [adsInitialView, setAdsInitialView] = useState('active');
  const [adsInitialState, setAdsInitialState] = useState('');
  const [adsHighlightAdId, setAdsHighlightAdId] = useState('');
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);

  useCommandPaletteHotkey(() => setCommandPaletteOpen(true));

  /* navigate принимает строку вида 'ads', '/ads?view=active', '/ads?state=WARNING_SENT', '/ads?fb_ad_id=123' */
  const navigate = useCallback((target) => {
    const path = String(target);
    if (path.startsWith('/ads') || path === 'ads') {
      try {
        const url = new URL('http://x' + (path.startsWith('/') ? path : '/' + path));
        setAdsInitialView(url.searchParams.get('view') || 'active');
        setAdsInitialState(url.searchParams.get('state') || '');
        setAdsHighlightAdId(url.searchParams.get('fb_ad_id') || url.searchParams.get('highlight') || '');
      } catch {
        /* игнорируем ошибки парсинга URL */
      }
      setCurrentPage('ads');
    } else {
      setCurrentPage(path.replace(/^\//, ''));
    }
    setSidebarOpen(false);
  }, []);

  /* Закрываем сайдбар по Escape */
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape' && sidebarOpen) setSidebarOpen(false);
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
            key={`ads:${adsInitialView}:${adsInitialState}:${adsHighlightAdId}`}
            initialView={adsInitialView}
            initialState={adsInitialState}
            initialHighlightAdId={adsHighlightAdId}
            onOpenNaming={() => navigate('naming')}
          />
        );
      case 'offers':
        return <OffersPage />;
      case 'analytics':
        return <AnalyticsPage />;
      case 'history':
        return <HistoryPage onNavigate={navigate} />;
      case 'naming':
        return <NamingTrackerPage />;
      case 'scripts':
        return <ScriptsPage />;
      case 'settings':
        return <SettingsPage />;
      default:
        return <DashboardPage onNavigate={navigate} />;
    }
  };

  return (
    <div className="flex min-h-screen bg-base">
      <CommandPalette
        open={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        onNavigate={navigate}
      />

      {/* Skip link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent focus:px-4 focus:py-2 focus:text-base"
      >
        Перейти к контенту
      </a>

      {/* Мобильная кнопка меню */}
      <button
        className="fixed left-4 top-4 z-50 rounded-md bg-surface p-2 text-secondary lg:hidden hover:text-primary"
        onClick={() => setSidebarOpen(!sidebarOpen)}
        aria-label={sidebarOpen ? 'Закрыть меню' : 'Открыть меню'}
        aria-expanded={sidebarOpen}
      >
        {sidebarOpen ? (
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="4" y1="4" x2="16" y2="16" />
            <line x1="16" y1="4" x2="4" y2="16" />
          </svg>
        ) : (
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="3" y1="5" x2="17" y2="5" />
            <line x1="3" y1="10" x2="17" y2="10" />
            <line x1="3" y1="15" x2="17" y2="15" />
          </svg>
        )}
      </button>

      {/* Оверлей мобильного сайдбара */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden animate-fade-in"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Сайдбар */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-40 flex w-sidebar flex-col border-r border-border bg-surface
          transition-transform duration-200 ease-out
          lg:translate-x-0
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
        role="navigation"
        aria-label="Главное меню"
      >
        {/* Логотип */}
        <div className="flex items-center gap-3 border-b border-border px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center border border-border bg-elevated">
            <svg width="20" height="20" viewBox="0 0 28 28" fill="none" aria-hidden="true">
              <path
                d="M14 2L4 6v8c0 5.5 4.3 10.7 10 12 5.7-1.3 10-6.5 10-12V6L14 2z"
                fill="#C9A227"
              />
              <ellipse cx="14" cy="14" rx="3.5" ry="4.5" fill="#F2EFE8" opacity="0.92" />
              <circle cx="14" cy="13" r="1.5" fill="#0C0C0C" />
            </svg>
          </div>
          <div className="flex flex-col">
            <span className="font-display text-sm font-semibold text-primary">AdGuard FB</span>
            <span className="text-2xs text-muted">Мониторинг объявлений</span>
          </div>
        </div>

        {/* Навигация */}
        <nav className="mt-2 flex flex-col gap-0.5 px-3">
          {PAGES.map((page) => {
            const isActive = currentPage === page.id;
            return (
              <button
                key={page.id}
                className={`nav-item ${isActive ? 'nav-item-active' : 'nav-item-idle'}`}
                onClick={() => navigate(page.id)}
                aria-current={isActive ? 'page' : undefined}
              >
                <span className={isActive ? 'text-accent' : 'text-muted'}>
                  {NAV_ICONS[page.id]}
                </span>
                {page.label}
              </button>
            );
          })}
        </nav>

        {/* Футер сайдбара */}
        <div className="mt-auto px-5 py-4">
          <span className="text-2xs text-muted">v0.1.0</span>
        </div>
      </aside>

      {/* Основной контент */}
      <main
        className="flex-1 lg:ml-sidebar min-h-screen"
        id="main-content"
        role="main"
      >
        <div className="mx-auto max-w-content p-md pt-16 lg:p-lg">
          <ErrorBoundary>
            {/* Suspense показывает спиннер пока чанк страницы загружается */}
            <Suspense fallback={<LoadingSpinner />}>
              {renderPage()}
            </Suspense>
          </ErrorBoundary>
        </div>
      </main>
      <SystemStatusBar />
    </div>
  );
}
