// Заглушка — будет реализована в T20.
export default function ScanRunsHistoryModal({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="rounded border border-border bg-surface p-4 text-xs text-muted">
        Модалка истории сканов (заглушка, будет реализована).
      </div>
    </div>
  );
}
