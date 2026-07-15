# Мониторинг FB Agent (Grafana + Prometheus + Loki)

Лёгкий стек наблюдения за сервером и контейнерами. Отдельный compose-проект
(`fb_agent_monitoring`) — не вмешивается в основной стек, можно поднимать/гасить независимо.

## Что показывает

- **Метрики хоста** (node-exporter): аптайм, CPU, RAM, диск, сеть, load average.
- **Метрики контейнеров** (cAdvisor): CPU/RAM по каждому контейнеру.
- **Метрики приложения** (наш `/metrics`): `app_requests_total`, длительности запросов.
- **Логи** (Loki + Promtail): все docker-логи + панель «Ошибки в логах».

Дашборд: **FB Agent — Server Overview** (провижнится автоматически).

## Запуск

```bash
cd /opt/fb-agent/current/deploy/monitoring
cp .env.monitoring.example .env.monitoring
# отредактируй GF_SECURITY_ADMIN_PASSWORD (openssl rand -base64 24)
docker compose -f docker-compose.monitoring.yml up -d
```

Prometheus retention: 15 дней / 2 GB. Loki retention: 7 дней. Логи самого стека
ограничены (json-file 20m×3) — диск не съест.

## Доступ к Grafana

Grafana слушает `127.0.0.1:3000` — наружу не торчит. Два способа открыть:

**1. SSH-туннель (сразу, без DNS):**
```bash
ssh -L 3000:localhost:3000 root@62.60.150.133
# затем открой http://localhost:3000  (admin / пароль из .env.monitoring)
```

**2. Через Caddy на поддомене (удобно, с телефона):**
Добавь DNS A-record `monitor.adpulse.su → 62.60.150.133`, затем в Caddyfile:
```
monitor.adpulse.su {
    reverse_proxy 127.0.0.1:3000
}
```
и `GF_SERVER_ROOT_URL=https://monitor.adpulse.su` в `.env.monitoring`, пересоздать grafana.

## Остановка / обновление

```bash
docker compose -f docker-compose.monitoring.yml down       # остановить (данные в volume сохранятся)
docker compose -f docker-compose.monitoring.yml pull && \
docker compose -f docker-compose.monitoring.yml up -d      # обновить образы
```

## Заметки

- Prometheus подключён к внешней сети `fb_agent_default`, чтобы скрейпить `api:8100/metrics`.
  Если основной стек пересоздаётся с другим именем проекта — поправь `name:` сети в compose.
- Дашборд и datasource провижнятся из `grafana/` — правки в UI не персистят структуру
  (datasource `editable: false`). Меняй JSON/YAML в репозитории.
