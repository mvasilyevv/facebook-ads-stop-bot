# Campaign Recorder — Фаза 1B: Фронтенд

> **Ждёт:** Фаза 1A (бэкенд роутер `/api/campaign-recorder/*` должен быть запущен)

**Цель:** Добавить на страницу Scripts новый submodule "Запись" — кнопки старт/стоп и просмотр отчёта анализатора.

**Архитектура:** Расширяем существующий `ScriptsPage.jsx` — добавляем submodule в секцию "Кампании". Новые API-функции в `api.js`.

**Стек:** React 19, Vite, существующие CSS-классы проекта.

---

## Файловая карта

| Действие | Файл |
|----------|------|
| Изменить | `frontend/src/api.js` |
| Изменить | `frontend/src/pages/ScriptsPage.jsx` |

---

### Task 1: Добавить API-функции в api.js

**Файлы:**
- Изменить: `frontend/src/api.js`

- [ ] **Шаг 1.1: Добавить функции в конец `frontend/src/api.js`**

```js
// --- Campaign Recorder ---
export const startRecording = (data) =>
  request('/campaign-recorder/start', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const stopRecording = (sessionId) =>
  request(`/campaign-recorder/stop/${sessionId}`, { method: 'POST' });

export const analyzeLastRecording = (offerCode) =>
  request(`/campaign-recorder/analyze${offerCode ? `?offer_code=${encodeURIComponent(offerCode)}` : ''}`);
```

- [ ] **Шаг 1.2: Проверить что frontend собирается**

```bash
cd frontend && npm run build 2>&1 | tail -5
```
Ожидаем: `built in X.Xs` без ошибок

- [ ] **Шаг 1.3: Коммит**

```bash
git add frontend/src/api.js
git commit -m "feat: campaign_recorder — API-функции во frontend"
```

---

### Task 2: Добавить submodule "Запись" в ScriptsPage

**Файлы:**
- Изменить: `frontend/src/pages/ScriptsPage.jsx`

- [ ] **Шаг 2.1: Добавить "record" в SCRIPT_MODULES**

Найти константу `SCRIPT_MODULES` в `ScriptsPage.jsx` и изменить секцию `campaigns`:

```js
const SCRIPT_MODULES = [
  {
    id: 'creatives',
    label: 'Креативы',
    submodules: [{ id: 'uniquify', label: 'Уникализация' }],
  },
  {
    id: 'campaigns',
    label: 'Кампании',
    submodules: [
      { id: 'create', label: 'Создание из папки' },
      { id: 'record', label: 'Запись сессии' },
    ],
  },
];
```

- [ ] **Шаг 2.2: Добавить state для recorder**

В теле `ScriptsPage` после существующих `useState` добавить:

```js
const [recorderOffer, setRecorderOffer] = useState('');
const [recorderCdpUrl, setRecorderCdpUrl] = useState('ws://localhost:9222');
const [recorderSessionId, setRecorderSessionId] = useState(null);
const [recorderStatus, setRecorderStatus] = useState('idle'); // idle | recording | stopped
const [recorderReport, setRecorderReport] = useState(null);
const [recorderError, setRecorderError] = useState('');
```

- [ ] **Шаг 2.3: Добавить импорт функций recorder**

В блок импортов `api.js` добавить:

```js
import {
  // ... существующие импорты ...
  startRecording,
  stopRecording,
  analyzeLastRecording,
} from '../api';
```

- [ ] **Шаг 2.4: Добавить обработчики**

После существующих хендлеров (перед `return`):

```js
const handleStartRecording = useCallback(async () => {
  if (!recorderOffer) {
    setRecorderError('Выберите оффер для записи');
    return;
  }
  setRecorderError('');
  try {
    const res = await startRecording({ offer_code: recorderOffer, cdp_url: recorderCdpUrl });
    setRecorderSessionId(res.session_id);
    setRecorderStatus('recording');
  } catch (err) {
    setRecorderError(err.message || 'Не удалось запустить запись');
  }
}, [recorderOffer, recorderCdpUrl]);

const handleStopRecording = useCallback(async () => {
  if (!recorderSessionId) return;
  try {
    await stopRecording(recorderSessionId);
    setRecorderStatus('stopped');
    setRecorderSessionId(null);
    // Сразу запрашиваем отчёт
    const report = await analyzeLastRecording(recorderOffer);
    setRecorderReport(report);
  } catch (err) {
    setRecorderError(err.message || 'Не удалось остановить запись');
  }
}, [recorderSessionId, recorderOffer]);
```

- [ ] **Шаг 2.5: Добавить JSX для submodule "record"**

В блоке рендера submodule'ов найти место где рендерится `activeSubmodule.id === 'create'` и добавить после:

```jsx
{activeSubmodule.id === 'record' && (
  <div className="flex flex-col gap-4">
    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">Оффер</label>
      <select
        className={INPUT_BASE_CLASS}
        value={recorderOffer}
        onChange={(e) => setRecorderOffer(e.target.value)}
        disabled={recorderStatus === 'recording'}
      >
        <option value="">— выберите оффер —</option>
        {sortedOffers.map((offer) => (
          <option key={offer.code} value={offer.code}>
            {offer.code}{offer.is_active ? '' : ' (неактивен)'}
          </option>
        ))}
      </select>
    </div>

    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">CDP URL браузера</label>
      <input
        className={INPUT_BASE_CLASS}
        value={recorderCdpUrl}
        onChange={(e) => setRecorderCdpUrl(e.target.value)}
        disabled={recorderStatus === 'recording'}
        placeholder="ws://localhost:9222"
      />
    </div>

    {recorderError && (
      <p className="text-sm text-red-500">{recorderError}</p>
    )}

    <div className="flex gap-3">
      {recorderStatus !== 'recording' ? (
        <button
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          onClick={handleStartRecording}
          disabled={offersLoading || !recorderOffer}
        >
          Начать запись
        </button>
      ) : (
        <button
          className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white"
          onClick={handleStopRecording}
        >
          ⏹ Остановить и проанализировать
        </button>
      )}
    </div>

    {recorderStatus === 'recording' && (
      <div className="flex items-center gap-2 rounded-md border border-yellow-400 bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
        <span className="animate-pulse">●</span>
        Запись активна — выполните действия в Ads Manager
      </div>
    )}

    {recorderReport && (
      <div className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4">
        <h3 className="text-sm font-semibold text-primary">Отчёт анализа</h3>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <span className="text-secondary">Всего событий:</span>
          <span className="font-medium">{recorderReport.total_events}</span>
          {Object.entries(recorderReport.by_type || {}).map(([type, count]) => (
            <>
              <span key={type + '_label'} className="text-secondary capitalize">{type}:</span>
              <span key={type + '_val'} className="font-medium">{count}</span>
            </>
          ))}
          <span className="text-secondary">Стабильных элементов:</span>
          <span className="font-medium text-green-600">{recorderReport.stable_selectors?.length ?? 0}</span>
          <span className="text-secondary">Ненадёжных элементов:</span>
          <span className="font-medium text-yellow-600">{recorderReport.fragile_selectors?.length ?? 0}</span>
        </div>
        {recorderReport.recommendations?.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-secondary uppercase tracking-wide">Рекомендации</p>
            {recorderReport.recommendations.map((rec, i) => (
              <p key={i} className="text-sm text-primary">{rec}</p>
            ))}
          </div>
        )}
        {recorderReport.steps_summary?.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-secondary uppercase tracking-wide">Шаги ({recorderReport.steps_summary.length})</p>
            <div className="max-h-48 overflow-y-auto rounded border border-border bg-base p-2">
              {recorderReport.steps_summary.map((step) => (
                <div key={step.step} className="flex gap-2 py-1 text-xs border-b border-border last:border-0">
                  <span className="w-6 shrink-0 text-secondary">{step.step}.</span>
                  <span className="font-mono text-accent">{step.type}</span>
                  <span className="text-primary truncate">{step.text || step.value || '—'}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    )}
  </div>
)}
```

- [ ] **Шаг 2.6: Проверить что frontend собирается**

```bash
cd frontend && npm run build 2>&1 | tail -5
```
Ожидаем: без ошибок

- [ ] **Шаг 2.7: Коммит**

```bash
git add frontend/src/pages/ScriptsPage.jsx
git commit -m "feat: campaign_recorder — UI запись/стоп/отчёт на ScriptsPage"
```

---

### Task 3: Ручная проверка в браузере

- [ ] **Шаг 3.1: Запустить API и frontend**

```bash
# Терминал 1
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload

# Терминал 2
cd frontend && npm run dev
```

- [ ] **Шаг 3.2: Открыть Scripts → Кампании → Запись сессии**

Убедиться что:
- Выпадашка офферов загружается
- Поле CDP URL заполнено по умолчанию `ws://localhost:9222`
- Кнопка "Начать запись" активна при выбранном оффере
- При клике "Начать запись" без Vision — появляется ошибка (не крэш)

- [ ] **Шаг 3.3: Коммит (если были мелкие правки)**

```bash
git add -p
git commit -m "fix: campaign_recorder UI — мелкие правки после ручной проверки"
```
