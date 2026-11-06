/**
 * Corpus Open — the case panel for Hermes Desktop.
 *
 * A pane beside the chat with the case at a glance: open deadlines with passed ones first, a
 * tamper badge (every registered file re-hashed against the hash taken when it was added), and
 * how many facts have no source. A status-bar chip shows passed deadlines from anywhere.
 *
 * Data comes from this plugin's own backend (dashboard/plugin_api.py, /api/plugins/corpus-open),
 * which reads the case folder set in Settings > Plugins > corpus-open. Read-only: the panel never
 * writes to the case. It refreshes when an agent's matter_* tool finishes, and once a minute.
 *
 * Loaded uncompiled by the desktop app: no JSX, and only the three modules its import fence allows.
 */
import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'corpus-open'
const POLL_MS = 60_000
const SHOW_FAILURES = 5

// ---------- shared state (pane and chip read the same snapshot) ----------

const state = { panel: null, error: null, setup: false, loading: false, verify: { state: 'unchecked' } }
const listeners = new Set()
let rest = null

function emit() {
  for (const fn of listeners) fn({ ...state })
}

function useSnapshot() {
  const [snap, setSnap] = useState({ ...state })
  useEffect(() => {
    listeners.add(setSnap)
    return () => listeners.delete(setSnap)
  }, [])
  return snap
}

async function call(path, method) {
  const out = await rest(path, method ? { method, timeoutMs: 120_000 } : { timeoutMs: 20_000 })
  if (!out || typeof out !== 'object') throw new Error('No answer from the Corpus Open backend.')
  return out
}

async function refresh() {
  if (!rest) return
  state.loading = true
  emit()
  try {
    const out = await call('/panel')
    state.panel = out.ok ? out.result : null
    state.error = out.ok ? null : out.error
    state.setup = !out.ok && !!out.setup
  } catch (e) {
    state.panel = null
    state.setup = false
    state.error = backendError(e)
  }
  if (!state.panel) state.verify = { state: 'unchecked' } // a badge belongs to the case it checked
  state.loading = false
  emit()
}

async function verify() {
  if (!rest || state.verify.state === 'checking') return
  state.verify = { state: 'checking' }
  emit()
  try {
    const out = await call('/verify', 'POST')
    state.verify = out.ok ? out.result : { state: 'error', error: out.error }
  } catch (e) {
    state.verify = { state: 'error', error: backendError(e) }
  }
  emit()
}

function backendError(e) {
  const msg = (e && e.message) || String(e)
  return /404|not enabled|not found/i.test(msg)
    ? 'The Corpus Open backend is off. Add corpus-open to plugins.enabled in config.yaml and restart Hermes.'
    : msg
}

let refreshTimer = null
function refreshSoon() {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(refresh, 400)
}

// ---------- wording (the local web UI's words where it has them) ----------

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || one + 's'}`
}

function when(days) {
  if (days === null || days === undefined) return 'date not readable'
  if (days < 0) return `${plural(-days, 'day')} ago (passed)`
  if (days === 0) return 'today'
  if (days === 1) return 'tomorrow'
  return `in ${plural(days, 'day')}`
}

const BADGE = {
  unchecked: { tone: 'muted', title: 'Not checked yet', detail: () => 'Re-hash every file in the case folder.' },
  checking: { tone: 'muted', title: 'Checking…', detail: () => 'Re-hashing every registered file.' },
  intact: {
    tone: 'ok',
    title: 'Intact',
    detail: v => (v.checked === 1 ? 'The 1 file is unchanged since it was added.' : `All ${v.checked} files are unchanged since they were added.`)
  },
  tampered: {
    tone: 'alarm',
    title: 'Tampered',
    detail: v =>
      `${plural(v.changed, 'file')} changed` +
      (v.missing ? ` and ${v.missing} missing` : '') +
      (v.changed + v.missing === 1 ? ' since it was added.' : ' since they were added.')
  },
  missing: { tone: 'alarm', title: 'Files missing', detail: v => `${v.missing} of ${v.checked} files are gone from the case folder.` },
  empty: { tone: 'muted', title: 'Nothing to check', detail: () => 'No files are registered in this case yet.' },
  error: { tone: 'alarm', title: 'Check failed', detail: v => v.error || 'The check did not finish.' }
}

// ---------- look (Hermes theme tokens, with fallbacks for older themes) ----------

const C = {
  text: 'var(--ui-text-primary, currentColor)',
  text2: 'var(--ui-text-secondary, currentColor)',
  text3: 'var(--ui-text-tertiary, currentColor)',
  rule: 'var(--ui-stroke-secondary, rgba(127,127,127,.25))',
  card: 'var(--ui-bg-elevated, transparent)',
  ok: 'var(--ui-green, #2f9e5b)',
  alarm: 'var(--ui-red, #d1453b)',
  warn: 'var(--ui-yellow, #c99a06)'
}
const TONE = { ok: C.ok, alarm: C.alarm, muted: C.text3, warn: C.warn }

const s = {
  pane: { height: '100%', overflowY: 'auto', padding: '14px 14px 20px', color: C.text, fontSize: 13, lineHeight: 1.45 },
  head: { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 },
  title: { fontSize: 15, fontWeight: 600, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  sub: { color: C.text3, fontSize: 11.5 },
  section: { marginTop: 16 },
  label: { color: C.text3, fontSize: 11, letterSpacing: '.06em', textTransform: 'uppercase', marginBottom: 6, display: 'flex', gap: 6 },
  card: { border: `1px solid ${C.rule}`, borderRadius: 8, padding: '10px 12px', background: C.card },
  btn: {
    border: `1px solid ${C.rule}`, borderRadius: 6, background: 'transparent', color: C.text2,
    fontSize: 12, padding: '3px 9px', cursor: 'pointer'
  },
  stats: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 },
  stat: { border: `1px solid ${C.rule}`, borderRadius: 8, padding: '8px 10px' },
  num: { fontSize: 20, fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
  mono: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11.5 }
}

// ---------- pieces ----------

function TamperBadge({ v }) {
  const b = BADGE[v.state] || BADGE.error
  const tone = TONE[b.tone]
  const failures = v.failures || []
  return jsxs('div', {
    style: { ...s.card, borderLeft: `3px solid ${tone}` },
    role: b.tone === 'alarm' ? 'alert' : undefined,
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 8 },
        children: [
          jsx('span', { style: { width: 9, height: 9, borderRadius: 9, background: tone, flex: 'none' } }),
          jsx('span', { style: { fontWeight: 600, color: tone, flex: 1 }, children: b.title }),
          jsx('button', {
            type: 'button',
            style: { ...s.btn, opacity: v.state === 'checking' ? 0.5 : 1 },
            disabled: v.state === 'checking',
            onClick: () => verify(),
            children: v.state === 'unchecked' ? 'Check now' : 'Check again'
          })
        ]
      }),
      jsx('div', { style: { color: C.text2, marginTop: 4 }, children: b.detail(v) }),
      failures.length > 0 &&
        jsxs('ul', {
          style: { margin: '6px 0 0', padding: 0, listStyle: 'none' },
          children: [
            ...failures.slice(0, SHOW_FAILURES).map(f =>
              jsxs(
                'li',
                {
                  style: { ...s.mono, display: 'flex', gap: 6, marginTop: 2 },
                  children: [
                    jsx('span', { style: { color: C.alarm, flex: 'none' }, children: f.kind }),
                    jsx('span', { style: { color: C.text2, overflowWrap: 'anywhere' }, children: f.rel_path })
                  ]
                },
                f.kind + f.rel_path
              )
            ),
            failures.length > SHOW_FAILURES || v.changed + v.missing > failures.length
              ? jsx(
                  'li',
                  {
                    style: { color: C.text3, marginTop: 2 },
                    children: `and ${v.changed + v.missing - Math.min(failures.length, SHOW_FAILURES)} more`
                  },
                  'more'
                )
              : null
          ]
        })
    ]
  })
}

function Stat({ n, label, tone }) {
  return jsxs('div', {
    style: { ...s.stat, borderColor: tone ? TONE[tone] : C.rule },
    children: [
      jsx('div', { style: { ...s.num, color: tone ? TONE[tone] : C.text }, children: n }),
      jsx('div', { style: { color: C.text3, fontSize: 11.5 }, children: label })
    ]
  })
}

function DeadlineRow({ d }) {
  const tone = d.passed ? C.alarm : d.days !== null && d.days <= 3 ? C.warn : C.rule
  const meta = [d.confidence, d.verification, d.owner && `owner: ${d.owner}`].filter(Boolean).join(' · ')
  return jsxs('li', {
    style: { borderLeft: `3px solid ${tone}`, padding: '6px 0 6px 10px', borderBottom: `1px solid ${C.rule}` },
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 8, alignItems: 'baseline' },
        children: [
          jsx('span', { style: { flex: 1, fontWeight: d.passed ? 600 : 500 }, children: d.label }),
          jsx('span', { style: { ...s.mono, color: d.passed ? C.alarm : C.text2, flex: 'none' }, children: d.due_date })
        ]
      }),
      jsxs('div', {
        style: { color: d.passed ? C.alarm : C.text3, fontSize: 11.5 },
        children: [when(d.days), meta ? jsx('span', { style: { color: C.text3 }, children: ` · ${meta}` }) : null]
      })
    ]
  })
}

function Message({ title, body, action }) {
  return jsxs('div', {
    style: { ...s.card, marginTop: 12 },
    children: [
      jsx('div', { style: { fontWeight: 600, marginBottom: 4 }, children: title }),
      jsx('div', { style: { color: C.text2 }, children: body }),
      action || null
    ]
  })
}

function CasePane() {
  const snap = useSnapshot()

  useEffect(() => {
    refresh().then(() => {
      if (state.panel && state.verify.state === 'unchecked') verify()
    })
    const t = setInterval(refresh, POLL_MS)
    return () => clearInterval(t)
  }, [])

  const p = snap.panel
  if (!p) {
    return jsx('div', {
      style: s.pane,
      children: snap.error
        ? jsx(Message, {
            title: snap.setup ? 'Choose a case folder' : 'The case panel could not load',
            body: snap.error,
            action: jsx('button', { type: 'button', style: { ...s.btn, marginTop: 8 }, onClick: () => refresh(), children: 'Try again' })
          })
        : jsx('div', { style: { color: C.text3 }, children: 'Loading the case…' })
    })
  }

  const o = p.overview
  const passed = p.deadlines.filter(d => d.passed)
  return jsxs('div', {
    style: s.pane,
    children: [
      jsxs('div', {
        style: s.head,
        children: [
          jsx('div', { style: s.title, title: o.title, children: o.title }),
          jsx('button', {
            type: 'button',
            style: s.btn,
            title: 'Refresh',
            onClick: () => refresh(),
            children: snap.loading ? '…' : '↻'
          })
        ]
      }),
      jsx('div', {
        style: s.sub,
        children: `As of ${p.as_of}` + (o.next_deadline ? ` · next: ${o.next_deadline.label}, ${when(o.next_deadline.days)}` : '')
      }),

      jsxs('div', {
        style: s.section,
        children: [jsx('div', { style: s.label, children: 'Source files' }), jsx(TamperBadge, { v: snap.verify })]
      }),

      jsxs('div', {
        style: s.section,
        children: [
          jsx('div', { style: s.label, children: 'Case' }),
          jsxs('div', {
            style: s.stats,
            children: [
              jsx(Stat, { n: o.documents, label: o.documents === 1 ? 'document' : 'documents' }),
              jsx(Stat, { n: o.facts, label: o.facts === 1 ? 'fact' : 'facts' }),
              jsx(Stat, {
                n: o.unsupported_facts,
                label: 'no source',
                tone: o.unsupported_facts > 0 ? 'warn' : undefined
              })
            ]
          }),
          o.unsupported_facts > 0 &&
            jsx('div', {
              style: { color: C.warn, marginTop: 6, fontSize: 12 },
              children: `${plural(o.unsupported_facts, 'fact has', 'facts have')} no source. Back ${o.unsupported_facts === 1 ? 'it' : 'them'} with a document before relying on ${o.unsupported_facts === 1 ? 'it' : 'them'}.`
            })
        ]
      }),

      jsxs('div', {
        style: s.section,
        children: [
          jsxs('div', {
            style: s.label,
            children: [
              jsx('span', { style: { flex: 1 }, children: `Deadlines (${o.open_deadlines} open)` }),
              passed.length > 0 && jsx('span', { style: { color: C.alarm }, children: `${passed.length} passed` })
            ]
          }),
          p.deadlines.length === 0
            ? jsx('div', { style: { color: C.text3 }, children: 'No open deadlines.' })
            : jsx('ul', {
                style: { margin: 0, padding: 0, listStyle: 'none' },
                children: p.deadlines.map(d => jsx(DeadlineRow, { d }, d.id))
              }),
          o.waiting_triggers > 0 &&
            jsx('div', {
              style: { color: C.text3, marginTop: 8, fontSize: 12 },
              children: `${plural(o.waiting_triggers, 'deadline')} wait${o.waiting_triggers === 1 ? 's' : ''} on an event that has not happened yet.`
            })
        ]
      }),

      jsx('div', {
        style: { ...s.sub, marginTop: 18 },
        children: 'Dates marked working-estimate or unverified are not confirmed. Read-only view of your case folder.'
      })
    ]
  })
}

function PassedChip() {
  const snap = useSnapshot()
  const p = snap.panel
  const n = p ? p.overview.passed_deadlines : 0
  const tampered = snap.verify.state === 'tampered' || snap.verify.state === 'missing'
  if (!n && !tampered) return null
  const text = [n ? `${n} passed` : null, tampered ? BADGE[snap.verify.state].title.toLowerCase() : null]
    .filter(Boolean)
    .join(' · ')
  return jsx('span', {
    title: 'Corpus Open: open the Case pane for details',
    style: { padding: '0 6px', fontSize: 11, color: C.alarm, fontWeight: 600 },
    children: `⚖ ${text}`
  })
}

// ---------- registration ----------

export default {
  id: ID,
  name: 'Corpus Open',
  register(ctx) {
    rest = ctx.rest
    ctx.register({
      id: 'case',
      area: sdk.PANES_AREA || 'panes',
      title: 'Case',
      data: { placement: 'right', width: '340px' },
      render: () => jsx(CasePane, {})
    })
    const right = sdk.STATUSBAR_AREAS && sdk.STATUSBAR_AREAS.right
    if (right) {
      ctx.register({ id: 'passed', area: right, order: 140, render: () => jsx(PassedChip, {}) })
    }
    // A matter_* tool just ran (a fact added, a trigger fired, a file ingested): show it now.
    ctx.onEvent('tool.complete', ev => {
      let text = ''
      try {
        text = JSON.stringify(ev)
      } catch (e) {
        return
      }
      if (text.includes('matter_')) {
        refreshSoon()
        if (/matter_(ingest_path|verify)/.test(text)) verify()
      }
    })
    refresh()
  }
}
