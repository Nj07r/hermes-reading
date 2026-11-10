# Reading reference

## Commands

All commands start with `hermes reading`.

| Command | Effect |
|---|---|
| `setup [--client FILE] [--manual]` | Saves the OAuth client (if given), connects Gmail, then lists the newsletters that the scan found. `--manual` prints the Google address and reads the redirect address from the terminal. |
| `status [--json]` | Shows the connection, the account, the label and the sender count. |
| `scan [--days N] [--add suggested\|all] [--json]` | Lists the mailing-list senders in the inbox of the last N days. `--add` routes the suggested senders or all of them. |
| `senders [--json]` | Lists the routed senders. |
| `add EMAIL...` | Routes senders. Updates the Gmail filters and moves their inbox mail into the label. |
| `remove EMAIL... [--restore]` | Stops routing senders. `--restore` moves their mail in the label back to the inbox. |
| `sync [--dry-run]` | Makes the Gmail filters match the sender list and moves listed senders' inbox mail. |
| `import-label LABEL` | Moves all mail of another label (name or id) into the Reading label and routes its senders. |
| `list [--since 24h] [--json]` | Lists the newsletters in the label: time, id, sender, subject. |
| `export [--since 24h] [--limit 30]` | Prints the newsletters as Markdown text. Does not mark them as read. |
| `unsubscribe ID [--trash]` | Unsubscribes from the sender of message ID and stops routing the sender. `--trash` moves their mail in the label to the Gmail Trash. |

`--since` takes a number and a unit: `m`, `h`, `d` or `w`.

## Settings

Desktop **Settings > Plugins > Reading**, or `config.yaml` under `plugins.entries.reading.settings`.

| Key | Default | Meaning |
|---|---|---|
| `label_name` | `Reading` | The Gmail label for newsletters. Reading creates it when it is missing. |
| `scan_days` | `30` | How far back the newsletter scan reads the inbox. |
| `list_limit` | `300` | The most recent emails of the label that the reader shows. |
| `mark_read` | `true` | Opening an email in the reader marks it as read. |
| `token_path` | empty | A Google token file to use. Empty means automatic (see Files). |

## Files

`HERMES_HOME` is `~/.hermes` unless you use a Hermes profile.

| Path | Content |
|---|---|
| `HERMES_HOME/reading/senders.json` | The routed senders. A sorted JSON array of lower-case addresses. |
| `HERMES_HOME/reading/state.json` | The ids of the Gmail filters that Reading owns, and a hash of the last synced list. |
| `HERMES_HOME/reading/client_secret.json` | The Google OAuth client, mode 600. |
| `HERMES_HOME/reading/token.json` | Reading's Google token, mode 600. |
| `HERMES_HOME/google_client_secret.json` | Used when Reading has no client of its own (google-workspace skill). |
| `HERMES_HOME/google_token.json` | Used when Reading has no token of its own and this token has both scopes. Reading never writes it. |

## Gmail

| Item | Value |
|---|---|
| Scopes | `gmail.modify` (list, read, label, trash, send the unsubscribe email), `gmail.settings.basic` (filters) |
| Filters | One per 20 senders. Criteria `from:(a OR b OR ...)`. Action: add the label, remove `INBOX`, `SPAM` and `IMPORTANT`. |
| Scan | `in:inbox newer_than:<scan_days>d`, at most 500 emails. A sender counts when its mail has `List-Unsubscribe` or `List-Id`. |
| Suggested | The sender is on a known newsletter platform, or has `List-Id` and 2 or more emails. |
| Unsubscribe | One-click POST when the email has `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. Else the `mailto:` address gets an email from your account. Else the page returns the `https:` address for you to open. |

## Context reference

`@newsletter:<message id>` expands to the newsletter as Markdown: subject, sender, date, web
version, Gmail link, and the text with links. The text stops at 40,000 characters. Autocomplete
searches the subjects and senders of the 100 newest emails in the label.

## Page API

`dashboard/plugin_api.py` serves `/api/plugins/reading/`. The dashboard sign-in applies.

| Route | Purpose |
|---|---|
| `GET /app` | The reader page (`page.html`). |
| `GET\|POST /call/<name>?id=<arg>` | Calls `reading_core.api_<name>(arg, body)`. A POST body is JSON. Errors return status 502 and `{"ok": false, "error": "..."}`. |

| Call | Body | Returns |
|---|---|---|
| `page` | | `html`: the text of `page.html` (the Desktop pane uses it). |
| `status` | | `client`, `connected`, `account`, `label`, `senders`, `ready`, `pending`. |
| `client` | `{"client": <JSON or text>}` | Saves the OAuth client. |
| `auth_start` | | `url`: the Google sign-in address. Starts a one-shot listener on `127.0.0.1`. |
| `auth_finish` | `{"url": "<redirect address>"}` | Finishes the sign-in from a pasted address. |
| `scan` | `{"days": N}` (optional) | `scanned`, `days`, `candidates`: `email`, `name`, `count`, `last_subject`, `last_ts`, `suggest`. |
| `senders` | | `senders`: `email`, `name`, `count` (emails in the label). |
| `senders_add` | `{"emails": [...]}` | The sync report and `added`. |
| `senders_remove` | `{"emails": [...], "restore": bool}` | The sync report, `removed`, and `restored` when asked. |
| `messages` | | `items`: `id`, `threadId`, `from`, `email`, `subject`, `snippet`, `ts`, newest first. |
| `message` | | The item fields plus `html`, `gmail`, `web`, `minutes`, `dup`, `unsubscribe`. Marks the email as read when `mark_read` is on. |
| `trash`, `untrash` | | Moves the email to the Gmail Trash, or back. |
| `unsubscribe` | `{"trash": bool}` | `method` (`one-click`, `email` or `link`), `url` for `link`, `trashed`. |

## Desktop pane bridge

`desktop/plugin.js` shows `page.html` in a frame and puts `window.__readingHost` on the Desktop
window. The page finds it as `parent.__readingHost`.

| Member | Effect |
|---|---|
| `call(name, id, method, body)` | The page API call, through the plugin REST door. |
| `open(url)` | Opens an `http` or `https` address in the system browser. |
| `copy(text)` | Writes the system clipboard. |
| `count(n)` | Sets the number in the pane tab. |
| `ask(id)` | Opens a new chat with `@newsletter:<id>` in the composer. |
| `chat(text)` | Opens a new chat with the text in the composer. |
