import { useState } from 'react';
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

  const renderPage = () => {
    switch (currentPage) {
      case 'dashboard': return <DashboardPage />;
      case 'ads': return <AdsPage />;
      case 'offers': return <OffersPage />;
      case 'settings': return <SettingsPage />;
      default: return <DashboardPage />;
    }
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-logo">
          🛑 <span>Stop Bot</span> v2
        </div>
        {PAGES.map((page) => (
          <button
            key={page.id}
            className={`nav-item ${currentPage === page.id ? 'active' : ''}`}
            onClick={() => setCurrentPage(page.id)}
          >
            <span className="nav-icon">{page.icon}</span>
            {page.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ padding: '12px', color: 'var(--text-muted)', fontSize: 11 }}>
          FB Stop Bot v2 • 0.1.0
        </div>
      </aside>
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  );
}
