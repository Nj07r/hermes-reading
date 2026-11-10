"""Reading: newsletters out of the Gmail inbox, into a reader built for them.

Senders in ``<HERMES_HOME>/reading/senders.json`` skip the inbox and land in one Gmail label
(default ``Reading``). Native Gmail filters do the moving, so mail moves even when Hermes is
off. The reader page (page.html) lists and shows that label, in the Hermes Desktop pane and
in any browser through the dashboard.

Standard library only, plus Hermes's own sign-in helper: every Gmail call goes through
``credentialed_provider_request("google", ...)``. Hermes runs the Google sign-in
(``hermes auth add reading:google``), stores the grant and refreshes it. Reading never holds a
token or a client secret.

The page calls ``api_<name>(arg, body)`` through dashboard/plugin_api.py, which reloads this
module per request, so page and core changes need no gateway restart.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import sys
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path


PLUGIN_ID = "reading"
PLUGIN_DIR = Path(__file__).absolute().parent
API = "https://gmail.googleapis.com/gmail/v1/users/me"
CLIENT_ID_ENV = "READING_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "READING_GOOGLE_CLIENT_SECRET"
SIGN_IN = "hermes auth add reading:google"
FILTER_CHUNK = 20  # senders per Gmail filter; keeps each filter query well under Gmail's size limit
SCAN_MAX = 500     # inbox messages the newsletter scan reads at most
TEXT_MAX = 40_000  # characters of newsletter text handed to Hermes

# Caches survive importlib.reload(): they live on a module object that is never reloaded.
_C = sys.modules.setdefault("_reading_cache", types.ModuleType("_reading_cache"))
if not hasattr(_C, "meta"):
    _C.meta, _C.label_id, _C.email = {}, None, None
if not hasattr(_C, "label_name"):
    _C.label_name = None


class GmailError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# ---------- settings ----------

def schema_defaults() -> dict:
    try:
        import yaml
        manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text()) or {}
    except Exception:
        return {}
    return {k: (v or {}).get("default") for k, v in (manifest.get("config_schema") or {}).items()}


def settings() -> dict:
    """config_schema defaults, overlaid with config.yaml plugins.entries.reading.settings."""
    out = schema_defaults()
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        user = (((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}).get("settings") or {}
        out.update({k: v for k, v in user.items() if v not in (None, "")})
    except Exception:
        pass
    return out


def setting(name: str, fallback=None):
    v = settings().get(name)
    return fallback if v in (None, "") else v


def label_name() -> str:
    return str(setting("label_name", "Reading")).strip() or "Reading"


def data_dir() -> Path:
    import os
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    d = home / PLUGIN_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def senders_path() -> Path:
    return data_dir() / "senders.json"


def state_path() -> Path:
    return data_dir() / "state.json"


# ---------- Gmail over Hermes's sign-in ----------

NOT_CONNECTED = f"Gmail is not connected. In a terminal, run `hermes reading setup` (or `{SIGN_IN}`)."


def client_configured() -> bool:
    """True when this profile has the Google client id Reading declares (the secret is optional)."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return False
    return bool(str(get_secret(CLIENT_ID_ENV) or "").strip())


def _call(method: str, path: str, params: dict | None = None, body=None):
    from hermes_cli.auth_constants import AuthError
    from hermes_cli.plugin_provider_requests import ProviderNotSignedIn, credentialed_provider_request

    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    try:
        resp = credentialed_provider_request("google", method, url, json=body, timeout=30)
    except ProviderNotSignedIn:
        raise GmailError(NOT_CONNECTED, code="not_signed_in")
    except AuthError as exc:
        code = getattr(exc, "code", "") or "auth"
        raise GmailError(f"Google sign-in problem ({code}): {exc}", code=code)
    if resp.status == 401:
        raise GmailError(NOT_CONNECTED, code="not_signed_in")
    if resp.status >= 300:
        raise GmailError(f"{method} {path} -> {resp.status}: {resp.text[:400]}")
    return resp.json() if resp.body else {}


# ---------- label + account ----------

def label_id(create: bool = False) -> str | None:
    name = label_name()
    if _C.label_id and _C.label_name == name:
        return _C.label_id
    for lab in _call("GET", "/labels").get("labels", []):
        if lab["name"] == name:
            _C.label_id, _C.label_name = lab["id"], name
            return lab["id"]
    if not create:
        return None
    lab = _call("POST", "/labels", body={"name": name, "labelListVisibility": "labelShow",
                                         "messageListVisibility": "show"})
    _C.label_id, _C.label_name = lab["id"], name
    return lab["id"]


def account_email() -> str:
    if not _C.email:
        _C.email = _call("GET", "/profile").get("emailAddress", "")
    return _C.email


def gmail_link(msg_id: str) -> str:
    return f"https://mail.google.com/mail/?authuser={urllib.parse.quote(account_email())}#all/{msg_id}"


# ---------- parsing helpers (pure) ----------

def _headers(payload: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def split_from(value: str) -> tuple[str, str]:
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', value or "")
    if m:
        return (m.group(1).strip() or m.group(2)), m.group(2).strip().lower()
    return (value or "").strip(), (value or "").strip().lower()


def tidy(text: str) -> str:
    """Drop the invisible preheader padding newsletters use (U+034F, zero-width, soft hyphen)."""
    text = re.sub(r"[\u034f\u200b-\u200f\u00ad\u2060\ufeff]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_unsubscribe(header: str, post: str = "") -> dict:
    """RFC 2369 List-Unsubscribe (+ RFC 8058 one-click). Returns the https and mailto targets."""
    urls = re.findall(r"<\s*([^>\s]+)\s*>", header or "")
    https = next((u for u in urls if u.lower().startswith("https://")), None)
    mailto = next((u for u in urls if u.lower().startswith("mailto:")), None)
    one_click = bool(https) and "list-unsubscribe=one-click" in (post or "").lower().replace(" ", "")
    return {"https": https, "mailto": mailto, "one_click": one_click, "available": bool(https or mailto)}


_WEB_RE = re.compile(r"view\s+(it\s+|this\s+(email\s+|post\s+)?)?(in|on)\s+(a\s+|your\s+)?(web\s+)?browser|"
                     r"view\s+online|read\s+online|read\s+in\s+(the\s+)?(browser|app)|web\s+version|open\s+in\s+browser", re.I)


def web_url(body_html: str) -> str | None:
    for m in re.finditer(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', body_html, re.I | re.S):
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        if _WEB_RE.search(html.unescape(text)):
            return clean_url(html.unescape(m.group(1)))
    # Substack has no "view in browser" link; its post URL is in the body.
    m = re.search(r'https://open\.substack\.com/pub/[^/"]+/p/[^"?&]+', body_html)
    return m.group(0) if m else None


def clean_url(url: str) -> str:
    # Click trackers (TLDR, AWS SES) wrap the real URL percent-encoded: .../CL0/https:%2F%2F.../1/...
    m = re.search(r"/C?L0/(https?:%2F%2F[^/]+)", url)
    if m:
        url = urllib.parse.unquote(m.group(1))
    if url.startswith("https://open.substack.com/"):
        return url.split("?", 1)[0]
    return url


def plain(body_html: str) -> str:
    text = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", body_html)
    return tidy(html.unescape(re.sub(r"<[^>]+>", " ", text)))


def html_to_text(body_html: str) -> str:
    """Readable text for Hermes: paragraphs kept, links as [text](url), trackers unwrapped."""
    t = re.sub(r"(?is)<(style|script|head|title)\b.*?</\1>", " ", body_html)
    t = re.sub(r"(?is)<!--.*?-->", " ", t)

    def link(m):
        href, label = clean_url(html.unescape(m.group(1))), tidy(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))))
        if not label or not href.startswith("http"):
            return f" {label} "
        return f" [{label}]({href}) " if label != href else f" {href} "

    t = re.sub(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', link, t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|tr|h[1-6]|li|table|blockquote|section|article)>", "\n", t)
    t = re.sub(r"(?i)<li\b[^>]*>", "\n- ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[\u034f\u200b-\u200f\u00ad\u2060\ufeff]", "", t)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in t.split("\n")]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 1:
            out.append(ln)
    return "\n".join(out).strip()


def opens_with(body_html: str, subject: str) -> bool:
    """True when the email's own visible top already shows the subject (Substack and most blogs)."""
    norm = lambda s: re.sub(r"[^\w]+", " ", s.lower()).strip()  # noqa: E731
    subj = norm(subject)
    return len(subj) > 12 and subj in norm(plain(body_html)[:500])


def minutes(body_html: str) -> int:
    return max(1, round(len(plain(body_html).split()) / 230))


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk(part: dict, out: dict):
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if data and mime in ("text/html", "text/plain") and mime not in out:
        out[mime] = _b64(data).decode("utf-8", "replace")
    cid = _headers(part).get("content-id")
    att = part.get("body", {}).get("attachmentId")
    if cid and att:
        out.setdefault("_cid", {})[cid.strip("<>")] = (mime, att)
    for p in part.get("parts", []) or []:
        _walk(p, out)


# ---------- reading ----------

def _meta(msg_id: str) -> dict:
    hit = _C.meta.get(msg_id)
    if hit:
        return hit
    m = _call("GET", f"/messages/{msg_id}", {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
    h = _headers(m.get("payload", {}))
    name, addr = split_from(h.get("from", ""))
    item = {"id": m["id"], "threadId": m.get("threadId"), "from": name, "email": addr,
            "subject": h.get("subject", "(no subject)"), "snippet": tidy(html.unescape(m.get("snippet", ""))),
            "ts": int(m.get("internalDate", 0)) // 1000}
    _C.meta[msg_id] = item
    return item


def _ids(params: dict, limit: int) -> list[str]:
    ids, page = [], None
    while len(ids) < limit:
        p = {**params, "maxResults": min(500, limit - len(ids))}
        if page:
            p["pageToken"] = page
        d = _call("GET", "/messages", p)
        ids += [m["id"] for m in d.get("messages", [])]
        page = d.get("nextPageToken")
        if not page:
            break
    return ids


def list_messages(limit: int | None = None) -> list[dict]:
    lid = label_id()
    if not lid:
        return []
    ids = _ids({"labelIds": lid}, int(limit or setting("list_limit", 300)))
    with ThreadPoolExecutor(8) as pool:
        items = list(pool.map(_meta, ids))
    return sorted(items, key=lambda i: i["ts"], reverse=True)


def _full(msg_id: str) -> tuple[dict, dict, str]:
    """The raw message, its headers, and an HTML body (plain text wrapped when there is no HTML)."""
    m = _call("GET", f"/messages/{msg_id}", {"format": "full"})
    out: dict = {}
    _walk(m.get("payload", {}), out)
    body = out.get("text/html")
    if body is None:
        text = html.escape(out.get("text/plain", m.get("snippet", "")))
        text = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', text)
        body = f'<pre style="white-space:pre-wrap;font:15px/1.55 -apple-system,sans-serif;padding:4px 18px;margin:0">{text}</pre>'
    for cid, (mime, att) in (out.get("_cid") or {}).items():  # inline cid: images, embedded as data URLs
        if f"cid:{cid}" in body:
            try:
                a = _call("GET", f"/messages/{msg_id}/attachments/{att}")
                body = body.replace(f"cid:{cid}", f"data:{mime};base64,{base64.b64encode(_b64(a['data'])).decode()}")
            except GmailError:
                pass
    return m, _headers(m.get("payload", {})), body


def get_message(msg_id: str, mark_read: bool = True) -> dict:
    m, h, body = _full(msg_id)
    if mark_read and setting("mark_read", True) and "UNREAD" in m.get("labelIds", []):
        try:
            _call("POST", f"/messages/{msg_id}/modify", body={"removeLabelIds": ["UNREAD"]})
        except GmailError:
            pass
    meta = _meta(msg_id)
    unsub = parse_unsubscribe(h.get("list-unsubscribe", ""), h.get("list-unsubscribe-post", ""))
    return {**meta, "html": body, "gmail": gmail_link(msg_id), "web": web_url(body), "minutes": minutes(body),
            "dup": opens_with(body, meta["subject"]),
            "unsubscribe": {"available": unsub["available"], "one_click": unsub["one_click"]}}


def message_text(msg_id: str) -> str:
    """The newsletter as Markdown for Hermes (no side effects: it stays unread)."""
    m, h, body = _full(msg_id)
    name, addr = split_from(h.get("from", ""))
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m.get("internalDate", 0)) // 1000))
    web = web_url(body)
    text = html_to_text(body)
    if len(text) > TEXT_MAX:
        text = text[:TEXT_MAX].rstrip() + "\n\n[... cut: the newsletter is longer ...]"
    head = [f"# {h.get('subject', '(no subject)')}", f"From: {name} <{addr}>", f"Date: {when}"]
    if web:
        head.append(f"Web version: {web}")
    head.append(f"Gmail: {gmail_link(msg_id)}")
    return "\n".join(head) + "\n\n" + text


def trash(msg_id: str) -> dict:
    _call("POST", f"/messages/{msg_id}/trash")
    _C.meta.pop(msg_id, None)
    return {"ok": True, "id": msg_id}


def untrash(msg_id: str) -> dict:
    _call("POST", f"/messages/{msg_id}/untrash")
    return {"ok": True, "id": msg_id}


# ---------- senders + filters ----------

def load_senders() -> list[str]:
    try:
        return sorted(set(json.loads(senders_path().read_text())))
    except FileNotFoundError:
        return []


def normalize(addresses) -> list[str]:
    out = set()
    for a in addresses or []:
        a = str(a).strip().lower()
        a = split_from(a)[1] if "<" in a else a
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a):
            out.add(a)
    return sorted(out)


def save_senders(senders) -> list[str]:
    data_dir().mkdir(parents=True, exist_ok=True)
    clean = normalize(senders)
    senders_path().write_text(json.dumps(clean, indent=1) + "\n")
    return clean


def _state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except FileNotFoundError:
        return {}


def _save_state(st: dict):
    data_dir().mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(st, indent=1) + "\n")


def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def from_query(chunk) -> str:
    return "from:(" + " OR ".join(chunk) + ")"


def all_ids(q: str) -> list[str]:
    return _ids({"q": q}, 100_000)


def sync(dry_run: bool = False) -> dict:
    """Make Gmail match senders.json: one filter per chunk of senders (skip the inbox, add the
    label, rescue from spam), and move their mail that is still in the inbox. Idempotent."""
    senders = load_senders()
    lid = label_id(create=not dry_run) or "(new)"
    st = _state()
    report = {"senders": len(senders), "label": label_name(), "filters_created": 0, "filters_deleted": 0, "moved": 0}
    digest = hashlib.sha256(json.dumps([senders, lid]).encode()).hexdigest()
    if digest != st.get("digest"):
        if dry_run:
            report["filters"] = f"would replace {len(st.get('filters', []))} with {len(chunks(senders, FILTER_CHUNK))}"
        else:
            for fid in st.get("filters", []):
                try:
                    _call("DELETE", f"/settings/filters/{fid}")
                    report["filters_deleted"] += 1
                except GmailError as exc:
                    if "404" not in str(exc):
                        raise
            new = []
            for chunk in chunks(senders, FILTER_CHUNK):
                f = _call("POST", "/settings/filters", body={
                    "criteria": {"query": from_query(chunk)},
                    "action": {"addLabelIds": [lid], "removeLabelIds": ["INBOX", "SPAM", "IMPORTANT"]}})
                new.append(f["id"])
            st.update(filters=new, digest=digest)
            _save_state(st)
            report["filters_created"] = len(new)
    for chunk in chunks(senders, FILTER_CHUNK):
        # Inbox only: years of archived back issues would bury the reader.
        ids = all_ids(from_query(chunk) + " in:inbox")
        report["moved"] += len(ids)
        if ids and not dry_run:
            for batch in chunks(ids, 1000):
                _call("POST", "/messages/batchModify", body={"ids": batch, "addLabelIds": [lid], "removeLabelIds": ["INBOX"]})
    if dry_run:
        report["would_move"] = report.pop("moved")
    if report.get("moved") and not dry_run:
        _C.meta.clear()
    return report


def add_senders(addresses) -> dict:
    given = normalize(addresses)
    if not given:
        raise GmailError("no valid email address given")
    before = set(load_senders())
    save_senders(before | set(given))
    return {"added": sorted(set(given) - before), **sync()}


def remove_senders(addresses, restore: bool = False) -> dict:
    """Stop routing senders. With ``restore``, their mail in the label goes back to the inbox."""
    given = set(normalize(addresses))
    before = set(load_senders())
    save_senders(before - given)
    report = {"removed": sorted(given & before), **sync()}
    if restore and given:
        lid = label_id()
        ids = [i for chunk in chunks(sorted(given), FILTER_CHUNK) for i in all_ids(from_query(chunk) + f' label:"{label_name()}"')]
        for batch in chunks(ids, 1000):
            _call("POST", "/messages/batchModify", body={"ids": batch, "addLabelIds": ["INBOX"], "removeLabelIds": [lid]})
        report["restored"] = len(ids)
        _C.meta.clear()
    return report


def sender_counts() -> dict[str, int]:
    """Messages per sender in the label (from the list cache)."""
    counts: dict[str, int] = {}
    for m in list_messages():
        counts[m["email"]] = counts.get(m["email"], 0) + 1
    return counts


# ---------- newsletter scan ----------

# Senders on these platforms are newsletters for sure; the scan ticks them by default.
PLATFORMS = ("substack.com", "beehiiv.com", "convertkit.com", "kit.com", "ck.page", "mailchimp", "mcsv.net",
             "list-manage.com", "buttondown", "ghost.io", "revue", "tinyletter", "mailerlite", "sendfox",
             "campaign-archive", "medium.com", "every.to", "morningbrew.com", "tldrnewsletter.com", "flodesk")


def _scan_meta(msg_id: str) -> dict:
    m = _call("GET", f"/messages/{msg_id}", {"format": "metadata", "metadataHeaders": [
        "From", "Subject", "List-Unsubscribe", "List-Unsubscribe-Post", "List-Id", "Precedence"]})
    return {"h": _headers(m.get("payload", {})), "ts": int(m.get("internalDate", 0)) // 1000}


def group_candidates(metas: list[dict], skip: set[str]) -> list[dict]:
    """Group scanned headers by sender. Only list mail counts (List-Unsubscribe or List-Id)."""
    by: dict[str, dict] = {}
    for m in metas:
        h = m["h"]
        if not (h.get("list-unsubscribe") or h.get("list-id")):
            continue
        name, addr = split_from(h.get("from", ""))
        if not addr or addr in skip:
            continue
        c = by.setdefault(addr, {"email": addr, "name": name, "count": 0, "last_subject": "", "last_ts": 0,
                                 "list_id": False, "platform": False})
        c["count"] += 1
        if m["ts"] >= c["last_ts"]:
            c.update(last_ts=m["ts"], last_subject=h.get("subject", ""), name=name or c["name"])
        blob = " ".join([addr, h.get("list-id", ""), h.get("list-unsubscribe", "")]).lower()
        c["list_id"] = c["list_id"] or bool(h.get("list-id"))
        c["platform"] = c["platform"] or any(p in blob for p in PLATFORMS)
    out = sorted(by.values(), key=lambda c: (-c["count"], -c["last_ts"]))
    for c in out:
        # Tick what looks like a newsletter; leave receipts and alerts for the user to decide.
        c["suggest"] = c["platform"] or (c["list_id"] and c["count"] >= 2)
    return out


def scan(days: int | None = None, limit: int = SCAN_MAX) -> dict:
    days = int(days or setting("scan_days", 30))
    ids = _ids({"q": f"in:inbox newer_than:{days}d"}, limit)
    with ThreadPoolExecutor(10) as pool:
        metas = list(pool.map(_scan_meta, ids))
    skip = set(load_senders()) | {account_email().lower()}
    return {"days": days, "scanned": len(ids), "candidates": group_candidates(metas, skip)}


# ---------- unsubscribe ----------

def _send_mailto(mailto: str) -> None:
    u = urllib.parse.urlsplit(mailto)
    q = {k.lower(): v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    msg = EmailMessage()
    msg["To"] = urllib.parse.unquote(u.path)
    msg["From"] = account_email()
    msg["Subject"] = q.get("subject", "unsubscribe")
    msg.set_content(q.get("body", "unsubscribe"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _call("POST", "/messages/send", body={"raw": raw})


def _one_click(url: str) -> bool:
    req = urllib.request.Request(url, data=b"List-Unsubscribe=One-Click", method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Hermes-Reading/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def unsubscribe(msg_id: str, trash_existing: bool = False) -> dict:
    """Unsubscribe from the sender of one message: one-click POST, else the mailto email, else
    hand back the web page for the user. Then stop routing the sender."""
    m = _call("GET", f"/messages/{msg_id}", {"format": "metadata", "metadataHeaders": [
        "From", "List-Unsubscribe", "List-Unsubscribe-Post"]})
    h = _headers(m.get("payload", {}))
    addr = split_from(h.get("from", ""))[1]
    u = parse_unsubscribe(h.get("list-unsubscribe", ""), h.get("list-unsubscribe-post", ""))
    result = {"ok": True, "email": addr, "method": None, "url": None}
    if u["one_click"] and _one_click(u["https"]):
        result["method"] = "one-click"
    elif u["mailto"]:
        _send_mailto(u["mailto"])
        result["method"] = "email"
    elif u["https"]:
        result.update(method="link", url=u["https"])  # the user finishes it on the sender's page
    else:
        raise GmailError("this newsletter has no unsubscribe header. Use the link inside the email.")
    if addr in load_senders():
        save_senders(set(load_senders()) - {addr})
        result["sync"] = sync()
    if trash_existing and addr:
        ids = all_ids(f'from:{addr} label:"{label_name()}"')
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda i: _call("POST", f"/messages/{i}/trash"), ids))
        for i in ids:
            _C.meta.pop(i, None)
        result["trashed"] = len(ids)
    return result


# ---------- status ----------

def status() -> dict:
    out = {"ok": True, "client": client_configured(), "connected": False, "label": label_name(),
           "senders": len(load_senders()), "account": None, "sign_in": SIGN_IN}
    if out["client"]:
        try:
            out["account"] = account_email()
            out["connected"] = True
        except GmailError as exc:
            if getattr(exc, "code", "") != "not_signed_in":
                out["error"] = str(exc)
    out["ready"] = out["connected"] and out["senders"] > 0
    return out


# ---------- page API: plugin_api.py dispatches /call/<name>?id= to api_<name>(arg, body) ----------

def _body(body) -> dict:
    return body if isinstance(body, dict) else {}


def api_page(_arg="", body=None) -> dict:
    """The reader page as JSON, for the Desktop pane (the plugin REST door only speaks JSON)."""
    return {"ok": True, "html": (PLUGIN_DIR / "page.html").read_text(encoding="utf-8")}


def api_status(_arg="", body=None) -> dict:
    return status()


def api_messages(_arg="", body=None) -> dict:
    return {"ok": True, "items": list_messages()}


def api_message(msg_id: str, body=None) -> dict:
    return {"ok": True, **get_message(msg_id)}


def api_trash(msg_id: str, body=None) -> dict:
    return trash(msg_id)


def api_untrash(msg_id: str, body=None) -> dict:
    return untrash(msg_id)


def api_scan(_arg="", body=None) -> dict:
    return {"ok": True, **scan(_body(body).get("days"))}


def api_senders(_arg="", body=None) -> dict:
    counts = {}
    try:
        counts = sender_counts()
    except GmailError:
        pass
    names = {m["email"]: m["from"] for m in _C.meta.values()}
    return {"ok": True, "label": label_name(),
            "senders": [{"email": s, "name": names.get(s, ""), "count": counts.get(s, 0)} for s in load_senders()]}


def api_senders_add(_arg="", body=None) -> dict:
    return {"ok": True, **add_senders(_body(body).get("emails") or [_arg])}


def api_senders_remove(_arg="", body=None) -> dict:
    b = _body(body)
    return {"ok": True, **remove_senders(b.get("emails") or [_arg], restore=bool(b.get("restore")))}


def api_unsubscribe(msg_id: str, body=None) -> dict:
    return unsubscribe(msg_id, trash_existing=bool(_body(body).get("trash")))
