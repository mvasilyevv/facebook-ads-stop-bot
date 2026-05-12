# Campaign Creator — Фаза 2B: Фронтенд

> **Ждёт:** Фаза 2A (бэкенд роутер `/api/campaign-creator/*`).

**Цель:** Добавить submodule "Автосоздание" на страницу Scripts — запуск задачи, прогресс шагов, кнопка подтверждения на checkpoint'ах.

**Стек:** React 19, Vite, polling через `setInterval`.

---

## Файловая карта

| Действие | Файл |
|----------|------|
| Изменить | `frontend/src/api.js` |
| Изменить | `frontend/src/pages/ScriptsPage.jsx` |

---

### Task 1: API-функции campaign_creator

- [ ] **Шаг 1.1: Добавить в `frontend/src/api.js`**

```js
// --- Campaign Creator ---
export const startCampaignCreator = (data) =>
  request('/campaign-creator/start', {
    method: 'POST',
    body: JSON.stringify(data),
  });

export const confirmCampaignCheckpoint = (taskId) =>
  request(`/campaign-creator/${taskId}/confirm`, { method: 'POST' });

export const getCampaignCreatorStatus = (taskId) =>
  request(`/campaign-creator/${taskId}/status`);
```

- [ ] **Шаг 1.2: Коммит**

```bash
git add frontend/src/api.js
git commit -m "feat: campaign_creator — API-функции во frontend"
```

---

### Task 2: Submodule "Автосоздание" в ScriptsPage

- [ ] **Шаг 2.1: Добавить submodule в `SCRIPT_MODULES`**

Изменить секцию `campaigns`:

```js
{
  id: 'campaigns',
  label: 'Кампании',
  submodules: [
    { id: 'create', label: 'Создание из папки' },
    { id: 'record', label: 'Запись сессии' },
    { id: 'autocreate', label: 'Автосоздание' },
  ],
},
```

- [ ] **Шаг 2.2: Импорт новых API-функций**

```js
import {
  // ... существующее ...
  startCampaignCreator,
  confirmCampaignCheckpoint,
  getCampaignCreatorStatus,
} from '../api';
```

- [ ] **Шаг 2.3: State для autocreate**

После существующих `useState`:

```js
const [autoOffer, setAutoOffer] = useState('');
const [autoFolder, setAutoFolder] = useState('');
const [autoCabinetId, setAutoCabinetId] = useState('');
const [autoCdpUrl, setAutoCdpUrl] = useState('ws://localhost:9222');
const [autoTask, setAutoTask] = useState(null);
const [autoError, setAutoError] = useState('');
const [autoFolders, setAutoFolders] = useState([]);
```

- [ ] **Шаг 2.4: Загрузить папки креативов при входе в submodule**

```js
useEffect(() => {
  if (activeSubmodule.id !== 'autocreate') return;
  getCampaignCreativeFolders()
    .then((data) => setAutoFolders(Array.isArray(data) ? data.filter((f) => f.is_valid) : []))
    .catch(() => setAutoFolders([]));
}, [activeSubmodule.id]);
```

- [ ] **Шаг 2.5: Polling статуса задачи**

```js
useEffect(() => {
  if (!autoTask?.id) return;
  if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(autoTask.status)) return;

  const timer = setInterval(async () => {
    try {
      const updated = await getCampaignCreatorStatus(autoTask.id);
      setAutoTask(updated);
    } catch (err) {
      setAutoError(err.message || 'Ошибка опроса статуса');
    }
  }, 2000);
  return () => clearInterval(timer);
}, [autoTask?.id, autoTask?.status]);
```

- [ ] **Шаг 2.6: Обработчики**

```js
const handleStartAutoCreate = useCallback(async () => {
  setAutoError('');
  if (!autoOffer || !autoFolder || !autoCabinetId) {
    setAutoError('Заполните оффер, папку и ID кабинета');
    return;
  }
  try {
    const task = await startCampaignCreator({
      offer_code: autoOffer,
      creative_folder: autoFolder,
      cabinet_id: autoCabinetId,
      cdp_url: autoCdpUrl,
    });
    setAutoTask(task);
  } catch (err) {
    setAutoError(err.message || 'Не удалось запустить');
  }
}, [autoOffer, autoFolder, autoCabinetId, autoCdpUrl]);

const handleConfirm = useCallback(async () => {
  if (!autoTask?.id) return;
  try {
    const updated = await confirmCampaignCheckpoint(autoTask.id);
    setAutoTask(updated);
  } catch (err) {
    setAutoError(err.message || 'Не удалось подтвердить');
  }
}, [autoTask?.id]);
```

- [ ] **Шаг 2.7: JSX**

Добавить в блок рендера submodule'ов:

```jsx
{activeSubmodule.id === 'autocreate' && (
  <div className="flex flex-col gap-4">
    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">Оффер</label>
      <select
        className={INPUT_BASE_CLASS}
        value={autoOffer}
        onChange={(e) => setAutoOffer(e.target.value)}
        disabled={!!autoTask && autoTask.status !== 'SUCCEEDED' && autoTask.status !== 'FAILED'}
      >
        <option value="">— выберите оффер —</option>
        {sortedOffers.map((o) => (
          <option key={o.code} value={o.code}>{o.code}</option>
        ))}
      </select>
    </div>

    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">Папка креативов</label>
      <select
        className={INPUT_BASE_CLASS}
        value={autoFolder}
        onChange={(e) => setAutoFolder(e.target.value)}
      >
        <option value="">— выберите папку —</option>
        {autoFolders.map((f) => (
          <option key={f.name} value={f.name}>
            {f.name} ({f.adset_count} адсетов × {f.creative_count})
          </option>
        ))}
      </select>
    </div>

    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">ID кабинета</label>
      <input
        className={INPUT_BASE_CLASS}
        value={autoCabinetId}
        onChange={(e) => setAutoCabinetId(e.target.value)}
        placeholder="например, act_1234567890"
      />
    </div>

    <div className="flex flex-col gap-2">
      <label className="text-sm font-medium text-secondary">CDP URL браузера</label>
      <input
        className={INPUT_BASE_CLASS}
        value={autoCdpUrl}
        onChange={(e) => setAutoCdpUrl(e.target.value)}
      />
    </div>

    {autoError && <p className="text-sm text-red-500">{autoError}</p>}

    {!autoTask && (
      <button
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        onClick={handleStartAutoCreate}
        disabled={!autoOffer || !autoFolder || !autoCabinetId}
      >
        Создать автоматически
      </button>
    )}

    {autoTask && (
      <div className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-primary">
            Задача {autoTask.id.slice(0, 8)}...
          </h3>
          <span className={`text-xs px-2 py-1 rounded font-medium ${
            autoTask.status === 'SUCCEEDED' ? 'bg-green-100 text-green-800' :
            autoTask.status === 'FAILED' ? 'bg-red-100 text-red-800' :
            autoTask.status === 'WAITING_CONFIRMATION' ? 'bg-yellow-100 text-yellow-800' :
            'bg-blue-100 text-blue-800'
          }`}>
            {autoTask.status}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-sm">
          <span className="text-secondary">Кампания:</span>
          <span className="font-medium">{autoTask.campaign_name || '—'}</span>
          <span className="text-secondary">Текущий шаг:</span>
          <span className="font-medium">{autoTask.current_step || '—'}</span>
        </div>

        {autoTask.error_message && (
          <div className="rounded bg-red-50 border border-red-200 p-2 text-xs text-red-800">
            {autoTask.error_message}
          </div>
        )}

        {autoTask.status === 'WAITING_CONFIRMATION' && (
          <div className="flex flex-col gap-2 rounded-md border border-yellow-400 bg-yellow-50 p-3">
            <p className="text-sm text-yellow-900">
              ⏸ Шаг <strong>{autoTask.current_step}</strong> завершён.
              Проверь результат в браузере и подтверди продолжение.
            </p>
            {autoTask.checkpoint_data && (
              <pre className="text-xs bg-white border border-yellow-200 rounded p-2 overflow-auto max-h-32">
                {JSON.stringify(autoTask.checkpoint_data, null, 2)}
              </pre>
            )}
            <button
              className="rounded-md bg-yellow-600 px-4 py-2 text-sm font-medium text-white self-start"
              onClick={handleConfirm}
            >
              ✓ Подтвердить и продолжить
            </button>
          </div>
        )}

        {['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(autoTask.status) && (
          <button
            className="rounded-md border border-border px-3 py-1 text-sm self-start"
            onClick={() => { setAutoTask(null); setAutoError(''); }}
          >
            Закрыть и начать новую
          </button>
        )}
      </div>
    )}
  </div>
)}
```

- [ ] **Шаг 2.8: Проверить билд**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Шаг 2.9: Коммит**

```bash
git add frontend/src/pages/ScriptsPage.jsx
git commit -m "feat: campaign_creator — UI автосоздания с checkpoint-подтверждениями"
```

---

### Task 3: Ручная проверка

- [ ] **Шаг 3.1: Запустить API + frontend**

```bash
./run.sh
```

- [ ] **Шаг 3.2: Scripts → Кампании → Автосоздание**

Проверить:
- Оффер, папка, кабинет, CDP URL — все поля заполняются
- Кнопка "Создать автоматически" создаёт задачу
- Статус обновляется каждые 2 секунды
- При `WAITING_CONFIRMATION` появляется жёлтый блок с кнопкой подтверждения
- При `FAILED` показывается ошибка
- При `SUCCEEDED` можно закрыть и начать новую

- [ ] **Шаг 3.3: Финальный коммит**

```bash
git add -p
git commit -m "fix: campaign_creator UI — правки после ручной проверки"
```
