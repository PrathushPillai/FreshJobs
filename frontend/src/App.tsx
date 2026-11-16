import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, BackendDown, timeAgo, type User } from './api'
import { AccountCard, AlertsCard, VerifyEmailBanner } from './components/AccountSettings'
import Admin from './components/Admin'
import AuthScreen from './components/AuthScreen'
import Companies from './components/Companies'
import Elsewhere from './components/Elsewhere'
import Feed from './components/Feed'
import Profile from './components/Profile'
import Settings from './components/Settings'
import { PrivacyDialog, SupportDialog } from './components/SupportDialog'
import Tracker from './components/Tracker'

const ALL_TABS = ['Feed', 'My profile', 'LinkedIn & more', 'Tracker', 'Sources', 'Settings', 'Admin'] as const
type Tab = (typeof ALL_TABS)[number]
const ADMIN_TABS: Tab[] = ['Sources', 'Admin']

/** Links in emails: ?verify=… (confirm the email), ?reset=… (new password), ?tab=profile (alert settings). */
function readLink() {
  const p = new URLSearchParams(window.location.search)
  return { verify: p.get('verify'), reset: p.get('reset'), tab: p.get('tab') }
}
function clearLink() {
  window.history.replaceState(null, '', window.location.pathname)
}

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand-400 via-brand-600 to-fuchsia-600 shadow-lg shadow-brand-600/40" aria-hidden>
        <svg viewBox="0 0 24 24" className="h-5 w-5 fill-white drop-shadow">
          <path d="M13.5 2 4 14h6.5L9 22l11-13h-6.6z" />
        </svg>
        <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-emerald-400 ring-2 ring-ink" />
      </span>
      <div className="leading-none">
        <h1 className="font-display text-lg font-extrabold tracking-tight text-white">
          Fresh<span className="bg-gradient-to-r from-emerald-300 to-teal-300 bg-clip-text text-transparent">Jobs</span>
        </h1>
        <p className="mt-0.5 hidden text-[10px] font-medium uppercase tracking-[0.14em] text-brand-200/70 sm:block">Apply first. Get seen first.</p>
      </div>
    </div>
  )
}

function Stat({ value, label, accent = false, title }: { value: number; label: string; accent?: boolean; title?: string }) {
  return (
    <span title={title} className="flex items-baseline gap-1.5 rounded-full bg-white/5 px-3 py-1 ring-1 ring-white/10">
      <b className={`font-display text-sm font-bold ${accent ? 'text-emerald-300' : 'text-white'}`}>{value.toLocaleString()}</b>
      <span className="text-[11px] text-slate-400">{label}</span>
    </span>
  )
}

function UserMenu({ user, onProfile }: { user: User; onProfile: () => void }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      queryClient.clear()
      queryClient.setQueryData(['me'], null)
    },
  })
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  const initial = (user.name || user.email)[0].toUpperCase()
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-full bg-white/5 py-1 pl-1 pr-3 text-sm font-semibold text-white ring-1 ring-white/10 hover:bg-white/10"
        aria-label="Account menu"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand-400 to-fuchsia-500 font-display text-xs font-bold">{initial}</span>
        <span className="hidden max-w-28 truncate sm:inline">{user.name || user.email.split('@')[0]}</span>
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-64 animate-rise rounded-2xl bg-white p-2 text-slate-800 shadow-2xl shadow-slate-900/20 ring-1 ring-slate-200">
          <div className="border-b border-slate-100 px-3 py-2.5">
            <p className="truncate font-semibold">{user.name || 'Your account'}</p>
            <p className="truncate text-xs text-slate-500">{user.email}</p>
          </div>
          <button onClick={() => { setOpen(false); onProfile() }} className="mt-1 block w-full rounded-xl px-3 py-2 text-left text-sm hover:bg-slate-50">
            🎯 My profile, alerts and password
          </button>
          <button onClick={() => logout.mutate()} className="block w-full rounded-xl px-3 py-2 text-left text-sm text-rose-700 hover:bg-rose-50">
            ↩ Log out
          </button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const queryClient = useQueryClient()
  const [link] = useState(readLink)
  const [tab, setTab] = useState<Tab>(link.tab === 'profile' ? 'My profile' : link.tab === 'admin' ? 'Admin' : 'Feed')
  useEffect(() => {
    if (link.tab && !link.verify && !link.reset) clearLink()  // ?tab=… opened the tab; keep the address clean
  }, [link])
  const [notice, setNotice] = useState<string | null>(null)
  const [dialog, setDialog] = useState<'support' | 'privacy' | null>(null)
  const [leaving, setLeaving] = useState(false)
  const goTo = (t: Tab) => {
    setTab(t)
    window.scrollTo(0, 0)
  }
  const me = useQuery({ queryKey: ['me'], queryFn: () => api.me().catch(() => null), retry: false, staleTime: 5 * 60_000 })
  const user = me.data ?? null

  // Any request answered 401 (session expired, logged out elsewhere): back to the login screen.
  useEffect(() => {
    const out = () => queryClient.setQueryData(['me'], null)
    window.addEventListener('freshjobs:logged-out', out)
    return () => window.removeEventListener('freshjobs:logged-out', out)
  }, [queryClient])

  // A confirmation link from the welcome email.
  const verified = useRef(false)
  useEffect(() => {
    if (!link.verify || verified.current) return
    verified.current = true
    api.verifyEmail(link.verify)
      .then(() => {
        setNotice('✓ Email confirmed. You can switch on job alerts in My profile.')
        // Reload the account (not set it from the reply: the link may be opened on a device that isn't logged in).
        queryClient.invalidateQueries({ queryKey: ['me'] })
        queryClient.invalidateQueries({ queryKey: ['alert-settings'] })
      })
      .catch((e: Error) => setNotice(e.message))
      .finally(clearLink)
  }, [link.verify, queryClient])

  const status = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    retry: false,
    enabled: !!user,
    // While the backend is starting (or down), ask every 5 s so the page loads as soon as it's up.
    refetchInterval: (q) => (q.state.status === 'error' ? 5_000 : q.state.data?.polling ? 10_000 : 30_000),
  })
  const poll = useMutation({
    mutationFn: api.poll,
    onSuccess: () => setTimeout(() => queryClient.invalidateQueries({ queryKey: ['status'] }), 500),
  })
  const s = status.data

  // When the backend comes back (after starting up), reload everything that failed meanwhile.
  const wasDown = useRef(false)
  useEffect(() => {
    if (status.isError) wasDown.current = true
    else if (wasDown.current && status.isSuccess) {
      wasDown.current = false
      queryClient.invalidateQueries()
    }
  }, [status.isError, status.isSuccess, queryClient])

  // When a check finishes (polling flips true -> false), reload everything so new jobs show up.
  const wasPolling = useRef(false)
  useEffect(() => {
    if (wasPolling.current && s && !s.polling) queryClient.invalidateQueries()
    wasPolling.current = !!s?.polling
  }, [s, queryClient])

  if (me.isLoading) {
    return (
      <div className="hero flex min-h-screen items-center justify-center">
        <span className="h-8 w-8 animate-spin rounded-full border-4 border-white/20 border-t-white" aria-label="Loading" />
      </div>
    )
  }
  if (!user || link.reset) {
    return (
      <AuthScreen
        resetToken={link.reset}
        onDone={() => {
          if (link.reset) {
            clearLink()
            link.reset = null
          }
        }}
      />
    )
  }

  const tabs = ALL_TABS.filter((t) => user.is_admin || !ADMIN_TABS.includes(t))
  if (!tabs.includes(tab)) setTimeout(() => goTo('Feed'))  // e.g. "View as user" from the Admin tab
  // Leaving the view swaps the login cookie: reload, so every part of the page starts again as the admin.
  // (Clearing the query cache instead left parts of the page on the viewed user, and repeated clicks raced.)
  const stopViewing = async () => {
    if (leaving) return
    setLeaving(true)
    try {
      await api.stopViewAs()
    } finally {
      window.location.assign('/?tab=admin')
    }
  }
  const live = !!s && (s.polling || s.slow_polling)
  return (
    <div className="min-h-screen">
      {user.viewing_as && (
        <div className="sticky top-0 z-30 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 bg-gradient-to-r from-fuchsia-600 via-brand-600 to-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-lg">
          <span>👁 Viewing FreshJobs as <b>{user.name || user.email}</b> ({user.email}) · read-only: changes are blocked</span>
          <button onClick={stopViewing} disabled={leaving} className="rounded-full bg-white px-3.5 py-1 text-xs font-bold text-brand-800 hover:bg-brand-50 disabled:opacity-70">
            {leaving ? 'Leaving…' : 'Exit view'}
          </button>
        </div>
      )}
      <header className="sticky top-0 z-20 border-b border-white/10 bg-ink/90 text-white shadow-xl shadow-brand-950/20 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Logo />
          <nav className="no-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-full bg-white/5 p-1 ring-1 ring-white/10">
            {tabs.map((t) => (
              <button
                key={t}
                onClick={() => goTo(t)}
                className={`flex items-center whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-semibold transition ${
                  tab === t ? 'bg-white text-ink shadow' : 'text-slate-300 hover:bg-white/10 hover:text-white'
                }`}
              >
                {t}
                {t === 'Tracker' && s?.tracked ? (
                  <span className="ml-1.5 rounded-full bg-gradient-to-r from-brand-500 to-fuchsia-500 px-1.5 text-[10px] font-bold text-white">{s.tracked}</span>
                ) : null}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs">
            {s && (
              <span className="hidden items-center gap-2 lg:flex">
                <Stat value={s.open_jobs} label="open jobs" title={`From ${s.companies.toLocaleString()} company boards and job feeds`} />
                <Stat value={s.new_last_24h} label="new today" accent />
              </span>
            )}
            <span
              className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1 font-medium text-slate-300 ring-1 ring-white/10"
              title={s?.last_poll ? `Last full check finished ${timeAgo(s.last_poll.finished_at)}` : undefined}
            >
              <span className="relative flex h-2 w-2">
                {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${status.isError ? 'bg-rose-500' : 'bg-emerald-400'}`} />
              </span>
              {status.isError ? (status.error instanceof BackendDown ? 'Starting…' : 'Error') : live ? 'Scanning' : 'Live'}
            </span>
            {user.is_admin && (
              <button
                onClick={() => poll.mutate()}
                disabled={s?.polling || poll.isPending}
                className="rounded-full bg-gradient-to-r from-brand-500 to-fuchsia-500 px-3.5 py-1.5 font-semibold text-white shadow-lg shadow-brand-600/30 transition hover:brightness-110 disabled:from-white/10 disabled:to-white/10 disabled:text-slate-400 disabled:shadow-none"
              >
                {s?.polling ? 'Checking…' : '⚡ Check now'}
              </button>
            )}
            <UserMenu user={user} onProfile={() => goTo('My profile')} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        {notice && (
          <div className="mb-4 flex items-center gap-3 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800 ring-1 ring-emerald-200">
            <span className="flex-1">{notice}</span>
            <button onClick={() => setNotice(null)} className="font-semibold" aria-label="Dismiss">✕</button>
          </div>
        )}
        <VerifyEmailBanner user={user} />
        {status.isError &&
          (status.error instanceof BackendDown ? (
            <p className="mb-4 flex items-center gap-3 rounded-xl bg-brand-50 px-4 py-3 text-sm text-brand-800 ring-1 ring-brand-200">
              <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-brand-300 border-t-brand-700" aria-hidden />
              {status.error.message}
            </p>
          ) : (
            <p className="mb-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-rose-200">
              The backend answered with an error: {status.error.message}
            </p>
          ))}
        {tab === 'Feed' && <Feed stats={s} onOpenProfile={() => goTo('My profile')} />}
        {tab === 'My profile' && (
          <div className="space-y-5">
            <Profile onSeeMatches={() => goTo('Feed')} />
            <AlertsCard />
            <AccountCard user={user} />
          </div>
        )}
        {tab === 'LinkedIn & more' && <Elsewhere />}
        {tab === 'Tracker' && <Tracker />}
        {tab === 'Sources' && user.is_admin && <Companies />}
        {tab === 'Settings' && <Settings status={s} isAdmin={user.is_admin} />}
        {tab === 'Admin' && user.is_admin && <Admin />}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 text-center text-xs text-slate-400">
        <div className="mb-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-sm">
          <button onClick={() => setDialog('support')} className="font-semibold text-brand-700 hover:underline">🛟 Contact & support</button>
          <button onClick={() => setDialog('privacy')} className="font-semibold text-slate-500 hover:text-slate-700 hover:underline">Privacy</button>
        </div>
        Straight from company career sites · H-1B data from the US Department of Labor and USCIS · E-Verify data from USCIS
      </footer>
      {dialog === 'support' && <SupportDialog loggedIn page={tab} onClose={() => setDialog(null)} />}
      {dialog === 'privacy' && <PrivacyDialog onClose={() => setDialog(null)} />}
    </div>
  )
}
