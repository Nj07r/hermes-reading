/**
 * Reading for Hermes Desktop: the same reader page as the web app, as a full page in the main
 * area (sidebar row "Reading", route /reading), so the list and the email sit side by side.
 *
 * The pane asks the backend for page.html (/call/page) and shows it in the SDK's SandboxedFrame:
 * an opaque origin with no reach into the app, its storage or the bridge. The page posts
 * { type: 'reading-call', seq, op, args } to this window and gets { type: 'reading-reply', seq, result | error }:
 *   call      -> ctx.rest('/call/<name>?id=')  (the plugin REST door, Desktop auth applies)
 *   open      -> ctx.os.openExternal(url)      (links open in the system browser)
 *   copy      -> ctx.os.writeClipboard(text)
 *   count     -> the number of waiting emails (kept for the sidebar)
 *   ask       -> a new chat with "@newsletter:<id>" in the composer (the agent half expands it)
 *   chat      -> a new chat with a prompt in the composer
 *   savePrefs -> keeps the page's dark/original choices (the page has no storage of its own)
 * Emails render in the page's own nested frames: sanitised, opaque origin, and a CSP that runs only the
 * page's nonce-pinned helper (size, keys, link clicks by postMessage), never the email's own script.
 */
import { host, ROUTES_AREA, SandboxedFrame, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'reading'
const PATH = '/reading'
const PREFS = 'reading.prefs'

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

// Prefs moved here from the page's own localStorage, which an opaque-origin frame does not have.
function loadPrefs() {
  const saved = pluginCtx.storage.get('prefs', null)
  if (saved) return saved
  try {
    return JSON.parse(localStorage.getItem(PREFS) || '{}')
  } catch {
    return {}
  }
}

const bridge = {
  call: (name, id = '', method = 'GET', body) => {
    if (!/^[a-z_]+$/.test(String(name)) || !['GET', 'POST'].includes(method)) {
      return Promise.reject(new Error('Reading: bad call'))
    }
    return pluginCtx.rest(`/call/${name}?id=${encodeURIComponent(String(id ?? ''))}`, {
      method,
      body,
      timeoutMs: 120_000
    })
  },
  ask: id => draftInNewChat(`@newsletter:${String(id)} `),
  chat: text => draftInNewChat(String(text || '')),
  open: url => (/^https?:/i.test(url || '') ? pluginCtx.os.openExternal(String(url)) : Promise.resolve(false)),
  copy: text => pluginCtx.os.writeClipboard(String(text || '')),
  count: setCount,
  savePrefs: prefs => {
    pluginCtx.storage.set('prefs', prefs && typeof prefs === 'object' ? prefs : {})
    return true
  }
}

// Match the page background to the app, so the pane does not look like a pasted-in window.
function themeStyle() {
  const root = getComputedStyle(document.documentElement)
  const bg = getComputedStyle(document.body).backgroundColor
  const accent = root.getPropertyValue('--ui-accent').trim()
  const vars = [bg && `--bg:${bg}`, accent && `--accent:${accent}`].filter(Boolean).join(';')
  return vars ? `<style>:root{${vars}}</style>` : ''
}

const attr = s => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

function paneDoc(html) {
  const head = themeStyle()
  return html
    .replace(/<link rel="(manifest|icon|apple-touch-icon)"[^>]*>/gi, '') // web-app only, would 404 here
    .replace(/<head>/i, m => m + head)
}

// The page HTML is fetched once per app session; leaving and coming back does not refetch it.
let cachedDoc = null

function frameSrc(doc) {
  const marker = `<meta name="reading-host" content="desktop" data-prefs="${attr(JSON.stringify(loadPrefs()))}">`
  return `data:text/html;charset=utf-8,${encodeURIComponent(doc.replace(/<head>/i, m => m + marker))}`
}

function ReadingPage() {
  const [doc, setDoc] = useState(cachedDoc)
  const [error, setError] = useState(null)
  const [nonce, setNonce] = useState(0)
  const frame = useRef(null)
  const src = useMemo(() => (doc ? frameSrc(doc) : ''), [doc, nonce])

  useEffect(() => {
    const onMessage = async e => {
      const d = e.data
      const source = e.source
      if (!source || source !== frame.current?.contentWindow || d?.type !== 'reading-call') return
      let reply
      try {
        const fn = Object.hasOwn(bridge, d.op) ? bridge[d.op] : null
        if (!fn) throw new Error(`Reading: unknown op ${d.op}`)
        reply = { result: await fn(...(Array.isArray(d.args) ? d.args : [])) }
      } catch (err) {
        reply = { error: err?.message || String(err) }
      }
      source.postMessage({ type: 'reading-reply', seq: d.seq, ...reply }, '*')
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

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

  // SandboxedFrame's default posture (allow-scripts, opaque origin). Every link goes through bridge.open.
  return jsx(SandboxedFrame, {
    key: nonce,
    ref: frame,
    title: 'Reading',
    src,
    className: 'block size-full border-0',
    style: { colorScheme: 'dark' }
  })
}

export default {
  id: ID,
  name: 'Reading',
  register(ctx) {
    pluginCtx = ctx
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
