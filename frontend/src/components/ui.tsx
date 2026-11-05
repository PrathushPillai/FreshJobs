import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { APP_STATUSES, type AppStatus, type Job, STATUS_STYLES, api } from '../api'

export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-1.5 text-sm text-slate-700">
      <input type="checkbox" className="accent-emerald-600" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

const TONES = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200',
  red: 'bg-red-50 text-red-700 ring-red-200',
  slate: 'bg-slate-100 text-slate-600 ring-slate-200',
  blue: 'bg-sky-50 text-sky-700 ring-sky-200',
  violet: 'bg-violet-50 text-violet-700 ring-violet-200',
}

export function Badge({ children, tone, title }: { children: ReactNode; tone: keyof typeof TONES; title?: string }) {
  return (
    <span title={title} className={`whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export function StatusPill({ status }: { status: AppStatus }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STATUS_STYLES[status]}`}>{status}</span>
}

export const EXP_LABELS = { entry: 'Entry (0–2 yrs)', mid: 'Mid (3–5 yrs)', senior: 'Senior (6+ yrs)', unknown: 'Not stated' } as const

export function ExperienceBadge({ job }: { job: Pick<Job, 'exp_level' | 'min_years'> }) {
  if (job.exp_level === 'unknown') return null
  const tone = job.exp_level === 'entry' ? 'blue' : job.exp_level === 'mid' ? 'violet' : 'slate'
  const label = { entry: 'Entry', mid: 'Mid', senior: 'Senior' }[job.exp_level]
  return <Badge tone={tone}>{job.min_years !== null ? `${label} · ${job.min_years}+ yrs` : label}</Badge>
}

export function SponsorBadge({ filings }: { filings: number | null }) {
  if (!filings) return null
  const label = filings >= 1000 ? `${(filings / 1000).toFixed(filings >= 10_000 ? 0 : 1)}k` : String(filings)
  return (
    <Badge tone="green" title={`${filings.toLocaleString()} certified H-1B filings (US Department of Labor data)`}>
      H-1B · {label}
    </Badge>
  )
}

/** "Track" button for a job; once tracked, a status dropdown. */
export function TrackControl({ job, compact = false }: { job: Pick<Job, 'id' | 'application'>; compact?: boolean }) {
  const queryClient = useQueryClient()
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['jobs'] })
    queryClient.invalidateQueries({ queryKey: ['job', job.id] })
    queryClient.invalidateQueries({ queryKey: ['applications'] })
    queryClient.invalidateQueries({ queryKey: ['status'] })
  }
  const track = useMutation({ mutationFn: (status: AppStatus) => api.track({ job_id: job.id, status }), onSuccess: refresh })
  const update = useMutation({
    mutationFn: (status: AppStatus) => api.updateApplication(job.application!.id, { status }),
    onSuccess: refresh,
  })

  if (job.application) {
    return (
      <select
        value={job.application.status}
        onChange={(e) => update.mutate(e.target.value as AppStatus)}
        onClick={(e) => e.stopPropagation()}
        className={`rounded-md border-0 py-1 pl-2 pr-6 text-xs font-medium capitalize ${STATUS_STYLES[job.application.status]}`}
        title="Application status"
      >
        {APP_STATUSES.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    )
  }
  return (
    <div className="flex gap-1">
      <button
        onClick={() => track.mutate('applied')}
        disabled={track.isPending}
        className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        title="Add to your tracker as applied"
      >
        {compact ? '+ I applied' : 'Mark applied'}
      </button>
      {!compact && (
        <button
          onClick={() => track.mutate('saved')}
          disabled={track.isPending}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        >
          Save for later
        </button>
      )}
    </div>
  )
}

/** A filter "pill" that opens a small panel, like LinkedIn's and Jobright's filter bars.
 * With onClear, an active pill shows a ✕ (and the panel a "Clear" link) that resets just this filter. */
export function FilterPill({ label, active, onClear, children }: { label: ReactNode; active?: boolean; onClear?: () => void; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  const clearable = active && onClear
  return (
    <div ref={ref} className="relative">
      <div
        className={`flex items-center overflow-hidden whitespace-nowrap rounded-full border text-sm font-medium transition ${
          active ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-slate-300 bg-white text-slate-700'
        }`}
      >
        <button onClick={() => setOpen(!open)} className={`flex items-center gap-1 py-1.5 pl-3 ${clearable ? 'pr-1.5' : 'pr-3'} ${active ? 'hover:bg-emerald-700' : 'hover:bg-slate-100'}`}>
          {label}
          {!clearable && <span className="text-[10px] opacity-70">▾</span>}
        </button>
        {clearable && (
          <button
            onClick={() => {
              onClear()
              setOpen(false)
            }}
            className="py-1.5 pl-1 pr-2.5 hover:bg-emerald-700"
            aria-label="Clear this filter"
            title="Clear this filter"
          >
            ✕
          </button>
        )}
      </div>
      {open && (
        <div className="absolute left-0 z-30 mt-2 w-max min-w-56 max-w-[calc(100vw-2rem)] rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
          {children}
          {clearable && (
            <div className="mt-2 border-t border-slate-100 pt-2 text-right">
              <button
                onClick={() => {
                  onClear()
                  setOpen(false)
                }}
                className="text-sm font-medium text-emerald-700 hover:underline"
              >
                Clear
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** One choice in a FilterPill panel. */
export function Choice({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${selected ? 'bg-emerald-50 font-medium text-emerald-800' : 'text-slate-700 hover:bg-slate-100'}`}
    >
      <span className={`h-3.5 w-3.5 shrink-0 rounded-full border ${selected ? 'border-4 border-emerald-600' : 'border-slate-300'}`} />
      {children}
    </button>
  )
}

const AVATAR_COLORS = ['bg-sky-600', 'bg-violet-600', 'bg-emerald-600', 'bg-amber-600', 'bg-rose-600', 'bg-indigo-600', 'bg-teal-600', 'bg-fuchsia-600']

/** Company initials on a colour picked from the name, standing in for a logo. */
export function CompanyAvatar({ name, size = 'md' }: { name: string; size?: 'md' | 'lg' }) {
  const words = name.replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean)
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)).toUpperCase()
  let hash = 0
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) | 0
  const color = AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length]
  const box = size === 'lg' ? 'h-14 w-14 text-lg rounded-xl' : 'h-11 w-11 text-sm rounded-lg'
  return <div className={`flex shrink-0 items-center justify-center font-semibold text-white ${color} ${box}`}>{initials}</div>
}
