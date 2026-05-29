# Развёртывание FB Stop Bot в Kubernetes (k3s на WSL2)

Это руководство описывает полный процесс настройки single-node k3s кластера на Windows mini-PC через WSL2 и деплой FB Stop Bot.

---

## 1. Установка WSL2 Ubuntu на Windows

1. Открой PowerShell от администратора:
   ```powershell
   wsl --install -d Ubuntu-22.04
   ```
2. Перезагрузи компьютер, создай пользователя Ubuntu при первом запуске.
3. Обнови систему:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

---

## 2. Установка k3s в WSL2

```bash
# Установка k3s (single-node, Traefik включён по умолчанию)
curl -sfL https://get.k3s.io | sh -

# Проверка что k3s запущен
sudo k3s kubectl get nodes
```

**Настройка kubeconfig для текущего пользователя:**
```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
# Заменяем 127.0.0.1 на реальный IP WSL2 (нужно для OpenLens на Windows)
WSL_IP=$(hostname -I | awk '{print $1}')
sed -i "s/127.0.0.1/${WSL_IP}/" ~/.kube/config
echo "kubeconfig настроен, IP WSL2: ${WSL_IP}"
```

---

## 3. Подключение OpenLens на Windows

1. Скачай OpenLens: https://github.com/MuhammedKalkan/OpenLens/releases
2. Скопируй kubeconfig из WSL2 в Windows:
   ```bash
   # Из WSL2 (замени USERNAME на имя Windows-пользователя)
   cp ~/.kube/config /mnt/c/Users/USERNAME/.kube/config
   ```
3. В OpenLens: File → Add Cluster → выбери скопированный kubeconfig.
4. Если подключение не проходит — убедись что IP в kubeconfig соответствует текущему IP WSL2 (он может меняться после перезагрузки).

---

## 4. Установка инструментов в WSL2

```bash
# Docker (если ещё не установлен через Docker Desktop)
sudo apt install -y docker.io
sudo usermod -aG docker $USER
newgrp docker

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Проверка
kubectl version --client
helm version
```

---

## 5. Настройка Postgres на хосте

**Вариант A: Docker Desktop (рекомендуется)**

Postgres запускается через docker-compose из проекта. В WSL2 он будет доступен через `host.docker.internal:5433`.

```bash
# Из корня проекта
docker compose up -d
# Проверка
docker compose ps
```

**Вариант B: PostgreSQL напрямую на Windows**

Установи PostgreSQL 16 на Windows, создай базу данных:
```sql
CREATE DATABASE fb_stop_bot;
CREATE USER fb_stop_bot WITH PASSWORD 'твой_пароль';
GRANT ALL PRIVILEGES ON DATABASE fb_stop_bot TO fb_stop_bot;
```

---

## 6. Настройка Vision (anti-detect браузер)

1. Vision должен быть запущен на хосте Windows (порт 3030).
2. Создай профиль в Vision Dashboard, скопируй Profile ID.
3. Получи X-Token из настроек Vision API.
4. Убедись что Vision доступен из WSL2:
   ```bash
   curl http://host.docker.internal:3030/api/v1/profiles
   ```

---

## 7. Подготовка values-mini-pc.yaml

Отредактируй `helm/fb-stop-bot/values-mini-pc.yaml`:

```yaml
browserAgents:
  - slug: profile-1
    visionProfileId: "ТВОЙ_РЕАЛЬНЫЙ_PROFILE_ID"
    replicas: 1
```

---

## 8. Создание secrets.yaml (НЕ коммитить!)

Скопируй пример и заполни реальными значениями:

```bash
cp helm/fb-stop-bot/secrets.example.yaml helm/fb-stop-bot/secrets.yaml
# Отредактируй helm/fb-stop-bot/secrets.yaml
```

Файл `secrets.yaml` добавлен в `.gitignore` и никогда не попадёт в репозиторий.

---

## 9. Сборка Docker-образов

```bash
# Из корня проекта (в WSL2)
make docker-build

# Или вручную с конкретным тегом
make docker-build IMAGE_TAG=1.0.0
```

---

## 10. Импорт образов в k3s

k3s использует собственное хранилище образов, отдельное от Docker. Нужно явно импортировать:

```bash
make k3s-import
# Или с тегом:
make k3s-import IMAGE_TAG=1.0.0
```

Проверить что образы импортированы:
```bash
sudo k3s ctr images list | grep fb-stop-bot
```

---

## 11. Применение миграций

Миграции применяются **автоматически** как Helm pre-install/pre-upgrade Job при каждом `helm install` или `helm upgrade`.

Если нужно запустить миграции вручную:
```bash
kubectl create job --from=cronjob/fb-stop-bot-migrate manual-migrate -n fb-stop-bot
# Или exec в api pod:
kubectl exec -it -n fb-stop-bot deploy/fb-stop-bot-api -- alembic upgrade head
```

---

## 12. Деплой через Helm

```bash
make helm-install
```

Или вручную:
```bash
helm upgrade --install fb-stop-bot helm/fb-stop-bot \
  -f helm/fb-stop-bot/values.yaml \
  -f helm/fb-stop-bot/values-mini-pc.yaml \
  -f helm/fb-stop-bot/secrets.yaml \
  --namespace fb-stop-bot --create-namespace
```

---

## 13. Проверка деплоя

```bash
# Статус подов
kubectl get pods -n fb-stop-bot

# Все ресурсы
kubectl get all -n fb-stop-bot

# Логи конкретного пода
kubectl logs -n fb-stop-bot deploy/fb-stop-bot-api

# Логи всех подов (стриминг)
make k8s-logs
```

Ожидаемые поды в статусе Running:
- `fb-stop-bot-api-*`
- `fb-stop-bot-observer-*`
- `fb-stop-bot-disable-worker-*`
- `fb-stop-bot-enable-worker-*`
- `fb-stop-bot-enable-recommendation-*`
- `fb-stop-bot-telegram-poller-*`
- `fb-stop-bot-health-watchdog-*`
- `browser-agent-profile-1-*`
- `fb-stop-bot-frontend-*`
- `fb-stop-bot-mini-app-*`

---

## 14. Настройка /etc/hosts на Windows

Добавь в `C:\Windows\System32\drivers\etc\hosts` (от администратора):
```
127.0.0.1 api.fbbot.local
127.0.0.1 app.fbbot.local
127.0.0.1 tma.fbbot.local
```

После этого в браузере Windows будут доступны:
- http://app.fbbot.local — React UI
- http://api.fbbot.local — FastAPI (документация на /docs)
- http://tma.fbbot.local/tma/ — Telegram Mini App

---

## 15. Cloudflare Tunnel для публичного доступа к TMA

Telegram требует HTTPS для Mini App. Используй cloudflared tunnel:

```bash
# Установка cloudflared в WSL2
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Быстрый туннель (временный URL, для тестов)
cloudflared tunnel --url http://tma.fbbot.local

# Постоянный туннель (требует аккаунт Cloudflare)
cloudflared login
cloudflared tunnel create fb-stop-bot-tma
cloudflared tunnel route dns fb-stop-bot-tma tma.yourdomain.com
```

**Альтернатива: ngrok**
```bash
ngrok http http://tma.fbbot.local
```

После получения HTTPS URL — обнови `WEB_APP_URL` в `values-mini-pc.yaml` и выполни `make helm-install`.

---

## Troubleshooting

### Pod в CrashLoopBackOff

```bash
# Смотреть логи упавшего контейнера
kubectl logs -n fb-stop-bot <pod-name> --previous

# Описание пода (events внизу)
kubectl describe pod -n fb-stop-bot <pod-name>
```

Частые причины:
- Неверные секреты (неправильный TELEGRAM_BOT_TOKEN, POSTGRES_PASSWORD)
- Postgres недоступен (проверь `host.docker.internal:5433`)
- Vision недоступен (проверь `host.docker.internal:3030`)

### Образ не найден (ImagePullBackOff)

```bash
# Проверить список образов в k3s
sudo k3s ctr images list | grep fb-stop-bot

# Переимпортировать образы
make k3s-import
```

### Подключиться к поду через shell

```bash
kubectl exec -it -n fb-stop-bot deploy/fb-stop-bot-api -- /bin/bash
```

### Проверить переменные окружения в поде

```bash
kubectl exec -n fb-stop-bot deploy/fb-stop-bot-api -- env | grep -v PASSWORD | grep -v TOKEN
```

### k3s не запускается после перезагрузки WSL2

WSL2 не поднимает systemd автоматически. Запусти:
```bash
sudo systemctl start k3s
# или
sudo k3s server &
```

Добавь в `~/.bashrc` для автозапуска:
```bash
# Автозапуск k3s при старте WSL2
if ! pgrep -x "k3s" > /dev/null; then
    sudo systemctl start k3s 2>/dev/null || true
fi
```

### IP WSL2 изменился после перезагрузки

```bash
WSL_IP=$(hostname -I | awk '{print $1}')
sed -i "s/[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+/${WSL_IP}/" ~/.kube/config
# Скопируй обновлённый kubeconfig в Windows и переподключись в OpenLens
```
