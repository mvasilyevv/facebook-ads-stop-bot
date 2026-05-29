/** Секция настроек Observer */
export function ObserverSettingsSection() {
  return (
    <section aria-label="Настройки Observer" className="panel p-5 space-y-5">
      <h2 className="text-base font-semibold text-primary">Observer — сканирование</h2>

      <div className="rounded-md border border-border bg-elevated/50 p-3">
        <p className="text-xs text-muted">
          Интервал сканирования подстраивается автоматически по уровню угрозы:
          <span className="font-semibold text-red-400"> 10с</span> (стоп) →
          <span className="font-semibold text-amber-400"> 13с</span> (warning) →
          <span className="font-semibold text-sky-400"> 15с</span> (активный залив) →
          <span className="font-semibold text-green-400"> 30с</span> (спокойно) →
          <span className="font-semibold text-muted"> 55с</span> (нет объявлений).
          После STOP — немедленный ре-скан.
        </p>
      </div>

      <div className="rounded-md border border-border bg-elevated/30 p-3">
        <p className="text-xs text-muted">
          Пороги срабатывания задаются отдельно для каждого оффера. Перейдите в раздел
          <span className="font-semibold text-primary"> «Офферы»</span> и откройте настройки нужного оффера.
        </p>
      </div>
    </section>
  );
}
