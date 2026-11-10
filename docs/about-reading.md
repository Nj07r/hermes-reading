# About Reading

## The problem

A newsletter is something you choose to read. Most of the other mail in an inbox is something
you must deal with. When both share one list, the newsletters lose: they wait under receipts and
alerts, and a full inbox makes reading feel like work. Readers such as Meco fix this with their
own servers and apps. Reading does the same with parts that you already own: your Gmail, your
Google client and your Hermes.

## How the parts work together

Reading has four parts.

- **Gmail filters** do the moving. For every 20 senders, Reading writes one filter that adds the
  label and removes the inbox. Gmail runs the filters on its own servers, so new issues move
  even when your computer is off. The filters also take listed senders out of spam.
- **The core** (`reading_core.py`) keeps the sender list, writes the filters, scans the inbox,
  lists and shows the label, and unsubscribes. It talks to the Gmail REST API directly and uses
  the Python standard library only.
- **The reader** (`page.html`) is one page for every screen. In Hermes Desktop it is a page in
  the main area (sidebar row **Reading**). In a browser it runs through the Hermes dashboard, so it also works on a phone.
- **The agent half** gives Hermes the `hermes reading` command, the `@newsletter:` reference
  and a skill. With these, Hermes can set Reading up, change senders, and read newsletters for
  digests.

## Why native filters, and not a sync job

A sync job must run to move mail, so it misses mail when the machine sleeps, and it needs a
place to run. Gmail filters have neither problem. The cost is a limit: one filter query can hold
only so many addresses. Reading puts 20 senders in each filter and replaces its own filters when
the list changes. It never touches filters that it did not create.

## Why your own Google client

Gmail access through a shared client means one company can read the mail of every user. Google
also asks every app with that access for a yearly security review. With your own client, the
token belongs to you, and Reading sends your mail to no one. The cost is a three-minute setup in
Google Cloud Console.

Hermes, not Reading, runs the sign-in and holds the token. Reading declares the client in
`plugin.yaml` (`oauth:`), and every Gmail call goes through Hermes's
`credentialed_provider_request`, which adds the token and sends it only to
`gmail.googleapis.com`. Reading's code never sees the token or the refresh token. A bug in Reading
cannot leak them, and `hermes auth logout reading:google` signs you out in one place.

## Why the scan only suggests

Mailing-list headers (`List-Unsubscribe`, `List-Id`) also come with bank statements, shop
receipts and security alerts. If those disappeared from the inbox, Reading would do harm. So the
scan ticks a sender only when it looks like a newsletter: a known newsletter platform, or a list
with two or more issues. You see every sender it found and decide.

## Why Desktop shows the web page

Hermes Desktop and the phone use the same `page.html`. One reader means one set of keys, one
dark mode and one set of fixes for difficult newsletters. In Desktop, a small bridge gives the
page what a browser tab gives it elsewhere: the API, the clipboard, the system browser for links,
and a door to a new Hermes chat.

Emails never run code. Each email renders in its own frame, after Reading removes scripts, event
attributes and forms, and a content security policy blocks the rest.
