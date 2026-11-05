import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, timeAgo } from './api'
import Companies from './components/Companies'
import Elsewhere from './components/Elsewhere'
import Feed from './components/Feed'
import Settings from './components/Settings'
import Tracker from './components/Tracker'

const TABS = ['Feed', 'LinkedIn & more', 'Tracker', 'Sources', 'Settings'] as const
type Tab = (typeof TABS)[number]

export default function App() {
  const [tab, setTab] = useState<Tab>('Feed')
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['status'], queryFn: api.status, refetchInterval: (q) => (q.state.data?.polling ? 3_000 : 30_000) })
  const poll = useMutation({
    mutationFn: api.poll,
    onSuccess: () => setTimeout(() => queryClient.invalidateQueries({ queryKey: ['status'] }), 500),
  })
  const s = status.data

  // When a check finishes (polling flips true -> false), reload everything so new jobs show up.
  const wasPolling = useRef(false)
  useEffect(() => {
    if (wasPolling.current && s && !s.polling) queryClient.invalidateQueries()
    wasPolling.current = !!s?.polling
  }, [s, queryClient])

  const live = !!s && (s.polling || s.slow_polling)
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 bg-slate-950 text-white shadow-lg shadow-slate-950/10">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 to-emerald-600 shadow-inner" aria-hidden>
              <svg viewBox="0 0 24 24" className="h-4.5 w-4.5 fill-white">
                <path d="M13.5 2 4 14h6.5L9 22l11-13h-6.6z" />
              </svg>
            </span>
            <h1 className="text-lg font-extrabold tracking-tight">
              Fresh<span className="text-emerald-400">Jobs</span>
            </h1>
          </div>
          <nav className="flex gap-0.5 overflow-x-auto">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${tab === t ? 'bg-white/10 text-white' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'}`}
              >
                {t}
                {t === 'Tracker' && s?.tracked ? <span className="ml-1.5 rounded-full bg-emerald-500 px-1.5 text-[10px] font-semibold text-white">{s.tracked}</span> : null}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-400">
            {s && (
              <span className="hidden items-center gap-3 md:flex">
                <span title={`${s.companies.toLocaleString()} company boards and job feeds`}>
                  <b className="font-semibold text-slate-200">{s.companies.toLocaleString()}</b> sources
                </span>
                <span>
                  <b className="font-semibold text-slate-200">{s.open_jobs.toLocaleString()}</b> open jobs
                </span>
                <span>
                  <b className="font-semibold text-emerald-400">{s.new_last_24h.toLocaleString()}</b> new today
                </span>
              </span>
            )}
            <span className="flex items-center gap-1.5" title={s?.last_poll ? `Last full check finished ${timeAgo(s.last_poll.finished_at)}` : undefined}>
              <span className="relative flex h-2 w-2">
                {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${status.isError ? 'bg-red-500' : 'bg-emerald-400'}`} />
              </span>
              {status.isError ? 'Offline' : live ? 'Scanning' : 'Live'}
            </span>
            <button
              onClick={() => poll.mutate()}
              disabled={s?.polling || poll.isPending}
              className="rounded-lg bg-white/10 px-3 py-1.5 font-semibold text-white hover:bg-white/15 disabled:opacity-50"
            >
              {s?.polling ? 'Checking…' : 'Check now'}
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-5">
        {status.isError && (
          <p className="mb-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            Can't reach the backend. Is it running on port 8000?
          </p>
        )}
        {tab === 'Feed' && <Feed />}
        {tab === 'LinkedIn & more' && <Elsewhere />}
        {tab === 'Tracker' && <Tracker />}
        {tab === 'Sources' && <Companies />}
        {tab === 'Settings' && <Settings status={s} />}
      </main>
    </div>
  )
}
