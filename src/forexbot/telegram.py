"""Minimal Telegram Bot API client (standard library only).

The token is read from the private .env file and is never printed or logged.

Some internet providers occasionally lose their route to one of Telegram's servers. If the normal
address doesn't answer, the client tries Telegram's other official API servers. The connection is
still HTTPS and the certificate is still checked against api.telegram.org, so it can only ever
talk to the real Telegram.
"""
import http.client
import json
import os
import socket
import ssl
import urllib.parse
from pathlib import Path

from . import PROJECT_ROOT

ENV_FILE = PROJECT_ROOT / ".env"
HOST = "api.telegram.org"
FALLBACK_IPS = ["149.154.167.220", "149.154.166.110"]   # Telegram's Bot API servers
CONNECT_TIMEOUT = 10


class _Connection(http.client.HTTPSConnection):
    """HTTPS to api.telegram.org, optionally via a specific server address."""

    def __init__(self, ip: str | None, read_timeout: float):
        super().__init__(HOST, timeout=read_timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip or self.host, 443), CONNECT_TIMEOUT)
        sock.settimeout(self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


SECRET_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TWELVEDATA_API_KEY", "MYFXBOOK_EMAIL",
               "MYFXBOOK_PASSWORD", "FRED_API_KEY")


def load_env(path: Path = ENV_FILE) -> dict:
    """Secrets from the private .env file; in the cloud they come from environment variables."""
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in SECRET_KEYS:
        if not env.get(k) and os.environ.get(k):
            env[k] = os.environ[k]
    return env


def set_env_value(key: str, value: str, path: Path = ENV_FILE) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        env = load_env()
        self.token = token or env.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or env.get("TELEGRAM_CHAT_ID", "")
        if not self.token or ":" not in self.token:
            raise TelegramError("No valid TELEGRAM_BOT_TOKEN in .env. Paste the token from @BotFather there.")
        self._route: str | None = None       # None = normal address; else a fallback server

    def call(self, method: str, **params) -> dict:
        data = urllib.parse.urlencode(
            {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
        ).encode()
        read_timeout = float(params.get("timeout", 0)) + 30     # long polls need longer
        routes = list(dict.fromkeys([self._route, None, *FALLBACK_IPS]))
        last_error = None
        for route in routes:
            conn = _Connection(route, read_timeout)
            try:
                conn.request("POST", f"/bot{self.token}/{method}", body=data,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
                resp = conn.getresponse()
                status, raw = resp.status, resp.read()
            except (OSError, http.client.HTTPException) as e:
                last_error = type(e).__name__
                continue
            finally:
                conn.close()
            self._route = route
            break
        else:
            raise TelegramError(f"Can't reach Telegram right now ({last_error}); will keep retrying.")
        if status == 401:
            raise TelegramError("Telegram rejected the token (401). Re-copy it from @BotFather into .env.")
        body = json.loads(raw or b"{}")
        if not body.get("ok"):
            raise TelegramError(f"Telegram {method} failed: {body.get('description', 'unknown error')}")
        return body["result"]

    def me(self) -> dict:
        return self.call("getMe")

    def updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", **params)

    def answer_button(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None,
             chat_id: str | None = None) -> dict:
        """Send an HTML-formatted message. buttons = rows of (label, callback_data), or
        (label, {"copy": text}) for a button that copies text to the phone's clipboard."""
        params = {"chat_id": chat_id or self.chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "copy_text": {"text": data["copy"]}} if isinstance(data, dict)
                 else {"text": label, "callback_data": data} for label, data in row] for row in buttons
            ]}
        if not params["chat_id"]:
            raise TelegramError("No TELEGRAM_CHAT_ID yet. Run scripts\\telegram_setup.py first.")
        return self.call("sendMessage", **params)
