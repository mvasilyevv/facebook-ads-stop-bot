import { useState } from 'react';
import DashboardScreen from './screens/DashboardScreen.jsx';
import AlertsScreen from './screens/AlertsScreen.jsx';
import AdDetailScreen from './screens/AdDetailScreen.jsx';

export default function MiniApp() {
  const [screen, setScreen] = useState('dashboard');
  const [params, setParams] = useState({});

  function navigate(name, p = {}) {
    setScreen(name);
    setParams(p);
    const tg = window.Telegram?.WebApp;
    if (tg) {
      if (name === 'dashboard') tg.BackButton.hide();
      else tg.BackButton.show();
    }
  }

  // Telegram back button handler
  const tg = window.Telegram?.WebApp;
  if (tg?.BackButton) {
    tg.BackButton.onClick(() => navigate('dashboard'));
  }

  if (screen === 'alerts')
    return (
      <AlertsScreen
        onSelectAd={(id) => navigate('ad_detail', { fbAdId: id })}
        onBack={() => navigate('dashboard')}
      />
    );
  if (screen === 'ad_detail')
    return (
      <AdDetailScreen
        fbAdId={params.fbAdId}
        onBack={() => navigate('alerts')}
      />
    );
  return <DashboardScreen onOpenAlerts={() => navigate('alerts')} />;
}
