# panel-auth — стилизованный login-gate для app.adpulse.su

Заменяет Caddy `basic_auth` на нормальную форму логина + долгоживущую cookie-сессию.

## Зачем
- Нативный popup `basic_auth` нельзя стилизовать и он перепрашивает пароль после
  чистки кэша. `panel-auth` даёт стилизованную HTML-форму + подписанную HttpOnly
  cookie на 30 дней (вход раз в месяц).
- Один общий пароль. bcrypt-хэш **переиспользует существующий** из Caddyfile —
  открытый пароль нигде не хранится.

## Архитектура
- Лёгкий stdlib-сервис (`app.py`, только stdlib + `bcrypt`) на `127.0.0.1:8090`,
  под systemd (рядом с caddy/novnc).
- Маршруты: `GET /login` (форма), `POST /login` (проверка пароля → Set-Cookie 30д →
  302 на `/`), `GET /verify` (forward_auth-таргет: 200 если cookie валидна, иначе
  302 на `/login`), `GET /logout`, `GET /health`.
- Caddy на `app.adpulse.su`: `basic_auth` → `forward_auth localhost:8090 { uri /verify }`;
  `/login`,`/logout` идут в обход (иначе петля). После авторизации — `/vnc/*`→:6080,
  остальное→:8080.

## Установка на хосте (62.60.150.133)
```bash
mkdir -p /opt/panel-auth
python3 -m venv /opt/panel-auth/venv
/opt/panel-auth/venv/bin/pip install --quiet bcrypt
cp app.py /opt/panel-auth/app.py

# .env: переиспользуем bcrypt-хэш из Caddyfile + случайный секрет для подписи cookie
cat > /opt/panel-auth/.env <<EOF
PANEL_AUTH_PORT=8090
PANEL_BCRYPT_HASH='<bcrypt-хэш из Caddyfile, в одинарных кавычках>'
PANEL_AUTH_SECRET='<openssl rand -hex 32>'
EOF
chmod 600 /opt/panel-auth/.env

cp panel-auth.service /etc/systemd/system/panel-auth.service
systemctl daemon-reload
systemctl enable --now panel-auth.service
```

Проверка сервиса (до правки Caddy):
```bash
curl -s localhost:8090/health                 # ok
curl -si localhost:8090/verify | head -1      # 302 (нет cookie)
curl -si -X POST localhost:8090/login -d 'password=ВЕРНЫЙ' | grep -i set-cookie
```

## Переключение Caddy
В блоке `app.adpulse.su` заменить `basic_auth {...}` на:
```
    @public path /login /logout
    handle @public { reverse_proxy localhost:8090 }
    handle {
        forward_auth localhost:8090 { uri /verify }
        handle_path /vnc/* { reverse_proxy localhost:6080 }
        handle { reverse_proxy localhost:8080 }
    }
```
Затем: `caddy validate --config /etc/caddy/Caddyfile` → `systemctl reload caddy`.

## Откат
```bash
cp /etc/caddy/Caddyfile.bak.<дата> /etc/caddy/Caddyfile
systemctl reload caddy
```
(Бэкап Caddyfile делается перед правкой.)

## Смена пароля
`caddy hash-password` → новый bcrypt-хэш → заменить `PANEL_BCRYPT_HASH` в
`/opt/panel-auth/.env` → `systemctl restart panel-auth`.
