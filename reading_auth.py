"""Google sign-in for Reading. Standard library only.

Reading needs two Gmail scopes: ``gmail.modify`` (list, read, label, trash, and send the
unsubscribe email) and ``gmail.settings.basic`` (create the Gmail filters).

Where the files are (first match wins):
- OAuth client: ``<HERMES_HOME>/reading/client_secret.json``, then
  ``<HERMES_HOME>/google_client_secret.json`` (the google-workspace skill uses the same file).
- Token: the ``token_path`` setting, then ``<HERMES_HOME>/reading/token.json``, then
  ``<HERMES_HOME>/google_token.json`` if that token already has both scopes.

Reading writes only its own token file. It never writes the shared google-workspace token.

Sign-in is the installed-app flow with PKCE: a one-shot HTTP server on 127.0.0.1 receives the
redirect. When the browser is on another machine than Hermes, the page does not load, and the
user pastes the address from the browser bar instead (``finish``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
FLOW_TTL = 600  # seconds a started sign-in stays valid

# Survives importlib.reload() (the dashboard reloads modules per request).
_A = sys.modules.setdefault("_reading_auth_state", types.ModuleType("_reading_auth_state"))
if not hasattr(_A, "flow"):
    _A.flow, _A.server = None, None


class AuthError(RuntimeError):
    pass


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def data_dir() -> Path:
    return hermes_home() / "reading"


def own_token_path() -> Path:
    return data_dir() / "token.json"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


# ---------- client ----------

def client_paths() -> list[Path]:
    return [data_dir() / "client_secret.json", hermes_home() / "google_client_secret.json"]


def parse_client(raw: dict) -> dict:
    """Accept a Google Cloud client JSON ("installed" or "web") or a flat dict."""
    if not isinstance(raw, dict):
        raise AuthError("the client file is not a JSON object")
    inner = raw.get("installed") or raw.get("web") or raw
    cid, secret = inner.get("client_id"), inner.get("client_secret")
    if not cid or not secret:
        raise AuthError("the client file has no client_id or client_secret. "
                        "Download the JSON of a Desktop app OAuth client from Google Cloud Console.")
    return {"client_id": cid, "client_secret": secret, "token_uri": inner.get("token_uri") or TOKEN_URI}


def load_client() -> tuple[dict, Path] | None:
    for path in client_paths():
        raw = _read_json(path)
        if raw:
            try:
                return parse_client(raw), path
            except AuthError:
                continue
    return None


def save_client(raw: dict) -> Path:
    parse_client(raw)  # validate before writing
    path = data_dir() / "client_secret.json"
    _write_private(path, raw)
    return path


# ---------- token ----------

def has_scopes(token: dict | None) -> bool:
    granted = set((token or {}).get("scopes") or [])
    if isinstance((token or {}).get("scope"), str):
        granted |= set(token["scope"].split())
    return all(s in granted for s in SCOPES)


def token_candidates(setting: str = "") -> list[Path]:
    paths = [Path(setting).expanduser()] if setting else []
    return paths + [own_token_path(), hermes_home() / "google_token.json"]


def find_token(setting: str = "") -> tuple[dict, Path] | None:
    """The first usable token: one with a refresh token and both scopes."""
    for path in token_candidates(setting):
        tok = _read_json(path)
        if tok and tok.get("refresh_token") and has_scopes(tok):
            return tok, path
    return None


def refresh(token: dict) -> tuple[str, float]:
    body = urllib.parse.urlencode({
        "client_id": token["client_id"], "client_secret": token["client_secret"],
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(token.get("token_uri") or TOKEN_URI, body, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise AuthError(f"Google refused the token ({exc.code}). Connect Gmail again. {detail}")
    return d["access_token"], time.time() + int(d.get("expires_in", 3600))


# ---------- sign-in flow ----------

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def auth_url(client: dict, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })


def parse_redirect(url: str) -> dict:
    """Code and state from the address the browser landed on (or from a bare code)."""
    url = (url or "").strip()
    if not url:
        raise AuthError("paste the address from the browser bar")
    if "://" not in url and "=" not in url:
        return {"code": url, "state": None}
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if "error" in q:
        raise AuthError(f"Google said: {q['error'][0]}")
    if "code" not in q:
        raise AuthError("that address has no code in it")
    return {"code": q["code"][0], "state": (q.get("state") or [None])[0]}


def exchange(client: dict, code: str, redirect_uri: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code": code,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    try:
        with urllib.request.urlopen(client.get("token_uri") or TOKEN_URI, body, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as exc:
        raise AuthError(f"Google refused the code ({exc.code}): {exc.read()[:300].decode('utf-8', 'replace')}")
    if not d.get("refresh_token"):
        raise AuthError("Google sent no refresh token. Remove Reading's access at "
                        "myaccount.google.com/permissions and connect again.")
    granted = (d.get("scope") or "").split()
    token = {**client, "refresh_token": d["refresh_token"], "scopes": granted or SCOPES}
    if not has_scopes(token):
        raise AuthError("Google did not grant every permission. Connect again and tick every box.")
    _write_private(own_token_path(), token)
    return token


_DONE_PAGE = b"""<!doctype html><meta charset=utf-8><title>Reading</title>
<body style="font:16px -apple-system,sans-serif;background:#100F0F;color:#CECDC3;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2>Gmail is connected</h2><p>You can close this tab and go back to Reading.</p></div>"""


def _stop_server() -> None:
    srv = _A.server
    _A.server = None
    if srv:
        threading.Thread(target=srv.shutdown, daemon=True).start()


def start(client: dict | None = None, listen: bool = True) -> dict:
    """Begin a sign-in. Returns the Google URL to open. With ``listen``, a one-shot server on
    127.0.0.1 finishes the sign-in by itself when the browser comes back."""
    if client is None:
        found = load_client()
        if not found:
            raise AuthError("no OAuth client yet. Add your client_secret.json first.")
        client = found[0]
    _stop_server()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    flow = {"client": client, "verifier": verifier, "state": state, "at": time.time(), "done": False, "error": None}
    if listen:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - keep the gateway log clean
                pass

            def do_GET(self):  # noqa: N802 - http.server API
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if "code" not in q and "error" not in q:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    finish(f"http://127.0.0.1{self.path}")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(_DONE_PAGE)
                except AuthError as exc:
                    flow["error"] = str(exc)
                    self.send_response(400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"Reading could not connect Gmail: {exc}".encode())
                finally:
                    _stop_server()

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        srv.timeout = 1
        _A.server = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        threading.Timer(FLOW_TTL, lambda: _A.server is srv and _stop_server()).start()
        flow["redirect_uri"] = f"http://127.0.0.1:{srv.server_address[1]}/"
    else:
        flow["redirect_uri"] = "http://127.0.0.1:1/"
    _A.flow = flow
    return {"url": auth_url(client, flow["redirect_uri"], state, challenge), "redirect_uri": flow["redirect_uri"]}


def finish(pasted: str) -> dict:
    """Finish a started sign-in from the redirect address (or the bare code)."""
    flow = _A.flow
    if not flow or time.time() - flow["at"] > FLOW_TTL:
        raise AuthError("the sign-in expired. Start it again.")
    got = parse_redirect(pasted)
    if got["state"] and got["state"] != flow["state"]:
        raise AuthError("that address belongs to another sign-in. Start it again.")
    exchange(flow["client"], got["code"], flow["redirect_uri"], flow["verifier"])
    flow["done"] = True
    _A.flow = None
    return {"ok": True, "token": str(own_token_path())}


def pending() -> dict:
    flow = _A.flow
    if not flow:
        return {"pending": False}
    return {"pending": True, "error": flow.get("error"), "age": int(time.time() - flow["at"])}
