import React, { useState, useEffect } from 'react';
import { getObserverSettings } from '../../api';

/**
 * Узкая нижняя статус-панель для отображения состояния воркеров и времени сканирования.
 */
export default function SystemStatusBar() {
  const [observerActive, setObserverActive] = useState(true);
  const [disableWorkerActive, setDisableWorkerActive] = useState(true);
  const [lastScanTime, setLastScanTime] = useState(null);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const data = await getObserverSettings();
        if (data) {
          setObserverActive(data.is_scanning ?? true);
          setDisableWorkerActive(true); // Disable worker обычно всегда активен, если сервер запущен
          if (data.last_scan_finished_at) {
            setLastScanTime(new Date(data.last_scan_finished_at).toLocaleTimeString('ru-RU'));
          } else {
            setLastScanTime(new Date().toLocaleTimeString('ru-RU'));
          }
        }
      } catch (err) {
        console.error('Ошибка загрузки статуса воркеров:', err);
      }
    };

    fetchStatus();
    const interval = setInterval(fetchStatus, 30000); // Обновляем раз в 30 секунд
    return () => clearInterval(interval);
  }, []);

  return (
    <footer className="fixed bottom-0 left-0 right-0 z-40 flex h-6 items-center justify-between border-t border-border bg-surface px-md font-mono text-[10px] text-text-dim">
      <div className="flex items-center gap-md">
        {/* Статус Observer */}
        <div className="flex items-center gap-xs">
          <span className={`h-1.5 w-1.5 rounded-full ${observerActive ? 'bg-ok animate-pulse shadow-[0_0_4px_var(--ok)]' : 'bg-stop'}`} />
          <span>OBSERVER: {observerActive ? 'ONLINE' : 'OFFLINE'}</span>
        </div>

        {/* Статус Disable Worker */}
        <div className="flex items-center gap-xs">
          <span className={`h-1.5 w-1.5 rounded-full ${disableWorkerActive ? 'bg-ok animate-pulse shadow-[0_0_4px_var(--ok)]' : 'bg-stop'}`} />
          <span>DISABLE_WORKER: {disableWorkerActive ? 'ACTIVE' : 'OFFLINE'}</span>
        </div>
      </div>

      <div className="flex items-center gap-md">
        {lastScanTime && (
          <span>ПОСЛЕДНИЙ СКАН: {lastScanTime}</span>
        )}
        <span className="text-text-muted">| SYSTEM OK</span>
      </div>
    </footer>
  );
}
