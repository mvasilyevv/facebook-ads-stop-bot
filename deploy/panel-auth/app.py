#!/usr/bin/env python3
"""panel-auth — лёгкий login-gate для app.adpulse.su.

Заменяет Caddy basic_auth: стилизованная HTML-форма логина + подписанная HttpOnly
cookie на 30 дней. Caddy ходит через forward_auth → GET /verify; при валидной
cookie — 200 (пускает к панели/VNC), иначе 302 на /login.

Один общий пароль: bcrypt-хэш переиспользует существующий из Caddyfile (PANEL_BCRYPT_HASH),
открытый пароль нигде не хранится. Cookie подписывается HMAC-SHA256 (PANEL_AUTH_SECRET).
Зависимости: только stdlib + bcrypt.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import bcrypt

PORT = int(os.environ.get("PANEL_AUTH_PORT", "8090"))
BCRYPT_HASH = os.environ["PANEL_BCRYPT_HASH"].encode()
SECRET = os.environ["PANEL_AUTH_SECRET"].encode()
COOKIE_NAME = "panel_session"
TTL = 30 * 24 * 3600  # 30 дней

_LOGIN_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AdPulse · вход</title>
<style>
  :root {
    --bg-0:#0a0a0b; --bg-1:#101012; --bg-2:#16161a; --bg-6:#38383f; --bg-7:#4a4a52;
    --bg-9:#7c7c86; --bg-10:#a8a8b0; --bg-11:#e4e4e7;
    --accent:#f5f1e8; --danger:#f87171; --hairline:rgba(255,255,255,.07);
    --mono:"JetBrains Mono","SF Mono","Menlo",ui-monospace,monospace;
    --body:"Inter Tight","SF Pro Text",system-ui,sans-serif;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; margin:0; }
  body {
    background:var(--bg-0); color:var(--bg-11); font-family:var(--body);
    display:flex; align-items:center; justify-content:center; min-height:100dvh;
    background-image:radial-gradient(circle at 50% 0%, #141417 0%, #0a0a0b 60%);
  }
  .card {
    width:340px; max-width:calc(100vw - 32px); background:var(--bg-1);
    border:1px solid var(--hairline); border-radius:14px; padding:32px 28px 26px;
    box-shadow:0 24px 60px -20px rgba(0,0,0,.6);
  }
  .mark {
    width:34px; height:34px; border-radius:9px; border:1px solid var(--bg-7);
    display:flex; align-items:center; justify-content:center; margin-bottom:20px;
    color:var(--accent);
  }
  .eyebrow {
    font-family:var(--mono); font-size:10px; letter-spacing:.16em; text-transform:uppercase;
    color:var(--bg-9); margin-bottom:6px;
  }
  h1 { font-family:var(--mono); font-size:20px; font-weight:500; margin:0 0 22px; letter-spacing:-.01em; }
  label {
    display:block; font-family:var(--mono); font-size:10px; letter-spacing:.12em;
    text-transform:uppercase; color:var(--bg-10); margin-bottom:7px;
  }
  input[type=password] {
    width:100%; height:40px; padding:0 12px; background:var(--bg-2); color:var(--bg-11);
    border:1px solid var(--bg-6); border-radius:9px; font-family:var(--body); font-size:14px;
    outline:none; transition:border-color .12s, background .12s;
  }
  input[type=password]:focus { border-color:var(--accent); background:#1b1b1f; }
  button {
    width:100%; height:42px; margin-top:18px; border:0; border-radius:9px;
    background:var(--accent); color:#17150f; font-family:var(--mono); font-weight:600;
    font-size:13px; letter-spacing:.02em; cursor:pointer; transition:opacity .12s, transform .04s;
  }
  button:hover { opacity:.9; }
  button:active { transform:translateY(1px); }
  .err {
    margin-top:14px; color:var(--danger); font-size:12px; font-family:var(--mono);
    min-height:14px;
  }
  .foot { margin-top:22px; font-size:11px; color:var(--bg-9); text-align:center; }
</style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <div class="mark" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>
      </svg>
    </div>
    <div class="eyebrow">Панель управления</div>
    <h1>AdPulse</h1>
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           autofocus required>
    <button type="submit">Войти</button>
    <div class="err">__ERROR__</div>
    <div class="foot">Доступ по одному паролю · сессия 30 дней</div>
  </form>
</body>
</html>"""


def _sign(expiry: int) -> str:
    sig = hmac.new(SECRET, str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def _valid(token: str) -> bool:
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    expected = hmac.new(SECRET, exp_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(exp_str) > int(time.time())
    except ValueError:
        return False


def _page(error: str = "") -> bytes:
    return _LOGIN_PAGE.replace("__ERROR__", error).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "panel-auth"

    def log_message(self, *args):  # без шумного access-лога
        pass

    def _cookie(self) -> str:
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        return jar[COOKIE_NAME].value if COOKIE_NAME in jar else ""

    def _body(self, code: int, data: bytes, ctype: str, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str, headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()

    def _route(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/verify":
            # forward_auth-таргет: валидная cookie → 200, иначе редирект на форму.
            if _valid(self._cookie()):
                self._body(200, b"ok", "text/plain")
            else:
                self._redirect("/login")
            return
        if path == "/health":
            self._body(200, b"ok", "text/plain")
            return
        if path == "/logout":
            self._redirect(
                "/login",
                [
                    (
                        "Set-Cookie",
                        f"{COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax",
                    )
                ],
            )
            return
        if path == "/login":
            if self.command == "POST":
                self._do_login()
            else:
                self._body(200, _page(), "text/html; charset=utf-8")
            return
        # Любой другой путь, доедающий до :8090 (через @auth passthrough) — на форму.
        self._redirect("/login")

    def _do_login(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        password = parse_qs(raw).get("password", [""])[0]
        ok = False
        if password:
            try:
                ok = bcrypt.checkpw(password.encode(), BCRYPT_HASH)
            except ValueError:
                ok = False
        if ok:
            token = _sign(int(time.time()) + TTL)
            cookie = f"{COOKIE_NAME}={token}; Max-Age={TTL}; Path=/; HttpOnly; Secure; SameSite=Lax"
            self._redirect("/", [("Set-Cookie", cookie)])
        else:
            self._body(401, _page("Неверный пароль"), "text/html; charset=utf-8")

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_POST(self):
        self._route()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"panel-auth слушает 127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
