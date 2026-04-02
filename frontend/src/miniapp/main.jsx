import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './theme.css';
import MiniApp from './App.jsx';

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

createRoot(document.getElementById('miniapp-root')).render(
  <StrictMode>
    <MiniApp />
  </StrictMode>
);
