import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createCreativeUniquifyJob,
  getAdSnapshots,
  getObserverSettings,
  getOffers,
} from './api.js';

// Хелпер: создаёт минимальный объект Response, совместимый с fetch
function makeResponse({ status = 200, body = null, contentType = 'application/json' }) {
  const headers = new Headers({ 'content-type': contentType });
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 401 ? 'Unauthorized' : 'OK',
    headers,
    json: vi.fn().mockResolvedValue(body),
    text: vi.fn().mockResolvedValue(typeof body === 'string' ? body : JSON.stringify(body)),
  };
}

beforeEach(() => {
  global.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('API-клиент: успешные запросы', () => {
  // Сценарий: GET-запрос возвращает корректные данные
  it('успешный GET-запрос возвращает данные из ответа', async () => {
    const mockData = { interval: 30, enabled: true };
    global.fetch.mockResolvedValueOnce(makeResponse({ body: mockData }));

    const result = await getObserverSettings();

    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('/api/settings/observer');
    expect(options.cache).toBe('no-store');
    expect(result).toEqual(mockData);
  });

  // Сценарий: 204 No Content возвращает null без попытки парсить тело
  it('ответ 204 возвращает null', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers({}),
      json: vi.fn(),
      text: vi.fn(),
    });

    const result = await getOffers();
    expect(result).toBeNull();
  });
});

describe('API-клиент: обработка ошибок', () => {
  // Сценарий: сервер вернул 401 — бросаем Error с кодом и деталью в сообщении
  it('статус 401 бросает Error с кодом ответа', async () => {
    const mockResp = () =>
      makeResponse({ status: 401, body: { detail: 'Недействительный API-ключ' } });
    global.fetch.mockResolvedValueOnce(mockResp());

    const err = await getObserverSettings().catch((e) => e);
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toContain('Ошибка API 401');
    expect(err.message).toContain('Недействительный API-ключ');
  });

  // Сценарий: fetch выбрасывает TypeError (нет сети) — ошибка пробрасывается наверх
  it('сетевая ошибка (fetch reject) пробрасывается как есть', async () => {
    global.fetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await expect(getObserverSettings()).rejects.toThrow('Failed to fetch');
  });

  // Сценарий: сервер вернул 500 без JSON-тела — сообщение содержит statusText
  it('ошибка 500 без тела использует statusText', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      headers: new Headers({ 'content-type': 'text/plain' }),
      text: vi.fn().mockResolvedValue(''),
      json: vi.fn(),
    });

    await expect(getObserverSettings()).rejects.toThrow('Ошибка API 500');
  });
});

describe('API-клиент: фильтрация query-параметров', () => {
  // Сценарий: null и undefined не попадают в URL, пустые строки тоже отфильтровываются
  it('null/undefined/пустая строка не включаются в query string', async () => {
    global.fetch.mockResolvedValueOnce(makeResponse({ body: [] }));

    await getAdSnapshots({ offer_id: null, state: undefined, limit: '', page: 1 });

    const [url] = fetch.mock.calls[0];
    expect(url).toContain('page=1');
    expect(url).not.toContain('offer_id');
    expect(url).not.toContain('state');
    expect(url).not.toContain('limit');
  });

  // Сценарий: все параметры заданы — все попадают в URL
  it('все ненулевые параметры формируют правильный query string', async () => {
    global.fetch.mockResolvedValueOnce(makeResponse({ body: [] }));

    await getAdSnapshots({ offer_id: '123', page: 2 });

    const [url] = fetch.mock.calls[0];
    expect(url).toContain('offer_id=123');
    expect(url).toContain('page=2');
  });

  // Сценарий: параметры отсутствуют — URL без знака вопроса
  it('пустые параметры не добавляют "?" в URL', async () => {
    global.fetch.mockResolvedValueOnce(makeResponse({ body: [] }));

    await getAdSnapshots({});

    const [url] = fetch.mock.calls[0];
    expect(url).toBe('/api/dashboard/ads');
  });
});

describe('API-клиент: загрузка файлов', () => {
  // Сценарий: FormData отправляется без ручного Content-Type, чтобы браузер добавил boundary
  it('не выставляет Content-Type для multipart-загрузки', async () => {
    global.fetch.mockResolvedValueOnce(makeResponse({ body: { ok: true } }));
    const file = new globalThis.File(['data'], 'creative.png', { type: 'image/png' });

    await createCreativeUniquifyJob({
      offerName: 'DRC_CR2',
      copies: 2,
      files: [file],
    });

    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('/api/tools/creative-uniquify');
    expect(options.method).toBe('POST');
    expect(options.headers['Content-Type']).toBeUndefined();
    expect(options.body).toBeInstanceOf(globalThis.FormData);
  });
});
