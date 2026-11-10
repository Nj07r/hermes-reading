# Reading for Hermes

**Your newsletters, out of the inbox and into a reader built for them.**

Newsletters are the good part of email, and the inbox is the worst place to read them. Reading
moves the senders you pick into one Gmail label and gives that label a calm, fast reader: a page in
Hermes Desktop, and a web app on your phone. Hermes reads along with you.


## Why it is different

- **Setup finds your newsletters for you.** Connect Gmail, and Reading scans your inbox for
  mailing-list mail and ticks the ones that look like newsletters. Untick the receipts, press
  one button, done. Add or remove a sender by hand at any time.
- **Gmail does the moving, not a server.** Reading writes native Gmail filters. New issues skip
  the inbox even when Hermes is off, and your mail never passes through a third party. To undo
  everything, remove the filters.
- **Ask Hermes about any issue.** One button opens a chat with the newsletter attached. Ask
  for the gist, argue with it, turn it into a task, save it to your notes. In any chat, type
  `@newsletter:` to attach one by subject.
- **Unsubscribe in one tap.** Reading uses the sender's one-click unsubscribe (RFC 8058), or
  sends the unsubscribe email for you, and stops routing the sender.
- **A reader, not a mail client.** Swipe or press `j`/`k` through issues, `d` to delete with
  Undo, `t` for dark or original colors (remembered per sender). Emails that ship their own dark
  design keep it.
- **Digests with your own agent.** `hermes reading export --since 24h` gives a cron job the day's
  newsletters as Markdown. The bundled skill shows Hermes how to turn them into a text digest or
  an audio brief.
- **Your Google client, your token.** Reading talks to the Gmail API with an OAuth client that
  you own. No telemetry. Public Hermes APIs only.

## Install

```bash
hermes plugins install Nj07r/hermes-reading
hermes plugins enable reading
```

Restart the gateway. In Hermes Desktop, open **Settings > Plugins** and switch on **Reading**.
**Reading** appears in the sidebar, under Archive.

## Set up

Open **Reading** in the sidebar. It walks you through three steps:

1. **Add a Google OAuth client.** Free, about 3 minutes, once: enable the Gmail API, set up the
   consent screen with yourself as a test user, create a **Desktop app** client and choose its
   JSON file on the page. If you use the google-workspace skill, Reading finds its client and
   skips this step.
2. **Connect Gmail.** Press **Connect Gmail** and allow access in the browser.
3. **Pick newsletters.** Check the list that the scan found and press **Move**.

Prefer the terminal, or no Desktop? `hermes reading setup` does the same. Or ask Hermes: "set up
Reading for me". The bundled skill knows the steps.

## Use it

| Where | How |
|---|---|
| Hermes Desktop | **Reading** in the sidebar: the list and the email side by side in the main area. |
| Browser and phone | `http://<your dashboard>/dashboard-plugins/reading/open.html`. On a phone, add it to the home screen. |
| Any chat | `@newsletter:` and a few letters of the subject. |
| Terminal and cron | `hermes reading list`, `export`, `senders`, `add`, `remove`, `unsubscribe`. |

Keys in the reader: `j` `k` move, `d` delete, `z` undo, `a` ask Hermes, `s` senders, `c` copy
link, `o` open in Gmail, `t` colors, `r` refresh, `?` all keys.

## Coming from Meco

`hermes reading import-label <Meco's label>` moves its mail into Reading and adds its senders.
Then disconnect Meco and run `hermes reading sync` once, in case Meco moved mail back to the
inbox.

## Documentation

- [Reference](docs/reference.md): commands, settings, files, Gmail scopes, page API.
- [About Reading](docs/about-reading.md): how the parts work together, and why.

## Privacy

Reading calls only the Gmail API, the Google sign-in endpoints, and, when you press
Unsubscribe, the sender's unsubscribe address. Your Google client and token stay in
`HERMES_HOME/reading/` (mode 600). The `@newsletter:` reference sends the newsletter text to
your model provider, like any text you paste into a chat.

## Development

```bash
git clone https://github.com/Nj07r/hermes-reading && cd hermes-reading
ln -s "$PWD" ~/.hermes/plugins/reading
~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests
hermes plugins validate . && hermes plugins doctor . --ci
```

`page.html` and `reading_core.py` reload on each request: no gateway restart needed. Changes to
`dashboard/plugin_api.py` or `__init__.py` need one. After a change to `desktop/plugin.js`, reload
Desktop with `cmd+shift+r`.

MIT license.
