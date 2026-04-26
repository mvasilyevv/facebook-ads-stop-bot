import { useEffect, useMemo, useRef, useState } from 'react';
import { createCreativeUniquifyJob, getOffers, openCreativeOutputFolder } from '../api';

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

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

export default function ScriptsPage() {
  const inputRef = useRef(null);
  const [offerName, setOfferName] = useState('');
  const [copies, setCopies] = useState(3);
  const [files, setFiles] = useState([]);
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [offers, setOffers] = useState([]);
  const [offersLoading, setOffersLoading] = useState(true);
  const [offersError, setOffersError] = useState('');

  const plannedFiles = useMemo(() => files.length * Number(copies || 0), [files.length, copies]);
  const sortedOffers = useMemo(
    () =>
      [...offers].sort((a, b) => {
        if (Boolean(a.is_active) !== Boolean(b.is_active)) return a.is_active ? -1 : 1;
        return String(a.code || '').localeCompare(String(b.code || ''), 'ru');
      }),
    [offers],
  );

  useEffect(() => {
    let cancelled = false;

    getOffers()
      .then((data) => {
        if (cancelled) return;
        const nextOffers = Array.isArray(data) ? data.filter((offer) => offer?.code) : [];
        setOffers(nextOffers);
        setOfferName((current) => {
          if (nextOffers.some((offer) => offer.code === current)) return current;
          return nextOffers.find((offer) => offer.is_active)?.code || nextOffers[0]?.code || '';
        });
        setOffersError('');
      })
      .catch((err) => {
        if (cancelled) return;
        setOffers([]);
        setOfferName('');
        setOffersError(err.message || 'Не удалось загрузить офферы');
      })
      .finally(() => {
        if (!cancelled) setOffersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

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
    if (sortedOffers.length === 0) {
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
    <div className="space-y-md animate-fade-in">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-lg text-primary">Скрипты</h1>
          <p className="text-sm text-muted">Уникализация креативов и раскладка по папкам</p>
        </div>
        <div className="text-xs text-muted">
          /Users/markvasilev/Documents/FB_Agent_Creo
        </div>
      </div>

      <form className="grid gap-md xl:grid-cols-[minmax(0,1fr)_360px]" onSubmit={handleSubmit}>
        <section className="panel overflow-hidden">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-medium text-primary">Креативы</h2>
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
                  disabled={offersLoading || sortedOffers.length === 0}
                >
                  <option value="">
                    {offersLoading ? 'Загрузка офферов...' : 'Выберите оффер'}
                  </option>
                  {sortedOffers.map((offer) => (
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
                disabled={loading || offersLoading || sortedOffers.length === 0}
              >
                <SparkIcon />
                {loading ? 'Обработка...' : 'Уникализировать'}
              </button>
            </div>
          </section>

          {error && (
            <div className="rounded-md border border-danger/30 bg-danger-muted px-4 py-3 text-sm text-danger">
              {error}
            </div>
          )}

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
