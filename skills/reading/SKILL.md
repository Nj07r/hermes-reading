---
name: reading
description: "Use when setting up Reading or wiring a newsletter digest."
version: 1.0.0
author: Vlad Sergienko
license: MIT
metadata:
  hermes:
    tags: [reading, newsletters, gmail, digest, cron]
---

# Reading: newsletters out of the inbox

Reading moves the newsletters a user picks out of the Gmail inbox into one label (default
`Reading`) with native Gmail filters, and shows that label in a reader: the Reading page in Hermes Desktop,
or any browser through the dashboard. Everything below runs with the terminal tool.

## When to Use

- The user asks to set up Reading, connect Gmail for it, or fix its connection.
- The user wants a daily or weekly digest (text or audio) of their newsletters.
- The user asks to add, remove or find newsletter senders.
- A message contains `@newsletter:<id>`: that is one newsletter, already expanded as text.

## Setup (help the user through it)

1. `hermes reading status`. If it says connected, skip to step 4.
2. OAuth client. Reading uses the user's own Google OAuth client (free, once). Walk them
   through it, one step at a time, and wait for each:
   - Enable the Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com
   - Consent screen: https://console.cloud.google.com/auth/overview. Choose External and add
     their own address under Audience > Test users.
   - Client: https://console.cloud.google.com/auth/clients > Create client > Desktop app >
     Download JSON.
3. Connect: the user runs `hermes reading setup --client <path to the JSON>` in their own
   terminal. It saves the client and runs `hermes auth add reading:google`, the Hermes sign-in.
   You cannot run that sign-in for them: Hermes refuses it from an agent session. Add
   `--no-browser` when the browser is on another machine. Never ask the user to paste a
   password, the client secret or a code into chat.
4. Pick newsletters: `hermes reading scan` lists mailing-list senders of the last 30 days. `[x]`
   marks the ones that look like newsletters. Show the list, let the user choose, then
   `hermes reading add <emails...>`, or `hermes reading scan --add suggested` if they agree with
   the ticks. The Reading page does the same with checkboxes.

## Senders

- `hermes reading senders` lists them. `add` and `remove` change the list and update the Gmail
  filters at once. `remove --restore` also moves the sender's mail back to the inbox.
- `hermes reading unsubscribe <message id> [--trash]` unsubscribes (one-click, else the mailto
  email, else it prints the sender's page) and stops routing the sender. Ask before you run it:
  it sends a request to the sender.
- Coming from Meco or another reader with its own label: `hermes reading import-label <label>`.

## Digest jobs

Do not make a new job unless the user asks. If they already have a digest job, offer to change
its prompt so it reads Reading. Example prompt for a daily cron job:

```text
Run `hermes reading export --since 24h`. It prints every newsletter of the last day as Markdown.
Write a short digest: the 3 to 5 stories that matter most to me, one paragraph each, with the
link. Skip ads and sponsor blocks. End with one line per remaining newsletter.
```

For an audio brief, add: "Then turn the digest into speech with the text_to_speech tool and
attach the audio." Keep the spoken version under about 900 words (5 to 7 minutes).

`hermes reading list --since 24h --json` gives ids, senders and subjects only, for jobs that
pick first and read second (`@newsletter:<id>` or `hermes reading export`).

## Pitfalls

- Gmail filters need the `gmail.settings.basic` scope. A google-workspace token without it is
  not used; Reading asks for its own token with both scopes.
- `export` does not mark anything as read. Opening an email in the reader does (setting
  `mark_read`).
- The label only holds what arrived since setup plus the inbox mail that `sync` moved. Old
  archived issues stay where they are on purpose.
