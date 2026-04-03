import { useEffect } from 'react';

const TYPE_STYLES = {
  success: 'border-success/30 bg-success-muted text-success',
  error: 'border-danger/30 bg-danger-muted text-danger',
};

/** Автозакрывающееся уведомление */
export function SettingsToast({ message, type, onClose }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 3000);
    return () => clearTimeout(timer);
  }, [onClose]);

  return (
    <div
      className={`fixed bottom-4 right-4 z-50 rounded-md border px-4 py-3 text-sm shadow-lg animate-fade-in ${TYPE_STYLES[type] || TYPE_STYLES.success}`}
      role="alert"
    >
      {message}
    </div>
  );
}
