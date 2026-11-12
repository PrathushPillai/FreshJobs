import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { APP_STATUSES, type AppStatus, type Job, type MatchBrief, matchTone, REPEAT_LABELS, STATUS_STYLES, api } from '../api'

/** A checkbox row drawn as a small switch. */
export function Toggle({ label, checked, onChange }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-2.5 rounded-lg px-1 py-1 text-sm text-slate-700 hover:bg-slate-50">
      <span className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition ${checked ? 'bg-brand-600' : 'bg-slate-300'}`}>
        <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${checked ? 'translate-x-4' : 'translate-x-0.5'}`} />
      </span>
      <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}

const TONES = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200/80',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200/80',
  red: 'bg-rose-50 text-rose-700 ring-rose-200/80',
  slate: 'bg-slate-100 text-slate-600 ring-slate-200/80',
  blue: 'bg-sky-50 text-sky-700 ring-sky-200/80',
  violet: 'bg-brand-50 text-brand-700 ring-brand-200/80',
  teal: 'bg-teal-50 text-teal-700 ring-teal-200/80',
}
const DOTS = {
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  red: 'bg-rose-500',
  slate: 'bg-slate-400',
  teal: 'bg-teal-500',
  blue: 'bg-sky-500',
  violet: 'bg-brand-500',
}

export function Badge({ children, tone, title, dot = false }: { children: ReactNode; tone: keyof typeof TONES; title?: string; dot?: boolean }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${TONES[tone]}`}>
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${DOTS[tone]}`} aria-hidden />}
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

export function RepeatBadge({ job }: { job: Pick<Job, 'repost_kind' | 'posted_at'> }) {
  if (!job.repost_kind) return null
  const { label, hint } = REPEAT_LABELS[job.repost_kind]
  const since = job.repost_kind === 'resurfaced' && job.posted_at
    ? ` · from ${new Date(job.posted_at).toLocaleDateString(undefined, {
        month: 'short', day: 'numeric',
        year: new Date(job.posted_at).getFullYear() === new Date().getFullYear() ? undefined : 'numeric',
      })}`
    : ''
  return <Badge tone="amber" title={hint}>{label}{since}</Badge>
}

/** What the posting itself says about F-1 students (OPT/CPT/STEM OPT) and E-Verify. */
export function VisaSignalBadges({ job }: { job: Pick<Job, 'student_visa' | 'everify_text' | 'everify' | 'sponsors_text'> }) {
  return (
    <>
      {job.sponsors_text && (
        <Badge tone="green" dot title='The posting says it sponsors visas (for example "We do sponsor visas!")'>Says it sponsors</Badge>
      )}
      {job.student_visa === 'welcome' && (
        <Badge tone="green" dot title="The posting says candidates on F-1 OPT, STEM OPT or CPT are welcome">OPT/CPT welcome</Badge>
      )}
      {job.student_visa === 'excluded' && (
        <Badge tone="red" dot title="The posting rules out candidates on F-1 OPT, STEM OPT or CPT">No OPT/CPT</Badge>
      )}
      {(job.everify === 'yes' || job.everify_text) ? (
        <Badge tone="blue" dot title="The employer uses E-Verify (USCIS's employer list, or the posting says so), which STEM OPT employers must">
          E-Verify
        </Badge>
      ) : job.everify === 'ended' ? (
        <Badge tone="amber" dot title="Every E-Verify account of this employer has ended (USCIS's employer list)">E-Verify ended</Badge>
      ) : null}
    </>
  )
}

/** The sponsorship verdict: "Likely sponsor" or "May sponsor" (nothing for unlikely; ruled out has its own badge). */
export function SponsorLikelyBadge({ job }: { job: Pick<Job, 'sponsor_likely' | 'sponsors_text' | 'h1b_filings' | 'everify'> }) {
  if (job.sponsor_likely === 'likely') {
    const why = job.sponsors_text ? 'the posting says it sponsors visas' : `the employer has ${(job.h1b_filings ?? 0).toLocaleString()} certified H-1B filings in the last two years`
    return <Badge tone="green" dot title={`Likely to sponsor: ${why}.`}>Likely sponsor</Badge>
  }
  if (job.sponsor_likely === 'possible') {
    const why = job.h1b_filings ? `the employer has filed ${job.h1b_filings} H-1B${job.h1b_filings === 1 ? '' : 's'} recently` : 'no H-1B filings found, but the employer uses E-Verify (STEM OPT is possible)'
    return <Badge tone="teal" dot title={`May sponsor: ${why}.`}>May sponsor</Badge>
  }
  return null
}

/** "Consultancy" or "Staffing firm": the employer mostly places people at client companies (from its own H-1B
 * filings, a staffing agency's job system, or its name). Shown, never hidden unasked; More can hide them. */
export function EmployerKindBadge({ kind }: { kind: Job['employer_kind'] }) {
  if (kind === 'consultancy') {
    return <Badge tone="amber" dot title="A consultancy: most of its H-1B workers are placed at client companies (US Department of Labor data), or its name says so. More → Hide consultancies & staffing firms leaves these out.">Consultancy</Badge>
  }
  if (kind === 'staffing') {
    return <Badge tone="amber" dot title="A staffing firm: it recruits for client companies, so the job is with one of its clients. More → Hide consultancies & staffing firms leaves these out.">Staffing firm</Badge>
  }
  return null
}

export function SponsorBadge({ filings }: { filings: number | null }) {
  if (!filings) return null
  const label = filings >= 1000 ? `${(filings / 1000).toFixed(filings >= 10_000 ? 0 : 1)}k` : String(filings)
  return (
    <Badge tone="violet" dot title={`${filings.toLocaleString()} certified H-1B filings (US Department of Labor data)`}>
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
        className={`rounded-full border-0 py-1 pl-3 pr-7 text-xs font-semibold capitalize ${STATUS_STYLES[job.application.status]}`}
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
  const secondary = 'rounded-full px-3 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50 hover:ring-slate-300 disabled:opacity-50'
  return (
    <div className="flex gap-1.5">
      <button onClick={() => track.mutate('applied')} disabled={track.isPending} className={secondary} title="Add to your tracker as applied">
        {compact ? '+ Applied' : '✓ Mark applied'}
      </button>
      {!compact && (
        <button onClick={() => track.mutate('saved')} disabled={track.isPending} className={secondary}>
          ☆ Save for later
        </button>
      )}
    </div>
  )
}

/** A filter "pill" that opens a small panel, like LinkedIn's and Jobright's filter bars.
 * With onClear, an active pill shows a ✕ (and the panel a "Clear" link) that resets just this filter. */
/** A filter in the bar: a pill that opens a panel. With `onApply`, changes made in the panel are only staged
 * (the Feed keeps them as a draft) until Apply; closing the panel any other way (outside click, Esc, Cancel)
 * calls `onCancel` and drops them. The pill's own ✕ and the panel's Reset apply at once: they're explicit. */
export function FilterPill({ label, active, onClear, onApply, onCancel, dirty, children }: {
  label: ReactNode
  active?: boolean
  onClear?: () => void
  onApply?: () => void
  onCancel?: () => void
  dirty?: boolean
  children: ReactNode
}) {
  const [open, setOpenState] = useState(false)
  const onCancelRef = useRef(onCancel)
  onCancelRef.current = onCancel
  const setOpen = (next: boolean) => {
    if (!next) onCancelRef.current?.()  // closed without Apply: drop what was staged
    setOpenState(next)
  }
  // Pills on the right half open their panel leftwards, so it never runs off the screen.
  const [alignRight, setAlignRight] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const toggle = () => {
    const box = ref.current?.getBoundingClientRect()
    if (box) setAlignRight(box.left > window.innerWidth / 2)
    setOpen(!open)
  }
  useEffect(() => {
    if (!open) return
    const shut = () => {
      onCancelRef.current?.()
      setOpenState(false)
    }
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && shut()
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && shut()
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
        className={`flex items-center overflow-hidden whitespace-nowrap rounded-full text-sm font-medium shadow-sm transition ${
          active
            ? 'bg-gradient-to-r from-brand-600 to-violet-600 text-white shadow-brand-600/25'
            : open
              ? 'bg-white text-slate-900 ring-2 ring-brand-300'
              : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:ring-slate-300'
        }`}
      >
        <button onClick={toggle} className={`flex items-center gap-1.5 py-1.5 pl-3.5 ${clearable ? 'pr-1.5' : 'pr-3'} ${active ? 'hover:bg-white/10' : 'hover:bg-slate-50'}`}>
          {label}
          {!clearable && (
            <svg viewBox="0 0 20 20" className={`h-3.5 w-3.5 opacity-60 transition ${open ? 'rotate-180' : ''}`} fill="currentColor" aria-hidden>
              <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.06l3.71-3.83a.75.75 0 1 1 1.08 1.04l-4.25 4.39a.75.75 0 0 1-1.08 0L5.21 8.27a.75.75 0 0 1 .02-1.06Z" clipRule="evenodd" />
            </svg>
          )}
        </button>
        {clearable && (
          <button
            onClick={() => {
              onClear()
              setOpen(false)
            }}
            className="py-1.5 pl-1 pr-3 hover:bg-white/10"
            aria-label="Clear this filter"
            title="Clear this filter"
          >
            ✕
          </button>
        )}
      </div>
      {open && (
        <div
          className={`absolute z-30 mt-2 w-max min-w-60 max-w-[min(24rem,calc(100vw-2rem))] animate-rise rounded-2xl bg-white p-3 shadow-2xl shadow-slate-900/15 ring-1 ring-slate-200 ${
            alignRight ? 'right-0' : 'left-0'
          }`}
        >
          {children}
          {(onClear || onApply) && (
            <div className="mt-2 flex items-center gap-2 border-t border-slate-100 pt-2">
              {onClear && (
                // Always there, so every filter can be put back to its default from its own menu.
                <button
                  disabled={!active}
                  onClick={() => {
                    onClear()
                    setOpenState(false)
                  }}
                  className="rounded-full px-3 py-1 text-sm font-medium text-brand-700 hover:bg-brand-50 disabled:cursor-default disabled:text-slate-400 disabled:hover:bg-transparent"
                >
                  Reset
                </button>
              )}
              {onApply && (
                <>
                  <button onClick={() => setOpen(false)} className="ml-auto rounded-full px-3 py-1 text-sm font-medium text-slate-600 hover:bg-slate-100">
                    Cancel
                  </button>
                  <button
                    onClick={() => {
                      onApply()
                      setOpenState(false)
                    }}
                    disabled={!dirty}
                    className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-4 py-1 text-sm font-bold text-white shadow-sm shadow-brand-600/25 transition hover:brightness-110 disabled:from-slate-200 disabled:to-slate-200 disabled:text-slate-400 disabled:shadow-none"
                  >
                    Apply
                  </button>
                </>
              )}
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
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm transition ${selected ? 'bg-brand-50 font-semibold text-brand-800' : 'text-slate-700 hover:bg-slate-50'}`}
    >
      <span className={`h-4 w-4 shrink-0 rounded-full border-2 transition ${selected ? 'border-[5px] border-brand-600' : 'border-slate-300'}`} />
      {children}
    </button>
  )
}

// Two-tone gradients for company initials, picked from the name so a company keeps its colour.
const AVATAR_GRADIENTS = [
  'from-sky-500 to-indigo-600',
  'from-violet-500 to-fuchsia-600',
  'from-emerald-500 to-teal-600',
  'from-amber-400 to-orange-600',
  'from-rose-500 to-pink-600',
  'from-indigo-500 to-brand-700',
  'from-teal-400 to-cyan-600',
  'from-fuchsia-500 to-purple-700',
  'from-lime-500 to-emerald-600',
  'from-orange-400 to-rose-600',
]

/** The company's logo (its website's icon, via Google's favicon service) when it has a real one, otherwise
 * its initials on a colour picked from the name. Google answers unknown sites with a 16 px globe, so an icon
 * that small counts as "no logo". */
export function CompanyAvatar({ name, domain, size = 'md' }: { name: string; domain?: string | null; size?: 'md' | 'lg' }) {
  const [logo, setLogo] = useState<'loading' | 'ok' | 'none'>(domain ? 'loading' : 'none')
  const box = size === 'lg' ? 'h-16 w-16 rounded-2xl' : 'h-12 w-12 rounded-xl'
  return (
    <div className={`relative shrink-0 ${box}`}>
      {logo !== 'ok' && <Initials name={name} size={size} />}
      {domain && logo !== 'none' && (
        <img
          src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=128`}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onLoad={(e) => setLogo(e.currentTarget.naturalWidth > 16 ? 'ok' : 'none')}
          onError={() => setLogo('none')}
          className={`absolute inset-0 h-full w-full bg-white object-contain p-1.5 shadow-md shadow-slate-900/10 ring-1 ring-slate-200 ${box} ${
            logo === 'ok' ? 'opacity-100' : 'opacity-0'
          } transition-opacity duration-300`}
        />
      )}
    </div>
  )
}

function Initials({ name, size }: { name: string; size: 'md' | 'lg' }) {
  const words = name.replace(/[^A-Za-z0-9 ]/g, ' ').split(/\s+/).filter(Boolean)
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)).toUpperCase()
  let hash = 0
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) | 0
  const gradient = AVATAR_GRADIENTS[Math.abs(hash) % AVATAR_GRADIENTS.length]
  const box = size === 'lg' ? 'h-16 w-16 text-xl rounded-2xl' : 'h-12 w-12 text-sm rounded-xl'
  return (
    <div className={`flex shrink-0 items-center justify-center bg-gradient-to-br font-display font-bold text-white shadow-md shadow-slate-900/10 ring-1 ring-white/40 ${gradient} ${box}`}>
      {initials}
    </div>
  )
}

/** The violet band at the top of each tab other than the Feed. */
export function PageHeader({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <section className="hero relative overflow-hidden rounded-3xl px-6 py-6 text-white shadow-xl shadow-brand-950/20 sm:px-8">
      <div className="hero-grid pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative flex items-start gap-4">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white/10 text-2xl ring-1 ring-white/20" aria-hidden>
          {icon}
        </span>
        <div>
          <h2 className="font-display text-2xl font-extrabold tracking-tight sm:text-3xl">{title}</h2>
          {children && <p className="mt-1 max-w-3xl text-sm text-brand-100/80">{children}</p>}
        </div>
      </div>
    </section>
  )
}

const MATCH_STYLES = {
  strong: { ring: 'stroke-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50 ring-emerald-200', label: 'Strong match' },
  good: { ring: 'stroke-teal-500', text: 'text-teal-700', bg: 'bg-teal-50 ring-teal-200', label: 'Good match' },
  fair: { ring: 'stroke-amber-500', text: 'text-amber-700', bg: 'bg-amber-50 ring-amber-200', label: 'Fair match' },
  weak: { ring: 'stroke-slate-400', text: 'text-slate-500', bg: 'bg-slate-50 ring-slate-200', label: 'Weak match' },
}

/** A circular gauge with the match score in the middle. */
export function MatchRing({ score, size = 44 }: { score: number; size?: number }) {
  const style = MATCH_STYLES[matchTone(score)]
  const r = size / 2 - 4
  const c = 2 * Math.PI * r
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size }} title={`${style.label}: ${score}/100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} className="fill-none stroke-slate-100" strokeWidth={4} />
        <circle cx={size / 2} cy={size / 2} r={r} className={`fill-none ${style.ring} transition-all`} strokeWidth={4} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - score / 100)} />
      </svg>
      <span className={`absolute font-display font-extrabold ${style.text} ${size >= 60 ? 'text-xl' : 'text-xs'}`}>{score}</span>
    </span>
  )
}

/** A job card's match line: the score and a few skills you have (✓) and lack (✗). */
export function MatchLine({ match }: { match: MatchBrief }) {
  const style = MATCH_STYLES[matchTone(match.score)]
  return (
    <div className={`mt-2.5 flex flex-wrap items-center gap-1.5 rounded-xl px-2 py-1.5 ring-1 ring-inset ${style.bg}`}>
      <MatchRing score={match.score} size={32} />
      <span className={`text-xs font-bold ${style.text}`}>{style.label}</span>
      {match.matched.slice(0, 3).map((s) => (
        <span key={s} className="rounded-full bg-white/80 px-1.5 text-[11px] font-semibold text-emerald-700">✓ {s}</span>
      ))}
      {match.missing.slice(0, 2).map((s) => (
        <span key={s} className="rounded-full bg-white/80 px-1.5 text-[11px] font-semibold text-rose-600">✗ {s}</span>
      ))}
      {match.notes[0] && <span className="text-[11px] text-slate-500">· {match.notes[0]}</span>}
    </div>
  )
}
