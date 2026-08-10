# MCP-сервер FB Agent — настройка Claude Desktop

Этот документ описывает, как подключить FB Agent к Claude Desktop через
[Model Context Protocol](https://modelcontextprotocol.io) и общаться с ботом
голосом/текстом из десктоп-приложения Anthropic, минуя Telegram.

Скоуп: **только READ_ONLY tools**, content-generation tools и **Resources**
(durable PostgreSQL snapshots). Денежных mutation-tools в MCP-контракте нет.
Транспорт — **stdio** (Claude Desktop сам запускает локальный процесс).

---

## 1. Что вы получите

После настройки в Claude Desktop появится:

- **tools** (только read-only):
  - `get_active_offers`, `get_recent_alerts`, `get_ad_action_status` — read-only из БД
  - `find_ads`, `get_insights`, `get_account_health`, `get_offer_performance` —
    read-only Marketing API (через активную Vision-сессию)
  - `get_tracker_stats` — read-only post-click статистика AdSet.pro (клики/
    регистрации/депозиты FTD/доход/ROI; разрез по event_type или ext_sub1..6).
    Независимо от Vision-сессии — работает, даже когда кабинет недоступен
  - `analyze_creative`, `generate_ad_copy` — генерация контента через LLM
    (без mutations)
- 3 **resources**:
  - `fb-stop-bot://offers` — JSON со списком активных офферов
  - `fb-stop-bot://recent-alerts` — алерты за последние 24 часа
  - `fb-stop-bot://schema-overview` — Markdown-обзор всех tools

Rate-limit: **30 запросов в час** на client_key `mcp:claude-desktop`.

---

## 2. Требования

- macOS, Claude Desktop ≥ 1.0 ([скачать](https://claude.ai/download))
- FB Agent развёрнут локально: Postgres (`docker-compose up -d`) и,
  опционально, `services/browser-agent` для Marketing API tools
- Установлен Python venv: `.venv/bin/python` существует
- Установлена зависимость `mcp`:

```bash
.venv/bin/pip install -e '.[dev]'
```

(Также можно поставить только runtime: `.venv/bin/pip install 'mcp>=1.0.0,<2.0.0'`.)

---

## 3. Конфигурация Claude Desktop

Откройте (или создайте) файл:

```
~/Library/Application Support/Claude/claude_desktop_config.json
```

Добавьте секцию `mcpServers` (пути замените на свои):

```json
{
  "mcpServers": {
    "fb-stop-bot": {
      "command": "/Users/markvasilev/Desktop/FB_Agent/.venv/bin/python",
      "args": ["/Users/markvasilev/Desktop/FB_Agent/run_mcp_server.py"],
      "cwd": "/Users/markvasilev/Desktop/FB_Agent",
      "env": {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5433",
        "POSTGRES_DB": "fb_stop_bot",
        "POSTGRES_USER": "fb_stop_bot",
        "POSTGRES_PASSWORD": "REPLACE_ME",
        "ENCRYPTION_KEY": "REPLACE_ME",
        "ENCRYPTION_KEY_VERIFY": "REPLACE_ME",
        "BROWSER_AGENT_GRPC_HOST": "localhost",
        "BROWSER_AGENT_GRPC_PORT": "50051"
      }
    }
  }
}
```

**Важно**:

- Подставьте реальные значения `POSTGRES_PASSWORD`, `ENCRYPTION_KEY` и
  `ENCRYPTION_KEY_VERIFY` из вашего `.env`.
- `BROWSER_AGENT_GRPC_*` нужен только для Marketing API tools (find_ads,
  get_insights). Если browser-agent не запущен — meta-tools
  вернут ошибку, остальные продолжат работать.
- `ANTHROPIC_API_KEY` для диалога в Claude Desktop НЕ нужен — общение ведёт
  сам Claude Desktop. НО **creative-tools** (`analyze_creative`,
  `generate_ad_copy`) зовут наш LLM-клиент напрямую, поэтому им нужен
  `ANTHROPIC_API_KEY` ИЛИ `OPENAI_API_KEY` (+ base_url) в `env`. Без них эти
  два tool'а вернут «AI не настроен»; read-only tools продолжат работать.
- `ADSETPRO_MCP_KEY` нужен для `get_tracker_stats` (статистика AdSet.pro).

### ⚠️ Предупреждение про секреты

Этот JSON-файл хранит секреты в plaintext на диске — это **требование Claude
Desktop**, обхода нет. Что НЕ делать:

- **Не коммитить** `claude_desktop_config.json` ни в один git-репозиторий.
- **Не публиковать скриншоты** с открытым файлом — в нём может быть случайно
  виден пароль БД.
- **Не шарить файл** через iCloud Drive, Dropbox, Slack-вложения и т.п.
- На общей машине — выставить права доступа: `chmod 600
  ~/Library/Application\ Support/Claude/claude_desktop_config.json`.

После добавления конфигурации **перезапустите Claude Desktop полностью**
(не просто закрыть окно — Quit через ⌘Q). Должна появиться иконка MCP-сервера
в правом нижнем углу окна чата.

---

## 4. Smoke-тест MCP-сервера

Перед подключением Claude Desktop проверьте, что сервер вообще стартует:

```bash
cd /Users/markvasilev/Desktop/FB_Agent
.venv/bin/python run_mcp_server.py
```

Ожидаемое поведение: процесс **зависает**, в stderr печатается:

```
... INFO MCP-сервер 'fb-stop-bot' запущен (transport=stdio)
```

Зависание — это правильно: сервер слушает stdin в ожидании JSON-RPC сообщений
от Claude Desktop. Нажмите Ctrl+C для graceful shutdown.

Если процесс падает на старте — смотрите stderr. Типичные проблемы:

- `ConnectionRefusedError` к Postgres → проверьте `docker-compose ps`
- `ImportError: mcp` → пересоберите окружение: `.venv/bin/pip install -e '.[dev]'`

---

## 5. Как пользоваться

После перезапуска Claude Desktop в новом чате должно быть видно:

- Кнопка-иконка плагина (внизу справа) — клик показывает доступные tools.
- В шапке чата можно перетащить любой `fb-stop-bot://*` resource — он попадёт
  в контекст вопроса.

Примеры запросов:

> Покажи активные офферы — сгруппируй по вертикали.

→ Claude вызовет `get_active_offers` → ответит с группировкой.

Process liveness проверяется в Prometheus/Grafana, а состояние control plane —
в operator snapshot. MCP не поддерживает отдельный liveness-authority.

> Отключи все объявления по офферу DRC_CR2.

→ Write-мутации через MCP отключены — Claude пояснит, что инструмент недоступен
(MCP read-only). Денежные действия выполняются только через operator UI/TMA и
единый `CommandService`, с явным кабинетом, подтверждением и видимым lifecycle.

---

## 6. Что осталось за рамками v1

- **HTTP/SSE transport** (для подключения с iPhone Claude app или через
  remote API gateway) — отдельная работа: нужен FastAPI-роутер + OAuth.
  Сейчас только stdio = только Mac.
- **OAuth для remote MCP** — если когда-то понадобится экспонировать сервер
  не на localhost. Сейчас доверяемся локальной авторизации Claude Desktop.
- **MCP Prompts** — шаблонные «начинки» вопросов (типа `/analyze-offer
  DRC_CR2`). MCP протокол это поддерживает, но v1 пропустили.
- **Cross-account агрегаты** — read tools требуют явный account scope там, где
  смешение кабинетов сделало бы ответ неоднозначным.

---

## 7. Где смотреть код

- `apps/mcp_server/main.py` — Server + регистрация хендлеров
- `apps/mcp_server/context.py` — lifecycle engine/Redis/MetaApiClient
- `apps/mcp_server/tool_adapter.py` — ToolHandler → mcp.types.Tool
- `apps/mcp_server/resources.py` — реализация 4 ресурсов
- `run_mcp_server.py` — entrypoint (stderr-логирование!)
- `tests/unit/test_mcp_*.py`, `tests/integration/test_mcp_*.py` — тесты
