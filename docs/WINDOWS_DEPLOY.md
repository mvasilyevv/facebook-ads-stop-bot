# Деплой FB Stop Bot на Windows Server 2022

Переезд с Linux-хоста на Windows Server 2022 (решение владельца 20.06.2026 — Linux-VNC
неюзабелен для ручного доступа; Windows даёт родной плавный RDP + Vision нативно).

## Принцип: гибрид Windows-native + WSL2

Меняется НЕ код, а **размещение** компонентов. Backend остаётся Linux-контейнерами (в WSL2),
на нативный Windows переезжает только GUI-слой (Vision) и его прокси (browser-agent).

| Компонент | Где | Почему |
|---|---|---|
| **Vision** (.exe) | Windows нативно | GUI-приложение; виден через родной RDP; лучший fingerprint |
| **browser-agent** (Node.js, gRPC :50051) | Windows нативно (служба NSSM) | CDP к Vision захардкожен на `127.0.0.1` → обязан быть на одном localhost с Vision |
| **Postgres, Redis** | WSL2 (Docker) | stateful Linux-контейнеры |
| **API + 12 воркеров** | WSL2 (Docker) | наш compose-стек без изменений |

### Сетевые связи
- `browser-agent` (Windows) → Vision API `127.0.0.1:3030` + CDP `127.0.0.1:<port>` — **нативный localhost, работает без патча кода**.
- Воркеры/API (WSL2 Docker) → `browser-agent` (Windows `:50051`) — через `host.docker.internal` или IP Windows-хоста. Уже configurable: `BROWSER_AGENT_HOST` в `.env`.
- Внешний доступ (AdSet.pro postback, API) — Windows Firewall + порт/реверс-прокси.
- Мой доступ + CI — **OpenSSH Server на Windows** (Windows Feature), оттуда `wsl` для Docker-команд.

## Что МЕНЯЕТСЯ в репозитории (минимум)
1. `run.sh` (bash) неприменим на Windows нативно → новый launcher:
   - `scripts/win/start.ps1` (PowerShell) — поднимает: WSL2 `docker compose up -d` (backend) + browser-agent (NSSM) + проверка Vision.
   - Backend внутри WSL2 запускается как на Linux (тот же compose, миграции через migrate-сервис).
2. `.env`: `BROWSER_AGENT_HOST=host.docker.internal` (воркеры в WSL2 Docker → browser-agent на Windows-хосте). `VISION_API_URL=http://127.0.0.1:3030` (browser-agent на Windows рядом с Vision).
3. CI/CD `.github/workflows/deploy.yml` — job `deploy`: SSH на Windows OpenSSH → `wsl -- docker compose pull/up` (backend) + рестарт browser-agent через `nssm restart`. (Linux-Docker-образы из GHCR работают в WSL2 Docker без изменений.)
4. browser-agent: собирается под Node.js Windows тем же `npm ci && npm run build`; ставится как служба через **NSSM** (`nssm install fb-browser-agent`), автозапуск.
5. Vision: автозапуск через Task Scheduler (logon) или ярлык в Startup; первый логин FB — руками через RDP.
6. Автозапуск при ребуте: WSL2 (`wsl --set-default-version 2`, systemd в WSL2 + `docker` enable) + NSSM-службы + Task Scheduler.

## Пошаговая установка (когда Windows готов)
0. Базовая Windows Server 2022: RDP включён (родной), задать пароль, обновления.
1. **OpenSSH Server**: `Add-WindowsCapability -Online -Name OpenSSH.Server` → старт службы (для моего доступа + CI).
2. **WSL2 + Ubuntu**: `wsl --install -d Ubuntu` → включить systemd в `/etc/wsl.conf`.
3. **Docker в WSL2**: Docker CE внутри Ubuntu-WSL2 (или Docker Desktop с WSL2-backend).
4. **Backend**: в WSL2 `git clone` репо в `/opt/fb_agent` (deploy key), `.env`, `docker compose build` (или pull GHCR) → `migrate` → `up -d`. Проверка: `/healthz`, `/readyz`, 12 heartbeat — **как уже отлажено на Linux** (см. накопленные фиксы ниже).
5. **Node.js на Windows** → склонировать/собрать `services/browser-agent` → `nssm install fb-browser-agent` (node dist/index.js, :50051).
6. **Vision .exe** на Windows → залогинить FB-профиль через RDP (плавно) → прописать прокси.
7. `BROWSER_AGENT_HOST=host.docker.internal` в `.env` (WSL2) → перезапуск воркеров → проверить probe канала `meta_api:channel:health`.
8. Firewall: открыть нужные порты (API/postback) наружу; RDP/SSH — ограничить по IP.
9. Автозапуск всего при ребуте.

## Накопленные Docker-фиксы (перенести в git main — пока только на старом Linux-сервере)
Должны попасть в репо ДО первого деплоя на Windows (иначе сборка/миграции упадут так же):
1. `docker/worker-entrypoint.sh` — migrate bootstrap (apply_schema → stamp/upgrade).
2. `docker/Dockerfile.python-base` — `ENV PYTHONPATH=/app`.
3. `migrations/env.py` — URL из `core.config` (не из hardcoded alembic.ini).
4. `uv.lock` — обновлён `uv lock` (был без redis и др.).
5. `docker/Dockerfile.frontend/mini-app` — node:20 → node:22 (pnpm 11.6) — ещё НЕ сделано.

## Что нужно от владельца (когда Windows готов)
- IP + RDP-креды (для ручного доступа к Vision/FB).
- Включить **OpenSSH Server** (или дать мне способ выполнять команды) — иначе я не смогу настраивать backend.
- Резидентный прокси для Vision-профиля (FB).

## Открытые вопросы / риски
- `host.docker.internal` из WSL2-Docker до Windows-хоста: на Docker Desktop работает из коробки; на Docker CE в WSL2 — проверить (возможно нужен IP WSL2-gateway или mirrored networking).
- WSL2 + Docker автозапуск при ребуте Windows без логона — настроить (Task Scheduler запускает `wsl`-сессию).
- Две среды на одной машине (Windows GUI + WSL2) — больше точек отказа; компенсируется мониторингом (health_watchdog + probe уже есть).
