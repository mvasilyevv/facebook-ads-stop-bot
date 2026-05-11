import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  buildCampaignCreatePlan,
  createCreativeUniquifyJob,
  getCampaignCreativeFolders,
  getOffers,
  openCreativeOutputFolder,
  startRecording,
  stopRecording,
  analyzeLastRecording,
} from '../api';

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const INPUT_BASE_CLASS = 'w-full rounded-md border border-border bg-base px-3 py-2 text-sm text-primary outline-none focus:border-accent disabled:opacity-60';
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

function formatBytes(value) {
  if (!Number.isFinite(value)) return '0 Б';
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`;
  return `${(value / 1024 / 1024).toFixed(1)} МБ`;
}

function normalizeFiles(fileList) {
  return Array.from(fileList || []).filter((file) =>
    ACCEPTED_TYPES.includes(file.type) || /\.(jpe?g|png|webp)$/i.test(file.name),
  );
}

function sortOffers(offers) {
  return [...offers].sort((a, b) => {
    if (Boolean(a.is_active) !== Boolean(b.is_active)) return a.is_active ? -1 : 1;
    return String(a.code || '').localeCompare(String(b.code || ''), 'ru');
  });
}

export default function ScriptsPage() {
  const [activeModuleId, setActiveModuleId] = useState('creatives');
  const [activeSubmoduleId, setActiveSubmoduleId] = useState('uniquify');
  const [offers, setOffers] = useState([]);
  const [offersLoading, setOffersLoading] = useState(true);
  const [offersError, setOffersError] = useState('');

  const [recorderOffer, setRecorderOffer] = useState('');
  const [recorderCdpUrl, setRecorderCdpUrl] = useState('ws://localhost:9222');
  const [recorderSessionId, setRecorderSessionId] = useState(null);
  const [recorderStatus, setRecorderStatus] = useState('idle');
  const [recorderReport, setRecorderReport] = useState(null);
  const [recorderError, setRecorderError] = useState('');

  const activeModule = SCRIPT_MODULES.find((module) => module.id === activeModuleId) || SCRIPT_MODULES[0];
  const activeSubmodule =
    activeModule.submodules.find((submodule) => submodule.id === activeSubmoduleId) ||
    activeModule.submodules[0];
  const sortedOffers = useMemo(() => sortOffers(offers), [offers]);

  useEffect(() => {
    let cancelled = false;

    getOffers()
      .then((data) => {
        if (cancelled) return;
        setOffers(Array.isArray(data) ? data.filter((offer) => offer?.code) : []);
        setOffersError('');
      })
      .catch((err) => {
        if (cancelled) return;
        setOffers([]);
        setOffersError(err.message || 'Не удалось загрузить офферы');
      })
      .finally(() => {
        if (!cancelled) setOffersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const selectModule = (module) => {
    setActiveModuleId(module.id);
    setActiveSubmoduleId(module.submodules[0]?.id || '');
  };

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
      const report = await analyzeLastRecording(recorderOffer);
      setRecorderReport(report);
    } catch (err) {
      setRecorderError(err.message || 'Не удалось остановить запись');
    }
  }, [recorderSessionId, recorderOffer]);

  return (
    <div className="space-y-md animate-fade-in">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-lg text-primary">Скрипты</h1>
          <p className="text-sm text-muted">Модульные сценарии для подготовки креативов и кампаний</p>
        </div>
        <div className="text-xs text-muted">
          /Users/markvasilev/Documents/FB_Agent_Creo
        </div>
      </div>

      <div className="grid gap-md xl:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="panel overflow-hidden">
          <div className="border-b border-border px-4 py-3">
            <div className="text-sm font-medium text-primary">Модули</div>
          </div>
          <div className="space-y-1 p-2">
            {SCRIPT_MODULES.map((module) => (
              <button
                key={module.id}
                type="button"
                className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                  module.id === activeModuleId
                    ? 'bg-accent-muted text-primary'
                    : 'text-secondary hover:bg-elevated hover:text-primary'
                }`}
                onClick={() => selectModule(module)}
              >
                {module.label}
              </button>
            ))}
          </div>

          <div className="border-t border-border px-4 py-3">
            <div className="text-xs uppercase text-muted">Подмодули</div>
          </div>
          <div className="space-y-1 p-2 pt-0">
            {activeModule.submodules.map((submodule) => (
              <button
                key={submodule.id}
                type="button"
                className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                  submodule.id === activeSubmodule.id
                    ? 'bg-elevated text-primary'
                    : 'text-secondary hover:bg-elevated hover:text-primary'
                }`}
                onClick={() => setActiveSubmoduleId(submodule.id)}
              >
                {submodule.label}
              </button>
            ))}
          </div>
        </aside>

        <main>
          {activeModuleId === 'creatives' && activeSubmodule.id === 'uniquify' && (
            <CreativeUniquifyScript
              offers={sortedOffers}
              offersLoading={offersLoading}
              offersError={offersError}
            />
          )}
          {activeModuleId === 'campaigns' && activeSubmodule.id === 'create' && (
            <CampaignCreateScript
              offers={sortedOffers}
              offersLoading={offersLoading}
              offersError={offersError}
            />
          )}
          {activeModuleId === 'campaigns' && activeSubmodule.id === 'record' && (
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
                    Остановить и проанализировать
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
        </main>
      </div>
    </div>
  );
}

function CreativeUniquifyScript({ offers, offersLoading, offersError }) {
  const inputRef = useRef(null);
  const [offerName, setOfferName] = useState('');
  const [copies, setCopies] = useState(3);
  const [files, setFiles] = useState([]);
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const plannedFiles = useMemo(() => files.length * Number(copies || 0), [files.length, copies]);

  useEffect(() => {
    setOfferName((current) => {
      if (offers.some((offer) => offer.code === current)) return current;
      return offers.find((offer) => offer.is_active)?.code || offers[0]?.code || '';
    });
  }, [offers]);

  const addFiles = (fileList) => {
    const nextFiles = normalizeFiles(fileList);
    setFiles((current) => [...current, ...nextFiles]);
    setResult(null);
    if (nextFiles.length === 0 && fileList?.length) {
      setError('Поддерживаются только JPEG, PNG и WEBP');
    } else {
      setError('');
    }
  };

  const removeFile = (index) => {
    setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setResult(null);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setDragActive(false);
    addFiles(event.dataTransfer.files);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setResult(null);

    if (!offerName.trim()) {
      setError('Выберите оффер');
      return;
    }
    if (offersLoading) {
      setError('Дождитесь загрузки офферов');
      return;
    }
    if (offers.length === 0) {
      setError('В проекте нет офферов для выбора');
      return;
    }
    if (files.length === 0) {
      setError('Загрузите хотя бы один креатив');
      return;
    }
    if (Number(copies) < 1) {
      setError('Количество копий должно быть не меньше 1');
      return;
    }

    setLoading(true);
    try {
      const response = await createCreativeUniquifyJob({
        offerName: offerName.trim(),
        copies: Number(copies),
        files,
      });
      setResult(response);
    } catch (err) {
      setError(err.message || 'Не удалось уникализировать креативы');
    } finally {
      setLoading(false);
    }
  };

  const handleOpenFolder = async () => {
    if (!result?.iteration_dir) return;
    setOpening(true);
    setError('');
    try {
      await openCreativeOutputFolder(result.iteration_dir);
    } catch (err) {
      setError(err.message || 'Не удалось открыть папку');
    } finally {
      setOpening(false);
    }
  };

  return (
    <form className="grid gap-md xl:grid-cols-[minmax(0,1fr)_360px]" onSubmit={handleSubmit}>
      <section className="panel overflow-hidden">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-medium text-primary">Уникализация креативов</h2>
        </div>

        <div className="space-y-md p-4">
          <div
            className={`
              flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-md border border-dashed px-4 py-8 text-center
              ${dragActive ? 'border-accent bg-accent-muted' : 'border-border bg-base/40 hover:border-border-hover'}
            `}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click();
            }}
          >
            <input
              ref={inputRef}
              className="hidden"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              onChange={(event) => addFiles(event.target.files)}
            />
            <UploadIcon />
            <div className="mt-3 text-sm font-medium text-primary">Загрузить изображения</div>
            <div className="mt-1 text-xs text-muted">JPEG, PNG или WEBP</div>
          </div>

          {files.length > 0 && (
            <div className="overflow-hidden rounded-md border border-border">
              <div className="grid grid-cols-[1fr_92px_40px] border-b border-border px-3 py-2 text-2xs uppercase text-muted">
                <span>Файл</span>
                <span className="text-right">Размер</span>
                <span />
              </div>
              <div className="divide-y divide-border">
                {files.map((file, index) => (
                  <div
                    key={`${file.name}:${file.size}:${index}`}
                    className="grid grid-cols-[minmax(0,1fr)_92px_40px] items-center gap-2 px-3 py-2"
                  >
                    <span className="truncate text-sm text-primary">{file.name}</span>
                    <span className="text-right font-mono text-xs text-muted">
                      {formatBytes(file.size)}
                    </span>
                    <button
                      type="button"
                      className="rounded-md px-2 py-1 text-muted hover:bg-elevated hover:text-primary"
                      onClick={() => removeFile(index)}
                      aria-label={`Удалить ${file.name}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      <aside className="space-y-md">
        <section className="panel p-4">
          <div className="space-y-md">
            <label className="block">
              <span className="mb-1 block text-xs text-muted">Оффер</span>
              <select
                className="w-full rounded-md border border-border bg-base px-3 py-2 text-sm text-primary outline-none focus:border-accent"
                value={offerName}
                onChange={(event) => setOfferName(event.target.value)}
                disabled={offersLoading || offers.length === 0}
              >
                <option value="">
                  {offersLoading ? 'Загрузка офферов...' : 'Выберите оффер'}
                </option>
                {offers.map((offer) => (
                  <option key={offer.id || offer.code} value={offer.code}>
                    {offer.code}{offer.is_active ? '' : ' · выкл.'}
                  </option>
                ))}
              </select>
              {offersError && (
                <span className="mt-1 block text-xs text-danger">{offersError}</span>
              )}
            </label>

            <label className="block">
              <span className="mb-1 block text-xs text-muted">Копий на каждый креатив</span>
              <input
                className="w-full rounded-md border border-border bg-base px-3 py-2 text-sm text-primary outline-none focus:border-accent"
                type="number"
                min="1"
                max="50"
                value={copies}
                onChange={(event) => setCopies(event.target.value)}
              />
            </label>

            <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-base/40 p-3">
              <Metric label="Креативов" value={files.length} />
              <Metric label="JPEG на выходе" value={plannedFiles} />
            </div>

            <button
              type="submit"
              className="btn-primary flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading || offersLoading || offers.length === 0}
            >
              <SparkIcon />
              {loading ? 'Обработка...' : 'Уникализировать'}
            </button>
          </div>
        </section>

        {error && <ErrorBox message={error} />}

        {result && (
          <section className="panel p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-primary">Готово</div>
                <div className="mt-1 text-xs text-muted">
                  Создано файлов: {result.files?.length || 0}
                </div>
              </div>
              <span className="badge-success">JPEG</span>
            </div>
            <div className="mt-3 break-all rounded-md bg-base px-3 py-2 font-mono text-xs text-secondary">
              {result.iteration_dir}
            </div>
            <button
              type="button"
              className="btn-secondary mt-3 flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              onClick={handleOpenFolder}
              disabled={opening}
            >
              <FolderIcon />
              {opening ? 'Открываю...' : 'Открыть папку'}
            </button>
          </section>
        )}
      </aside>
    </form>
  );
}

function CampaignCreateScript({ offers, offersLoading, offersError }) {
  const [folders, setFolders] = useState([]);
  const [foldersLoading, setFoldersLoading] = useState(true);
  const [foldersRefreshing, setFoldersRefreshing] = useState(false);
  const [foldersError, setFoldersError] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState(null);
  const [copyStatus, setCopyStatus] = useState('');
  const [form, setForm] = useState({
    offer_code: '',
    creative_folder_name: '',
    cabinet_id: '1472252497899089',
  });

  const selectedOffer = offers.find((offer) => offer.code === form.offer_code);
  const selectedFolder = folders.find((folder) => folder.name === form.creative_folder_name);
  const foldersMountedRef = useRef(true);

  const loadCampaignFolders = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setFoldersRefreshing(true);
    try {
      const data = await getCampaignCreativeFolders();
      if (!foldersMountedRef.current) return;
      const nextFolders = Array.isArray(data) ? data : [];
      setFolders(nextFolders);
      setFoldersError('');
      setForm((current) => ({
        ...current,
        creative_folder_name:
          current.creative_folder_name && nextFolders.some((folder) => folder.name === current.creative_folder_name)
            ? current.creative_folder_name
            : nextFolders.find((folder) => folder.is_valid !== false)?.name || nextFolders[0]?.name || '',
      }));
    } catch (err) {
      if (!foldersMountedRef.current) return;
      setFoldersError(err.message || 'Не удалось обновить папки креативов');
    } finally {
      if (!foldersMountedRef.current) return;
      setFoldersLoading(false);
      if (!silent) setFoldersRefreshing(false);
    }
  }, []);

  useEffect(() => {
    foldersMountedRef.current = true;
    loadCampaignFolders();

    const intervalId = window.setInterval(() => {
      if (!document.hidden) {
        loadCampaignFolders({ silent: true });
      }
    }, 5000);
    const handleFocus = () => loadCampaignFolders({ silent: true });
    window.addEventListener('focus', handleFocus);

    return () => {
      foldersMountedRef.current = false;
      window.clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
    };
  }, [loadCampaignFolders]);

  useEffect(() => {
    setForm((current) => {
      if (offers.some((offer) => offer.code === current.offer_code)) return current;
      return {
        ...current,
        offer_code: offers.find((offer) => offer.is_active)?.code || offers[0]?.code || '',
      };
    });
  }, [offers]);

  const updateField = (name, value) => {
    setPlan(null);
    setCopyStatus('');
    setError('');
    setForm((current) => ({ ...current, [name]: value }));
  };

  const buildPlan = async (event) => {
    event.preventDefault();
    setPlan(null);
    setCopyStatus('');
    setError('');

    if (!form.offer_code) {
      setError('Выберите оффер');
      return;
    }
    if (!selectedOffer?.country_name) {
      setError('У выбранного оффера не указана страна');
      return;
    }
    if (!form.creative_folder_name) {
      setError('Выберите папку креативов');
      return;
    }
    if (selectedFolder?.is_valid === false) {
      setError(selectedFolder.validation_error || 'Выбранная папка не подходит для создания кампании');
      return;
    }

    setLoading(true);
    try {
      const response = await buildCampaignCreatePlan(form);
      setPlan(response);
    } catch (err) {
      setError(err.message || 'Не удалось построить план создания кампании');
    } finally {
      setLoading(false);
    }
  };

  const copyValue = async (value) => {
    const text = String(value || '').trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus('Скопировано');
    } catch {
      setCopyStatus('Не удалось скопировать');
    }
  };

  return (
    <form className="grid gap-md 2xl:grid-cols-[minmax(0,1fr)_420px]" onSubmit={buildPlan}>
      <section className="panel overflow-hidden">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-medium text-primary">Создание кампании из папки</h2>
        </div>
        <div className="grid gap-md p-4 lg:grid-cols-2">
          <Field label="Оффер">
            <select
              className={INPUT_BASE_CLASS}
              value={form.offer_code}
              onChange={(event) => updateField('offer_code', event.target.value)}
              disabled={offersLoading || offers.length === 0}
            >
              <option value="">{offersLoading ? 'Загрузка офферов...' : 'Выберите оффер'}</option>
              {offers.map((offer) => (
                <option key={offer.id || offer.code} value={offer.code}>
                  {offer.code}{offer.country_name ? ` · ${offer.country_name}` : ' · страна не задана'}
                </option>
              ))}
            </select>
            {offersError && <HelpText tone="danger">{offersError}</HelpText>}
          </Field>

          <Field label="Папка креативов">
            <select
              className={INPUT_BASE_CLASS}
              value={form.creative_folder_name}
              onChange={(event) => updateField('creative_folder_name', event.target.value)}
              disabled={foldersLoading || folders.length === 0}
            >
              <option value="">{foldersLoading ? 'Загрузка папок...' : 'Выберите папку'}</option>
              {folders.map((folder) => (
                <option key={folder.path} value={folder.name}>
                  {folder.name}{folder.is_valid === false ? ' · не подходит' : ''}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary mt-2 flex items-center justify-center gap-2 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
              onClick={() => loadCampaignFolders()}
              disabled={foldersRefreshing}
            >
              <RefreshIcon />
              {foldersRefreshing ? 'Обновляю...' : 'Обновить'}
            </button>
            {foldersError && <HelpText tone="danger">{foldersError}</HelpText>}
            {selectedFolder?.is_valid === false && (
              <HelpText tone="danger">
                {selectedFolder.validation_error || 'Папка не подходит для создания кампании'}
              </HelpText>
            )}
          </Field>

          <Field label="ID кабинета">
            <input className={INPUT_BASE_CLASS} value={form.cabinet_id} onChange={(event) => updateField('cabinet_id', event.target.value)} />
          </Field>
        </div>
      </section>

      <aside className="space-y-md">
        <section className="panel p-4">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-base/40 p-3">
              <Metric label="Групп" value={selectedFolder?.adset_count || 0} />
              <Metric label="Объявлений" value={(selectedFolder?.adset_count || 0) * (selectedFolder?.creative_count || 0)} />
            </div>
            <div className="rounded-md border border-border bg-base/40 px-3 py-2">
              <div className="text-2xs uppercase text-muted">Страна оффера</div>
              <div className="mt-1 text-sm text-primary">{selectedOffer?.country_name || 'Не задана'}</div>
            </div>
            <div className="rounded-md border border-border bg-base/40 px-3 py-2">
              <div className="text-2xs uppercase text-muted">Тип медиа</div>
              <div className="mt-1 text-sm text-primary">
                {selectedFolder?.is_valid === false
                  ? 'Не подходит'
                  : selectedFolder?.media_type === 'video'
                    ? 'Видео'
                    : 'Изображения'}
              </div>
            </div>
            <button
              type="submit"
              className="btn-primary flex w-full items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading || offersLoading || foldersLoading}
            >
              <PlanIcon />
              {loading ? 'Собираю план...' : 'Собрать план'}
            </button>
          </div>
        </section>

        {error && <ErrorBox message={error} />}

        {plan && (
          <CampaignManualGuide
            plan={plan}
            copyStatus={copyStatus}
            onCopy={copyValue}
          />
        )}
      </aside>
    </form>
  );
}

function CampaignManualGuide({ plan, copyStatus, onCopy }) {
  const guide = Array.isArray(plan.manual_guide) ? plan.manual_guide : [];

  return (
    <section className="panel overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium text-primary">Ручной помощник</div>
          {copyStatus && <div className="text-xs text-muted">{copyStatus}</div>}
        </div>
      </div>
      <div className="space-y-3 p-4">
        <div className="grid grid-cols-2 gap-2">
          <MetricBox label="Групп" value={plan.adset_count} />
          <MetricBox label="Объявлений" value={plan.ad_count} />
        </div>
        <div className="rounded-md border border-border bg-base/40 px-3 py-2">
          <div className="text-2xs uppercase text-muted">Гео</div>
          <div className="mt-1 text-sm text-primary">
            {plan.location_plan?.add_locations?.join(' + ')}
          </div>
          <div className="mt-1 text-xs text-muted">
            Только {plan.location_plan?.required_location_type}; города и регионы отклоняются
          </div>
        </div>
        <div className="max-h-[720px] space-y-3 overflow-auto pr-1">
          {guide.map((section) => (
            <ManualGuideSection key={section.title} section={section} onCopy={onCopy} />
          ))}
        </div>
      </div>
    </section>
  );
}

function ManualGuideSection({ section, onCopy }) {
  if (section.title === 'Объявления') {
    const groups = groupAdGuideItems(section.items || []);
    return (
      <div className="overflow-hidden rounded-md border border-border">
        <div className="border-b border-border bg-base/40 px-3 py-2 text-2xs uppercase text-muted">
          {section.title}
        </div>
        <div className="space-y-2 p-3">
          {groups.map((group) => (
            <div key={group.title} className="rounded-md border border-border bg-base/40">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-primary">
                {group.title}
              </div>
              <div className="divide-y divide-border">
                {group.items.map((item, index) => (
                  <ManualGuideRow
                    key={`${group.title}:${item.label}:${index}`}
                    item={item}
                    onCopy={onCopy}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (section.title === 'Копии групп') {
    const groups = groupCopiedAdsetGuideItems(section.items || []);
    return (
      <div className="overflow-hidden rounded-md border border-border">
        <div className="border-b border-border bg-base/40 px-3 py-2 text-2xs uppercase text-muted">
          {section.title}
        </div>
        <div className="space-y-3 p-3">
          {groups.map((adset) => (
            <div key={adset.title} className="rounded-md border border-border bg-base/40">
              <div className="border-b border-border px-3 py-2 text-xs font-medium text-primary">
                {adset.title}
              </div>
              {adset.items.length > 0 && (
                <div className="divide-y divide-border">
                  {adset.items.map((item, index) => (
                    <ManualGuideRow
                      key={`${adset.title}:${item.label}:${index}`}
                      item={item}
                      onCopy={onCopy}
                    />
                  ))}
                </div>
              )}
              {adset.ads.length > 0 && (
                <div className="space-y-2 p-3">
                  {adset.ads.map((ad) => (
                    <div key={`${adset.title}:${ad.title}`} className="rounded-md border border-border bg-surface">
                      <div className="border-b border-border px-3 py-2 text-xs font-medium text-primary">
                        {ad.title}
                      </div>
                      <div className="divide-y divide-border">
                        {ad.items.map((item, index) => (
                          <ManualGuideRow
                            key={`${adset.title}:${ad.title}:${item.label}:${index}`}
                            item={item}
                            onCopy={onCopy}
                          />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="border-b border-border bg-base/40 px-3 py-2 text-2xs uppercase text-muted">
        {section.title}
      </div>
      <div className="divide-y divide-border">
        {(section.items || []).map((item, index) => (
          <ManualGuideRow
            key={`${section.title}:${item.label}:${index}`}
            item={item}
            onCopy={onCopy}
          />
        ))}
      </div>
    </div>
  );
}

function ManualGuideRow({ item, onCopy }) {
  return (
    <div className="grid grid-cols-[128px_minmax(0,1fr)] gap-3 px-3 py-2 text-xs">
      <div className="text-muted">{item.label}</div>
      {item.copyable ? (
        <button
          type="button"
          className="min-w-0 break-words text-left font-mono text-primary hover:text-accent"
          onClick={() => onCopy(item.value)}
        >
          {item.value}
        </button>
      ) : (
        <div className="min-w-0 break-words text-secondary">{item.value}</div>
      )}
    </div>
  );
}

function groupAdGuideItems(items) {
  const groups = [];
  const groupByTitle = new Map();

  items.forEach((item) => {
    const match = String(item.label || '').match(/^(Ad \d+):\s*(.+)$/);
    if (!match) {
      const title = 'Объявление';
      if (!groupByTitle.has(title)) {
        const group = { title, items: [] };
        groupByTitle.set(title, group);
        groups.push(group);
      }
      groupByTitle.get(title).items.push(item);
      return;
    }

    const title = match[1];
    if (!groupByTitle.has(title)) {
      const group = { title, items: [] };
      groupByTitle.set(title, group);
      groups.push(group);
    }
    groupByTitle.get(title).items.push({ ...item, label: match[2] });
  });

  return groups;
}

function groupCopiedAdsetGuideItems(items) {
  const groups = [];
  const groupByTitle = new Map();

  const ensureAdset = (title) => {
    if (!groupByTitle.has(title)) {
      const group = { title, items: [], ads: [], adByTitle: new Map() };
      groupByTitle.set(title, group);
      groups.push(group);
    }
    return groupByTitle.get(title);
  };

  const ensureAd = (adset, title) => {
    if (!adset.adByTitle.has(title)) {
      const ad = { title, items: [] };
      adset.adByTitle.set(title, ad);
      adset.ads.push(ad);
    }
    return adset.adByTitle.get(title);
  };

  items.forEach((item) => {
    const adMatch = String(item.label || '').match(/^(Группа \d+),\s*ad\s*(\d+):\s*(.+)$/i);
    if (adMatch) {
      const adset = ensureAdset(adMatch[1]);
      const ad = ensureAd(adset, `Ad ${adMatch[2]}`);
      ad.items.push({ ...item, label: adMatch[3] });
      return;
    }

    const adsetMatch = String(item.label || '').match(/^(Группа \d+):\s*(.+)$/);
    if (adsetMatch) {
      const adset = ensureAdset(adsetMatch[1]);
      adset.items.push({ ...item, label: adsetMatch[2] });
      return;
    }

    const adset = ensureAdset('Копия группы');
    adset.items.push(item);
  });

  return groups.map((group) => {
    const { adByTitle: _adByTitle, ...publicGroup } = group;
    return publicGroup;
  });
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

function HelpText({ children, tone = 'muted' }) {
  const cls = tone === 'danger' ? 'text-danger' : 'text-muted';
  return <span className={`mt-1 block text-xs ${cls}`}>{children}</span>;
}

function ErrorBox({ message }) {
  return (
    <div className="rounded-md border border-danger/30 bg-danger-muted px-4 py-3 text-sm text-danger">
      {message}
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div>
      <div className="font-mono text-lg text-primary">{value}</div>
      <div className="text-2xs uppercase text-muted">{label}</div>
    </div>
  );
}

function MetricBox({ label, value }) {
  return (
    <div className="rounded-md border border-border bg-base/40 px-3 py-2">
      <div className="font-mono text-lg text-primary">{value}</div>
      <div className="text-2xs uppercase text-muted">{label}</div>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" fill="none" className="text-accent">
      <path d="M17 22V8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M11 14L17 8L23 14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8 24V26C8 27.1 8.9 28 10 28H24C25.1 28 26 27.1 26 26V24" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M8 1.5L9.4 5.5L13.5 7L9.4 8.5L8 12.5L6.6 8.5L2.5 7L6.6 5.5L8 1.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M2 4.5C2 3.7 2.7 3 3.5 3H6L7.4 4.5H12.5C13.3 4.5 14 5.2 14 6V11.5C14 12.3 13.3 13 12.5 13H3.5C2.7 13 2 12.3 2 11.5V4.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

function PlanIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M4 3H13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M4 8H13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M4 13H13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M1.8 3H2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M1.8 8H2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M1.8 13H2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M13 5.5A5.2 5.2 0 0 0 3.8 3.8L2.5 5.1" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.5 2.3V5.1H5.3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 10.5A5.2 5.2 0 0 0 12.2 12.2L13.5 10.9" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13.5 13.7V10.9H10.7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
