import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type ReactNode } from 'react'
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
