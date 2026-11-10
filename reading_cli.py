"""The ``hermes reading`` command line."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import reading_auth as auth
import reading_core as core

USAGE = """examples:
  hermes reading setup                      connect Gmail, then pick your newsletters
  hermes reading scan --add suggested       route every newsletter the scan finds
  hermes reading add news@example.com       route one more sender
  hermes reading remove news@example.com --restore
  hermes reading export --since 24h         newsletters of the last day as Markdown (digest jobs)
  hermes reading import-label Meco          coming from Meco: take over its label
"""

CLIENT_HELP = """Reading needs a Google OAuth client (free, about 3 minutes, once):
  1. https://console.cloud.google.com/apis/library/gmail.googleapis.com  ->  Enable (Gmail API)
  2. https://console.cloud.google.com/auth/overview  ->  set up the consent screen
     (External, add your own address under Audience > Test users)
  3. https://console.cloud.google.com/auth/clients  ->  Create client  ->  Desktop app  ->  Download JSON
  4. hermes reading setup --client ~/Downloads/client_secret_....json
Already use the google-workspace skill? Its client works too: Reading finds it by itself."""


def parse_since(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", text or "")
    if not m:
        raise SystemExit("reading: --since takes a number and m, h, d or w, for example 24h")
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]


def setup(parser) -> None:
    parser.epilog = USAGE
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    sub = parser.add_subparsers(dest="reading_cmd")

    s = sub.add_parser("setup", help="connect Gmail and pick newsletters")
    s.add_argument("--client", help="path to the Google OAuth client JSON (Desktop app)")
    s.add_argument("--manual", action="store_true", help="no local browser: paste the address instead")

    st = sub.add_parser("status", help="connection, label and sender count")
    st.add_argument("--json", action="store_true")

    sc = sub.add_parser("scan", help="find newsletters in the inbox")
    sc.add_argument("--days", type=int, help="how far back (default: the scan_days setting)")
    sc.add_argument("--add", choices=["suggested", "all"], help="route the senders found")
    sc.add_argument("--json", action="store_true")

    se = sub.add_parser("senders", help="list the routed senders")
    se.add_argument("--json", action="store_true")

    a = sub.add_parser("add", help="route senders into the label")
    a.add_argument("emails", nargs="+")
    r = sub.add_parser("remove", help="stop routing senders")
    r.add_argument("emails", nargs="+")
    r.add_argument("--restore", action="store_true", help="also move their mail back to the inbox")

    sy = sub.add_parser("sync", help="make the Gmail filters match the sender list")
    sy.add_argument("--dry-run", action="store_true")

    il = sub.add_parser("import-label", help="take over another label (for example Meco's)")
    il.add_argument("label", help="label name or id")

    ls = sub.add_parser("list", help="newsletters in the label")
    ls.add_argument("--since", help="only newer ones, for example 24h or 7d")
    ls.add_argument("--json", action="store_true")

    ex = sub.add_parser("export", help="newsletters as Markdown text, for digest jobs")
    ex.add_argument("--since", default="24h")
    ex.add_argument("--limit", type=int, default=30)

    u = sub.add_parser("unsubscribe", help="unsubscribe from the sender of a message")
    u.add_argument("id")
    u.add_argument("--trash", action="store_true", help="also trash their mail in the label")
    parser.set_defaults(func=run)


def _print(data) -> None:
    print(json.dumps(data, indent=1, ensure_ascii=False))


def _connect(client_file: str | None, manual: bool) -> None:
    if client_file:
        path = auth.save_client(json.loads(Path(client_file).expanduser().read_text()))
        print(f"Saved the OAuth client to {path}")
    if not auth.load_client():
        print(CLIENT_HELP)
        raise SystemExit(1)
    flow = auth.start(listen=not manual)
    print("Open this address and allow Reading to use Gmail:\n\n  " + flow["url"] + "\n")
    if manual:
        pasted = input("Then paste the address of the page you land on (it may not load): ")
        auth.finish(pasted)
    else:
        webbrowser.open(flow["url"])
        print("Waiting for Google (Ctrl+C to stop)...")
        deadline = time.time() + auth.FLOW_TTL
        while time.time() < deadline:
            p = auth.pending()
            if not p["pending"]:
                break
            if p.get("error"):
                raise SystemExit(f"reading: {p['error']}")
            time.sleep(1)
    found = auth.find_token()
    if not found:
        raise SystemExit("reading: the sign-in did not finish. Run setup again (or with --manual).")
    core._C.token = None
    print(f"Connected as {core.account_email()}.")


def _show_candidates(found: dict) -> None:
    cands = found["candidates"]
    print(f"Scanned {found['scanned']} inbox emails of the last {found['days']} days: {len(cands)} list senders.\n")
    for c in cands:
        tick = "x" if c["suggest"] else " "
        print(f"  [{tick}] {c['count']:>3}  {c['name'][:28]:<28}  {c['email']}")
    print("\n[x] = looks like a newsletter. Route them with `hermes reading scan --add suggested`,"
          " or pick with `hermes reading add EMAIL ...`.")


def run(args) -> int:
    cmd = getattr(args, "reading_cmd", None)
    try:
        if cmd == "setup":
            if not auth.find_token() or args.client:
                _connect(args.client, args.manual)
            else:
                print(f"Already connected as {core.account_email()}.")
            if not core.load_senders():
                print()
                _show_candidates(core.scan())
            else:
                print(f"{len(core.load_senders())} senders routed into the label {core.label_name()}.")
        elif cmd == "status":
            st = core.status()
            if args.json:
                _print(st)
            else:
                print(f"Gmail: {'connected as ' + str(st['account']) if st['connected'] else 'not connected'}")
                print(f"OAuth client: {'found' if st['client'] else 'missing (see hermes reading setup)'}")
                print(f"Label: {st['label']}    Senders: {st['senders']}")
        elif cmd == "scan":
            found = core.scan(args.days)
            if args.add:
                pick = [c["email"] for c in found["candidates"] if args.add == "all" or c["suggest"]]
                _print(core.add_senders(pick) if pick else {"added": []})
            elif args.json:
                _print(found)
            else:
                _show_candidates(found)
        elif cmd == "senders":
            senders = core.load_senders()
            _print(senders) if args.json else print("\n".join(senders) or "(none)")
        elif cmd == "add":
            _print(core.add_senders(args.emails))
        elif cmd == "remove":
            _print(core.remove_senders(args.emails, restore=args.restore))
        elif cmd == "sync":
            _print(core.sync(dry_run=args.dry_run))
        elif cmd == "import-label":
            _print(import_label(args.label))
        elif cmd == "list":
            items = core.list_messages()
            if args.since:
                cut = time.time() - parse_since(args.since)
                items = [m for m in items if m["ts"] >= cut]
            if args.json:
                _print(items)
            else:
                for m in items:
                    print(f"{time.strftime('%m-%d %H:%M', time.localtime(m['ts']))}  {m['id']}  {m['from'][:24]:<24}  {m['subject']}")
        elif cmd == "export":
            cut = time.time() - parse_since(args.since)
            items = [m for m in core.list_messages() if m["ts"] >= cut][: args.limit]
            with ThreadPoolExecutor(6) as pool:
                texts = list(pool.map(lambda m: core.message_text(m["id"]), items))
            print(f"{len(items)} newsletters since {args.since}.\n")
            print("\n\n---\n\n".join(texts))
        elif cmd == "unsubscribe":
            _print(core.unsubscribe(args.id, trash_existing=args.trash))
        else:
            print(USAGE)
            return 2
    except (core.GmailError, auth.AuthError) as exc:
        print(f"reading: {exc}", file=sys.stderr)
        return 1
    return 0


def import_label(label: str) -> dict:
    """Move all mail of another label into Reading and route its senders (a Meco move, say)."""
    src = next((lab["id"] for lab in core._call("GET", "/labels").get("labels", [])
                if label in (lab["id"], lab["name"])), None)
    if not src:
        raise core.GmailError(f"no label named {label}")
    lid = core.label_id(create=True)
    ids = core._ids({"labelIds": src}, 100_000)
    with ThreadPoolExecutor(8) as pool:
        metas = list(pool.map(core._meta, ids))
    found = {m["email"] for m in metas if "@" in m["email"]}
    core.save_senders(set(core.load_senders()) | found)
    for batch in core.chunks(ids, 1000):
        core._call("POST", "/messages/batchModify", body={"ids": batch, "addLabelIds": [lid], "removeLabelIds": ["INBOX"]})
    return {"moved": len(ids), "senders_found": len(found), **core.sync()}
