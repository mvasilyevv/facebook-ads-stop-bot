#!/usr/bin/env python3
"""Авто-рефреш Vision X-Token через облачный логин (v1.empr.cloud).

Логинится username+password (2FA на аккаунте отключён) → свежий JWT
(`data.token`; API-логин выдаёт долгоживущий токен) → при необходимости получает
team-token → пишет VISION_X_TOKEN в production .env (с бэкапом) → пересоздаёт
контейнеры, поднимает профиль через ensure-cdp и проверяет канал probe'ом.

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
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENV_PATH = os.environ.get("FB_AGENT_ENV", "/opt/fb-agent/shared/.env")
FB_AGENT_ROOT = os.environ.get("FB_AGENT_ROOT", "/opt/fb-agent")
CLOUD_AUTH = "https://v1.empr.cloud/api/v1/users/auth"


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
    backup_path = f"{path}.bak.token-{int(time.time())}"
    shutil.copy2(path, backup_path)
    with open(path, encoding="utf-8") as env_file:
        lines = env_file.read().splitlines()
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

    # Never truncate the live secrets file.  A killed process or full disk must
    # leave either the complete old file or the complete new one.  The backup is
    # intentionally created first and remains available for an operator rollback.
    env_path = os.path.abspath(path)
    env_dir = os.path.dirname(env_path)
    temp_fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(env_path)}.token-",
        dir=env_dir,
    )
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="\n") as temp_file:
            temp_fd = -1
            temp_file.write("\n".join(out) + "\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, env_path)
        temp_path = ""
        directory_fd = os.open(
            env_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def recreate_runtime() -> None:
    """Перечитывает новый token/env без остановки vision-webtop."""
    compose_script = os.path.join(FB_AGENT_ROOT, "current", "scripts", "server-compose.sh")
    services = [
        "browser-agent",
        "api",
        "observer",
        "meta_api",
        "health_watchdog",
        "campaign_creator",
        "creator_worker",
        "creator_recorder",
    ]
    cmd = [
        compose_script,
        "compose",
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        *services,
    ]
    env = os.environ.copy()
    env["FB_AGENT_ROOT"] = FB_AGENT_ROOT
    subprocess.run(cmd, check=True, timeout=180, env=env)


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
    team_id = env.get("VISION_TEAM_ID", "")
    if team_id:
        # Профили живут в командной папке: личный токен меняем на team-token
        # (GET /teams/{id}/auth), иначе /start отдаёт Payment required (инцидент 01.07).
        req = urllib.request.Request(
            f"https://v1.empr.cloud/api/v1/teams/{team_id}/auth",
            headers={"X-Token": new},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            new = json.loads(r.read())["data"]["token"]
        log(f"team-token получен (team {team_id[:8]}…), до exp {days_left(new):.1f}д")
    update_env_token(ENV_PATH, new)
    log("VISION_X_TOKEN обновлён в .env (бэкап создан)")

    if args.no_restart:
        log("--no-restart: канал не трогаю")
        return 0

    recreate_runtime()
    time.sleep(5)
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
