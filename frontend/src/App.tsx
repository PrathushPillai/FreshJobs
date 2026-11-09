import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, BackendDown, timeAgo } from './api'
import Companies from './components/Companies'
import Elsewhere from './components/Elsewhere'
import Feed from './components/Feed'
import Profile from './components/Profile'
import Settings from './components/Settings'
import Tracker from './components/Tracker'

const TABS = ['Feed', 'My profile', 'LinkedIn & more', 'Tracker', 'Sources', 'Settings'] as const
type Tab = (typeof TABS)[number]

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

export default function App() {
  const [tab, setTab] = useState<Tab>('Feed')
  const goTo = (t: Tab) => {
    setTab(t)
    window.scrollTo(0, 0)
  }
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    retry: false,
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

  const live = !!s && (s.polling || s.slow_polling)
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-white/10 bg-ink/90 text-white shadow-xl shadow-brand-950/20 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Logo />
          <nav className="no-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-full bg-white/5 p-1 ring-1 ring-white/10">
            {TABS.map((t) => (
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
            <button
              onClick={() => poll.mutate()}
              disabled={s?.polling || poll.isPending}
              className="rounded-full bg-gradient-to-r from-brand-500 to-fuchsia-500 px-3.5 py-1.5 font-semibold text-white shadow-lg shadow-brand-600/30 transition hover:brightness-110 disabled:from-white/10 disabled:to-white/10 disabled:text-slate-400 disabled:shadow-none"
            >
              {s?.polling ? 'Checking…' : '⚡ Check now'}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
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
        {tab === 'My profile' && <Profile onSeeMatches={() => goTo('Feed')} />}
        {tab === 'LinkedIn & more' && <Elsewhere />}
        {tab === 'Tracker' && <Tracker />}
        {tab === 'Sources' && <Companies />}
        {tab === 'Settings' && <Settings status={s} />}
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-8 pt-2 text-center text-xs text-slate-400">
        Straight from company career sites · H-1B data from the US Department of Labor and USCIS · E-Verify data from USCIS
      </footer>
    </div>
  )
}
