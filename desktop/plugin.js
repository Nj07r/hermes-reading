/**
 * Reading for Hermes Desktop: the same reader page as the web app, as a full page in the main
 * area (sidebar row "Reading", route /reading), so the list and the email sit side by side.
 *
 * The pane asks the backend for page.html (/call/page) and shows it in a srcdoc frame. The page
 * finds parent.__readingHost (same origin) and uses it instead of fetch, window.open and the clipboard:
 *   call  -> ctx.rest('/call/<name>?id=')  (the plugin REST door, Desktop auth applies)
 *   open  -> ctx.os.openExternal(url)      (links open in the system browser)
 *   copy  -> ctx.os.writeClipboard(text)
 *   count -> the number of waiting emails (kept for the sidebar)
 *   ask   -> a new chat with "@newsletter:<id>" in the composer (the agent half expands it)
 *   chat  -> a new chat with a prompt in the composer
 * Emails still render in the page's own nested frames: sanitised, CSP script-src 'none'.
 */
import { host, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'reading'
const PATH = '/reading'

let pluginCtx = null

let count = 0
const setCount = n => {
  count = Number(n) || 0
}

// A fresh chat draft mounts a moment after newChat(): retry until its composer takes the text.
async function draftInNewChat(text) {
  host.newChat()
  for (let i = 0; i < 25; i++) {
    await new Promise(r => setTimeout(r, 120))
    if (await host.composer.setDraft('new', text)) {
      host.composer.focus('new')
      return true
    }
  }
  host.notify({ kind: 'error', message: 'Reading: could not open a new chat draft' })
  return false
}

const bridge = {
  call: (name, id = '', method = 'GET', body) =>
    pluginCtx.rest(`/call/${encodeURIComponent(name)}?id=${encodeURIComponent(id)}`, {
      method,
      body,
      timeoutMs: 120_000
    }),
  ask: id => draftInNewChat(`@newsletter:${id} `),
  chat: text => draftInNewChat(String(text || '')),
  open: url => (/^https?:/i.test(url || '') ? pluginCtx.os.openExternal(url) : Promise.resolve(false)),
  copy: text => pluginCtx.os.writeClipboard(String(text || '')),
  count: setCount
}

// Match the page background to the app, so the pane does not look like a pasted-in window.
function themeStyle() {
  const root = getComputedStyle(document.documentElement)
  const bg = getComputedStyle(document.body).backgroundColor
  const accent = root.getPropertyValue('--ui-accent').trim()
  const vars = [bg && `--bg:${bg}`, accent && `--accent:${accent}`].filter(Boolean).join(';')
  return vars ? `<style>:root{${vars}}</style>` : ''
}

function paneDoc(html) {
  const head = themeStyle()
  return html
    .replace(/<link rel="(manifest|icon|apple-touch-icon)"[^>]*>/gi, '') // web-app only, would 404 here
    .replace(/<head>/i, m => m + head)
}

// The page HTML is fetched once per app session; leaving and coming back does not refetch it.
let cachedDoc = null

function ReadingPage() {
  const [doc, setDoc] = useState(cachedDoc)
  const [error, setError] = useState(null)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (cachedDoc && !nonce) return
    let live = true
    setError(null)
    bridge
      .call('page')
      .then(res => {
        if (!live) return
        if (!res?.html) throw new Error(res?.error || 'No page from the Reading backend')
        cachedDoc = paneDoc(res.html)
        setDoc(cachedDoc)
      })
      .catch(err => live && setError(err?.message || String(err)))
    return () => {
      live = false
    }
  }, [nonce])

  if (error) {
    return jsxs('div', {
      className: 'flex h-full flex-col items-start gap-2 p-4 text-sm text-(--ui-text-secondary)',
      children: [
        jsx('div', { children: `Reading is unreachable: ${error}` }),
        jsx('button', {
          type: 'button',
          className: 'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs hover:bg-(--chrome-action-hover)',
          onClick: () => setNonce(n => n + 1),
          children: 'Retry'
        })
      ]
    })
  }
  if (!doc) return jsx('div', { className: 'p-4 text-sm text-(--ui-text-tertiary)', children: 'Loading…' })

  // allow-same-origin: the page reads its own email frames (height, dark mode) and the bridge.
  // No allow-popups / allow-top-navigation: every link goes through bridge.open.
  return jsx('iframe', {
    key: nonce,
    title: 'Reading',
    srcDoc: doc,
    sandbox: 'allow-scripts allow-same-origin',
    className: 'block size-full border-0',
    style: { colorScheme: 'dark' }
  })
}

export default {
  id: ID,
  name: 'Reading',
  register(ctx) {
    pluginCtx = ctx
    window.__readingHost = bridge
    ctx.onDispose?.(() => {
      if (window.__readingHost === bridge) delete window.__readingHost
    })
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      title: 'Reading',
      data: { path: PATH },
      render: () => jsx(ReadingPage, {})
    })
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: PATH, label: 'Reading', codicon: 'book' }
    })
  }
}
