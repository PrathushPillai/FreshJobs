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

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 shadow-sm backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <h1 className="text-xl font-bold tracking-tight">
            Fresh<span className="text-emerald-600">Jobs</span>
          </h1>
          <nav className="flex gap-1">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1.5 text-sm ${tab === t ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
              >
                {t}
                {t === 'Tracker' && s?.tracked ? <span className="ml-1 rounded-full bg-emerald-600 px-1.5 text-[10px] text-white">{s.tracked}</span> : null}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
            {s && (
              <span>
                {s.companies} sources · {s.open_jobs.toLocaleString()} open · {s.new_last_24h} new in 24h
                {s.last_poll && ` · checked ${timeAgo(s.last_poll.finished_at)}`}
                {s.slow_polling && ' · syncing big-company boards…'}
              </span>
            )}
            <button
              onClick={() => poll.mutate()}
              disabled={s?.polling || poll.isPending}
              className="rounded-md border border-slate-300 px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
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
