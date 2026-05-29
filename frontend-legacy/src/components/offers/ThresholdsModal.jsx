import OfferThresholdsTab from './OfferThresholdsTab.jsx';

/** Обёртка для обратной совместимости; предпочтительно OfferThresholdsTab в панели деталей. */
export default function ThresholdsModal({ offer, onClose, onSaved }) {
  return (
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/60"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="panel max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg text-primary">Пороги оффера {offer.code}</h2>
        <OfferThresholdsTab
          offer={offer}
          onSaved={() => {
            onSaved?.();
            onClose();
          }}
        />
        <div className="flex justify-end pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
