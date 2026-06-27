#!/usr/bin/env python3
"""Авто-рефреш Vision X-Token через облачный логин (v1.empr.cloud).

Логинится username+password (2FA на аккаунте отключён) → свежий JWT
(`data.token`; API-логин выдаёт ~годовой токен, UI-токен был 30-дневным) → пишет
VISION_X_TOKEN в /opt/fb_agent/.env (с бэкапом) → перезапускает browser-agent +
поднимает профиль + ensure-cdp + проверяет канал probe'ом.

Зачем: у облака Vision нет refresh-ручки — обновить токен можно только повторным
логином. Раньше протухание токена роняло канал (заливы + авто-стоп) на дни, молча.
Теперь таймер раз в сутки рефрешит токен до истечения — ручных действий ноль.

Запуск (на ХОСТЕ, нужен доступ к systemctl + .env + docker):
  vision-refresh-token.py --if-expiring 5   # рефреш только если до exp < N дней (для таймера)
  vision-refresh-token.py --force           # рефреш всегда (ручной/тест)
  vision-refresh-token.py --force --no-restart  # только обновить .env, без рестарта агента

Креды читаются из .env: VISION_USERNAME, VISION_PASSWORD, VISION_FOLDER_ID,
VISION_PROFILE_ID. Секреты (пароль, токен) в логи НЕ пишутся.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENV_PATH = os.environ.get("FB_AGENT_ENV", "/opt/fb_agent/.env")
CLOUD_AUTH = "https://v1.empr.cloud/api/v1/users/auth"
LOCAL_API = "http://127.0.0.1:3030"


def log(msg: str) -> None:
    print(f"[vision-refresh] {msg}", flush=True)


def read_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v
    return env


def jwt_exp(token: str) -> datetime | None:
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p))["exp"]
        return datetime.fromtimestamp(exp, timezone.utc)
    except Exception:
        return None


def days_left(token: str) -> float:
    exp = jwt_exp(token)
    if not exp:
        return -1.0
    return (exp - datetime.now(timezone.utc)).total_seconds() / 86400.0


def login(username: str, password: str) -> str:
    """POST /users/auth {username, password} → data.token (JWT)."""
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        CLOUD_AUTH, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise RuntimeError(f"логин не прошёл (HTTP {e.code}): {detail}") from e
    token = (data.get("data") or {}).get("token")
    if not isinstance(token, str) or not token.startswith("eyJ"):
        raise RuntimeError(f"в ответе нет data.token: {str(data)[:200]}")
    return token


def update_env_token(path: str, new_token: str) -> None:
    shutil.copy2(path, f"{path}.bak.token-{int(time.time())}")
    lines = open(path).read().splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith("VISION_X_TOKEN="):
            out.append("VISION_X_TOKEN=" + new_token)
            found = True
        else:
            out.append(line)
    if not found:
        out.append("VISION_X_TOKEN=" + new_token)
    open(path, "w").write("\n".join(out) + "\n")


def start_profile(token: str, folder: str, profile: str) -> str:
    url = f"{LOCAL_API}/start/{folder}/{profile}"
    req = urllib.request.Request(url, headers={"X-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:  # noqa: BLE001
        return f"err: {e}"


def ensure_cdp() -> str:
    cmd = [
        "docker",
        "exec",
        "fb_agent-api-1",
        "sh",
        "-c",
        'curl -s --max-time 120 -X POST -H "X-API-Key: $API_KEY" '
        "http://localhost:8100/api/vision/ensure-cdp",
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=140).stdout[:200]
    except Exception as e:  # noqa: BLE001
        return f"err: {e}"


def probe() -> dict:
    code = (
        "import asyncio,os,json\n"
        "from core.meta_api.client import MetaApiClient\n"
        "async def m():\n"
        ' c=MetaApiClient(host=os.environ.get("BROWSER_AGENT_HOST","host.docker.internal"),'
        'port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT","50051")))\n'
        " await c.start(); print(json.dumps(await c.check_health(full_probe=True)))\n"
        "asyncio.run(m())"
    )
    cmd = ["docker", "exec", "fb_agent-meta_api-1", "python", "-c", code]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        return json.loads(out.strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return {"healthy": False, "detail": f"probe err: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Авто-рефреш Vision X-Token")
    ap.add_argument(
        "--if-expiring",
        type=float,
        default=None,
        help="рефреш только если до exp меньше N дней (для таймера)",
    )
    ap.add_argument("--force", action="store_true", help="рефреш всегда")
    ap.add_argument(
        "--no-restart",
        action="store_true",
        help="только обновить .env, без рестарта browser-agent/профиля",
    )
    args = ap.parse_args()

    env = read_env(ENV_PATH)
    cur = env.get("VISION_X_TOKEN", "")
    user = env.get("VISION_USERNAME")
    pw = env.get("VISION_PASSWORD")
    folder = env.get("VISION_FOLDER_ID", "")
    profile = env.get("VISION_PROFILE_ID", "")
    if not user or not pw:
        log("нет VISION_USERNAME/VISION_PASSWORD в .env — логиниться нечем")
        return 2

    dl = days_left(cur) if cur else -1.0
    if not args.force and args.if_expiring is not None and dl > args.if_expiring:
        log(f"токен жив ещё {dl:.1f}д (> {args.if_expiring}) — рефреш не нужен")
        return 0

    log(f"текущий токен: {dl:.1f}д до exp; логинюсь за свежим")
    new = login(user, pw)
    log(f"свежий токен получен, до exp {days_left(new):.1f}д")
    update_env_token(ENV_PATH, new)
    log("VISION_X_TOKEN обновлён в .env (бэкап создан)")

    if args.no_restart:
        log("--no-restart: канал не трогаю")
        return 0

    subprocess.run(["systemctl", "restart", "fb-browser-agent"], timeout=60)
    time.sleep(8)
    log("старт профиля: " + start_profile(new, folder, profile))
    time.sleep(8)
    log("ensure-cdp: " + ensure_cdp())
    time.sleep(5)
    h = probe()
    if h.get("healthy"):
        log(f"✅ канал здоров (probe 200), url={str(h.get('current_url', ''))[:55]}")
        return 0
    log(f"⚠️ канал ещё не здоров: {str(h.get('detail', ''))[:150]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
