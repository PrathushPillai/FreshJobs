import { useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import { api, formatDateTime, type H1BHistory, type JobDetail as JobDetailType, money, postedText, SOURCE_LABELS, timeAgo, WINDOW_HINT } from '../api'
import { Badge, CompanyAvatar, ExperienceBadge, RepeatBadge, SponsorBadge, TrackControl, VisaSignalBadges } from './ui'

/** Apply a Hunter.io pattern like "{first}.{last}" to a name. */
function guessEmail(pattern: string, domain: string, name: string): string | null {
  const parts = name.toLowerCase().match(/[a-z]+/g) ?? []
  const first = parts[0]
  if (!first) return null
  const last = parts.length > 1 ? parts[parts.length - 1] : ''
  const local = pattern
    .split('{first}').join(first)
    .split('{last}').join(last)
    .split('{f}').join(first.slice(0, 1))
    .split('{l}').join(last.slice(0, 1))
  return local.includes('{') ? null : `${local}@${domain}`
}

function Recruiters({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient()
  const [lookup, setLookup] = useState(false)
  const [name, setName] = useState('')
  const info = useQuery({ queryKey: ['recruiters', jobId, lookup], queryFn: () => api.recruiters(jobId, lookup) })
  const d = info.data
  const h = d?.hunter
  const guessed = h?.pattern && h.domain && name ? guessEmail(h.pattern, h.domain, name) : null

  return (
    <section className="space-y-3 rounded-2xl p-5 ring-1 ring-slate-200">
      <h3 className="font-display font-bold text-slate-900">🤝 Find the recruiter</h3>
      <p className="text-xs text-slate-500">These links open LinkedIn or Google searches in a new tab. Sign in to LinkedIn to see full profiles.</p>
      <ul className="space-y-1 text-sm">
        {d?.links.map((l) => (
          <li key={l.url}>
            <a href={l.url} target="_blank" rel="noreferrer" className="text-sky-700 hover:underline">
              {l.label} ↗
            </a>
          </li>
        ))}
      </ul>

      <div className="border-t border-slate-100 pt-3">
        {!d?.hunter_configured && !h && (
          <p className="text-xs text-slate-500">
            For email lookup, get a free key at hunter.io (25 searches a month), add <code>HUNTER_API_KEY=…</code> to <code>.env</code>, and restart the backend.
          </p>
        )}
        {d?.hunter_configured && !lookup && (
          <button onClick={() => setLookup(true)} className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white">
            Look up {d.company} recruiter emails (Hunter.io)
          </button>
        )}
        {lookup && info.isFetching && <p className="text-sm text-slate-500">Looking up…</p>}
        {h?.error && <p className="text-sm text-red-600">{h.error}</p>}
        {h && !h.error && (
          <div className="space-y-2 text-sm">
            <p className="text-slate-600">
              {h.domain ? (
                <>
                  Domain <b>{h.domain}</b>
                  {h.pattern ? (
                    <>
                      {' '}· email format <code>{h.pattern}@{h.domain}</code>
                    </>
                  ) : (
                    ' · email format unknown'
                  )}
                </>
              ) : (
                'Hunter.io found no domain for this company.'
              )}
              {h.cached && <span className="text-xs text-slate-400"> (cached)</span>}
            </p>
            {h.contacts && h.contacts.length > 0 ? (
              <ul className="divide-y divide-slate-100 rounded-md border border-slate-200">
                {h.contacts.map((c) => (
                  <li key={c.email} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                    <div>
                      <div className="font-medium">{c.name ?? c.email}</div>
                      <div className="text-xs text-slate-500">{c.position ?? c.department ?? '—'}</div>
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <a href={`mailto:${c.email}`} className="text-sky-700 hover:underline">
                        {c.email}
                      </a>
                      {c.confidence !== null && <span className="text-slate-400">{c.confidence}%</span>}
                      {c.linkedin && (
                        <a href={c.linkedin} target="_blank" rel="noreferrer" className="text-sky-700 hover:underline">
                          LinkedIn ↗
                        </a>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-slate-500">No published contacts. Find the recruiter on LinkedIn above, then use the email format below.</p>
            )}
            {h.pattern && h.domain && (
              <label className="block text-xs text-slate-600">
                Found a recruiter on LinkedIn? Type their name to get their likely email:
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1 text-sm" />
                {guessed && (
                  <span className="mt-1 block">
                    Likely email: <b className="select-all">{guessed}</b> <span className="text-slate-400">(a guess, so verify it before sending)</span>
                  </span>
                )}
              </label>
            )}
            <button
              onClick={() => api.recruiters(jobId, true, true).then((r) => queryClient.setQueryData(['recruiters', jobId, true], r))}
              className="text-xs text-slate-500 hover:underline"
            >
              Refresh lookup
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

/** The job's full details: header, apply/track actions, facts, recruiter finder and description. */
export function JobDetailContent({ id, inDrawer = false }: { id: number; inDrawer?: boolean }) {
  const job = useQuery({ queryKey: ['job', id], queryFn: () => api.job(id) })
  const j = job.data
  // Before you spend time on it: open its apply page and see if it's still up (once per 6 h per job).
  const recentlyChecked = !!j?.checked_at && Date.now() - new Date(j.checked_at).getTime() < 6 * 3600_000
  const check = useQuery({
    queryKey: ['verify', id],
    queryFn: () => api.verify(id),
    enabled: !!j && !j.closed_at && !recentlyChecked,
    staleTime: Infinity,
  })
  const closedNow = check.data?.open === false
  // Saved without a description (older Workday jobs, list stubs, blank Lever postings): fetch it now.
  const queryClient = useQueryClient()
  const needsDescription = !!j && (j.description ?? '').trim().length < 100
  const describe = useQuery({
    queryKey: ['describe', id],
    queryFn: async () => {
      const got = await api.describe(id)
      if (got.description) queryClient.setQueryData(['job', id], (old: JobDetailType | undefined) => (old ? { ...old, description: got.description! } : old))
      return got
    },
    enabled: needsDescription,
    staleTime: Infinity,
  })
  // When it went live: the company's own time, or when we caught it if the company only gives a date.
  const listed = j?.listed_at ?? j?.posted_at ?? null
  if (job.isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex gap-4">
          <div className="skeleton h-16 w-16 rounded-2xl" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-6 w-3/4" />
            <div className="skeleton h-4 w-1/2" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-24 rounded-2xl" />)}
        </div>
      </div>
    )
  }
  if (!j) return null
  return (
    <div className="animate-rise space-y-6">
      <div className="-mx-6 -mt-6 rounded-t-3xl border-b border-slate-100 bg-gradient-to-br from-brand-50 via-white to-emerald-50/60 px-6 pb-5 pt-6">
        {/* In the phone drawer, leave room on the right for its Close button. */}
        <div className={`flex items-start gap-4 ${inDrawer ? 'pr-20' : ''}`}>
          <CompanyAvatar key={j.logo_domain ?? j.company} name={j.company} domain={j.logo_domain} size="lg" />
          <div className="min-w-0">
            <h2 className="font-display text-2xl font-extrabold leading-tight tracking-tight text-slate-900">{j.title}</h2>
            <p className="mt-1 text-sm text-slate-600">
              <span className="font-semibold text-slate-800">{j.company}</span> · {j.location || 'Location not listed'}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {listed ? <span title={j.listed_until ? WINDOW_HINT : undefined}>Posted {postedText(listed, j.listed_until)}</span> : 'Posting date not given'}
              {!j.baseline && ` · found by us ${timeAgo(j.first_seen_at)}`}
              {j.department && ` · ${j.department}`}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <ExperienceBadge job={j} />
          <SponsorBadge filings={j.h1b_filings} />
          <VisaSignalBadges job={j} />
          {j.remote && <Badge tone="slate">Remote</Badge>}
          {j.no_sponsor && <Badge tone="red" dot>No sponsorship</Badge>}
          <RepeatBadge job={j} />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <a
            href={j.url}
            target="_blank"
            rel="noreferrer"
            className="rounded-full bg-gradient-to-r from-brand-600 via-violet-600 to-fuchsia-600 px-6 py-2.5 text-sm font-bold text-white shadow-lg shadow-brand-600/30 transition hover:-translate-y-px hover:brightness-110"
          >
            Apply on company site ↗
          </a>
          <TrackControl job={j} />
        </div>
        {(j.closed_at || closedNow) ? (
          <p className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
            This job has closed{check.data?.detail ? ` (${check.data.detail})` : ''}. It's hidden from the feed now.
          </p>
        ) : (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-500">
            {check.isFetching ? (
              <><span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-200 border-t-brand-600" /> Checking the apply page…</>
            ) : check.data?.open || recentlyChecked ? (
              <><span className="h-2 w-2 rounded-full bg-emerald-500" /> Apply page is up (checked {timeAgo(check.data?.checked_at ?? j.checked_at!)})</>
            ) : (
              'Couldn’t confirm the apply page is up; it may still be open.'
            )}
          </p>
        )}
      </div>
      {j.no_sponsor && (
        <p className="rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-rose-200">
          The description suggests this role doesn't sponsor visas, or needs US citizenship or a clearance. Some employers can still be persuaded, so it's shown anyway.
        </p>
      )}
      <RepeatNote job={j} />
      <VisaSnapshot job={j} />
      <h3 className="font-display text-sm font-bold uppercase tracking-wider text-slate-500">Details</h3>
      <dl className="-mt-3 grid grid-cols-[auto_1fr] gap-x-5 gap-y-2.5 rounded-2xl bg-slate-50/80 p-5 text-sm ring-1 ring-slate-100">
        <dt className="text-slate-500">Posted</dt>
        <dd>
          {listed ? `${formatDateTime(listed)} (${timeAgo(listed)})` : 'Not given by the source'}
          {j.posted_at && listed && j.posted_at !== listed && (
            <span className="block text-xs text-slate-500">The company only gives the date ({new Date(j.posted_at).toLocaleDateString()}); this is when we caught it.</span>
          )}
        </dd>
        <dt className="text-slate-500">Found by us</dt>
        <dd>{j.baseline ? 'Already listed when we started tracking this source' : `${formatDateTime(j.first_seen_at)} (${timeAgo(j.first_seen_at)})`}</dd>
        <dt className="text-slate-500">Experience</dt>
        <dd className="flex items-center gap-2">
          <ExperienceBadge job={j} />
          {j.min_years === null && j.exp_level === 'unknown' && 'Not stated'}
        </dd>
        <dt className="text-slate-500">H-1B sponsor</dt>
        <dd>
          {j.sponsor ? (
            <span className="space-y-0.5">
              <span className="flex items-center gap-2">
                <SponsorBadge filings={j.sponsor.filings} />
                {j.sponsor.filings.toLocaleString()} certified H-1B filings
              </span>
              <span className="block text-xs text-slate-500">
                As {j.sponsor.employers.join(', ')}
                {j.sponsor.top_titles.length > 0 && <> · often for: {j.sponsor.top_titles.slice(0, 5).join(', ')}</>}
              </span>
            </span>
          ) : (
            <span className="text-slate-500">No H-1B filings found (or sponsor data not imported yet)</span>
          )}
        </dd>
        <dt className="text-slate-500">H-1B approvals</dt>
        <dd><USCISApprovals job={j} /></dd>
        <dt className="text-slate-500">E-Verify</dt>
        <dd><EVerifyInfo job={j} /></dd>
        {j.h1b_roles && (
          <>
            <dt className="text-slate-500">H-1B for this role</dt>
            <dd><RoleHistory history={j.h1b_roles} /></dd>
          </>
        )}
        <dt className="text-slate-500">Source</dt>
        <dd>{SOURCE_LABELS[j.source] ?? j.source}</dd>
        <dt className="text-slate-500">History</dt>
        <dd>{j.events.map((e) => `${e.kind} ${timeAgo(e.at)}`).join(' → ')}</dd>
      </dl>
      <Recruiters jobId={j.id} />
      <div>
        <h3 className="mb-3 font-display text-sm font-bold uppercase tracking-wider text-slate-500">About the job</h3>
        {needsDescription && describe.isFetching ? (
          <div className="space-y-2">
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-200 border-t-brand-600" /> Loading the full description from the company's site…
            </p>
            {[90, 75, 85, 60].map((w) => <div key={w} className="skeleton h-3" style={{ width: `${w}%` }} />)}
          </div>
        ) : needsDescription && !(j.description ?? '').trim() ? (
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600 ring-1 ring-slate-100">
            The company's site didn't give a description here.{' '}
            <a href={j.url} target="_blank" rel="noreferrer" className="font-semibold text-brand-700 hover:underline">
              Open the posting ↗
            </a>{' '}
            for the details.
          </p>
        ) : (
          <div className="whitespace-pre-line text-[15px] leading-relaxed text-slate-700">{j.description}</div>
        )}
      </div>
    </div>
  )
}

/** JobDetailContent in a slide-over drawer (used on narrow screens and outside the feed). */
export default function JobDetail({ id, onClose }: { id: number; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ink/40 backdrop-blur-sm" onClick={onClose}>
      <aside className="relative h-full w-full max-w-2xl animate-rise overflow-y-auto bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={onClose}
          className="absolute right-4 top-4 z-10 rounded-full bg-white/90 px-3 py-1 text-sm font-semibold text-slate-600 shadow ring-1 ring-slate-200 hover:text-slate-900"
        >
          Close ✕
        </button>
        <JobDetailContent id={id} inDrawer />
      </aside>
    </div>
  )
}

function RepeatNote({ job }: { job: JobDetailType }) {
  if (!job.repost_kind) return null
  const o = job.repost_of
  let text: ReactNode
  if (job.repost_kind === 'resurfaced') {
    text = job.posted_at
      ? <>Re-listed as new, but the company first posted it on {new Date(job.posted_at).toLocaleDateString()} ({timeAgo(job.posted_at)}). Others have likely applied already.</>
      : <>Re-listed as new, but the company dates it more than a week ago.</>
  } else if (o) {
    const original = <a href={o.url} target="_blank" rel="noreferrer" className="font-medium underline">{o.title} at {o.company}</a>
    text = job.repost_kind === 'duplicate'
      ? <>The same job is also listed elsewhere: {original}, which we found {timeAgo(o.first_seen_at)} and is still open.</>
      : <>A repost of {original}, which we first found {timeAgo(o.first_seen_at)}{o.closed_at ? ` and saw close ${timeAgo(o.closed_at)}` : ''}.</>
  } else {
    text = <>A repost of a job this company listed before.</>
  }
  return <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">{text}</p>
}

/** The employer's H-1B filings for the occupation that fits this job, then its other occupations. */
function RoleHistory({ history }: { history: H1BHistory }) {
  const m = history.match
  const others = history.roles.filter((r) => r.soc_code !== m?.soc_code).slice(0, 4)
  return (
    <div className="space-y-1.5">
      {m ? (
        <div>
          <p>
            <span className="font-semibold">{m.filings.toLocaleString()}</span> {m.filings === 1 ? 'filing' : 'filings'} as <span className="font-medium">{m.soc_title}</span>
            {m.median_wage !== null && (
              <>
                {' '}· median <span className="font-semibold">{money(m.median_wage)}</span>
                {m.low_wage !== null && m.high_wage !== null && m.low_wage !== m.high_wage && (
                  <span className="text-slate-500"> (middle half {money(m.low_wage)} to {money(m.high_wage)})</span>
                )}
              </>
            )}
          </p>
          <p className="text-xs text-slate-500">
            {m.top_cities.length > 0 && <>Mostly in {m.top_cities.slice(0, 3).join(' · ')}. </>}
            {m.matched_by === 'title'
              ? <>Matched by similar titles they filed: {m.top_titles.slice(0, 3).join(', ')}.</>
              : <>The occupation this title usually files under; their own titles: {m.top_titles.slice(0, 3).join(', ')}.</>}
          </p>
        </div>
      ) : (
        <p className="text-amber-800">No H-1B filings for this kind of role, though they filed {history.total.toLocaleString()} for others.</p>
      )}
      {others.length > 0 && (
        <p className="text-xs text-slate-500">
          Also: {others.map((r) => `${r.soc_title} ${r.filings.toLocaleString()}${r.median_wage !== null ? ` (${money(r.median_wage)})` : ''}`).join(' · ')}
        </p>
      )}
    </div>
  )
}

/** Whether the employer can hire on STEM OPT: USCIS's E-Verify list, then what the posting says. */
function EVerifyInfo({ job }: { job: JobDetailType }) {
  const e = job.everify_info
  const year = (d: string | null) => (d ? new Date(d).getFullYear() : null)
  const day = (d: string | null) => (d ? new Date(d).toLocaleDateString() : '')
  if (e?.status === 'yes') {
    return (
      <span>
        <span className="font-medium text-emerald-700">Enrolled</span>
        {e.since && <> since {year(e.since)}</>}, so it can hire on STEM OPT
        <span className="block text-xs text-slate-500">As {e.name}{e.workforce && <> · {e.workforce} employees</>} (USCIS E-Verify list)</span>
      </span>
    )
  }
  if (e?.status === 'ended') {
    return (
      <span>
        <span className="font-medium text-amber-800">Ended</span> on {day(e.ended)}
        {job.everify_text ? ', though the posting says they use it' : ''}
        <span className="block text-xs text-slate-500">As {e.name}; ask before relying on STEM OPT (USCIS E-Verify list)</span>
      </span>
    )
  }
  return (
    <span className="text-slate-600">
      {job.everify_text ? 'The posting says they use it' : 'Not found in USCIS\'s list'}
      <span className="block text-xs text-slate-500">Employers enroll under their legal name, so they may still be enrolled under another one.</span>
    </span>
  )
}

/** Petitions USCIS actually decided for the employer, newest fiscal year first. */
function USCISApprovals({ job }: { job: JobDetailType }) {
  const u = job.uscis
  if (!u || u.years.length === 0) {
    return <span className="text-slate-500">None found in USCIS's data for FY2024 to FY2026</span>
  }
  const rate = (approved: number, denied: number) =>
    approved + denied > 0 ? `${Math.round((100 * denied) / (approved + denied))}% denied` : ''
  return (
    <div className="space-y-0.5">
      {u.years.map((y) => (
        <p key={y.fiscal_year}>
          <span className="text-slate-500">FY{y.fiscal_year}:</span>{' '}
          <span className="font-semibold">{y.new_approvals.toLocaleString()}</span> new H-1B workers approved
          {y.new_denials > 0 && <span className="text-slate-500"> ({rate(y.new_approvals, y.new_denials)})</span>}
          <span className="text-slate-500"> · {y.approvals.toLocaleString()} approvals of every kind</span>
        </p>
      ))}
      <p className="text-xs text-slate-500">
        "New" means workers new to H-1B, as a new grad would be. USCIS data, as {u.names.slice(0, 2).join(', ')}.
      </p>
    </div>
  )
}

/** Four tiles that answer "can I get hired here on a visa?" at a glance. */
function VisaSnapshot({ job }: { job: JobDetailType }) {
  const latest = job.uscis?.years[0]
  const ev = job.everify_info
  const everifyOn = ev?.status === 'yes' || (!ev && job.everify_text)
  const role = job.h1b_roles?.match
  const tiles: { label: string; value: ReactNode; sub: ReactNode; tone: string }[] = [
    {
      label: 'H-1B approvals',
      value: latest ? latest.new_approvals.toLocaleString() : job.h1b_filings ? job.h1b_filings.toLocaleString() : '—',
      sub: latest
        ? `new workers approved in FY${latest.fiscal_year}${latest.new_denials ? ` · ${Math.round((100 * latest.new_denials) / (latest.new_approvals + latest.new_denials))}% denied` : ''}`
        : job.h1b_filings ? 'H-1B filings (DOL)' : 'no H-1B record found',
      tone: latest || job.h1b_filings ? 'from-brand-500/15 to-fuchsia-500/10 text-brand-800 ring-brand-200' : 'from-slate-100 to-slate-50 text-slate-500 ring-slate-200',
    },
    {
      label: 'E-Verify (STEM OPT)',
      value: everifyOn ? 'Enrolled' : ev?.status === 'ended' ? 'Ended' : 'Not found',
      sub: everifyOn
        ? ev?.since ? `since ${new Date(ev.since).getFullYear()}` : 'the posting says so'
        : ev?.status === 'ended' ? `ended ${ev.ended ? new Date(ev.ended).getFullYear() : ''}` : 'may use another legal name',
      tone: everifyOn
        ? 'from-sky-500/15 to-emerald-500/10 text-sky-800 ring-sky-200'
        : ev?.status === 'ended' ? 'from-amber-400/20 to-amber-50 text-amber-800 ring-amber-200' : 'from-slate-100 to-slate-50 text-slate-500 ring-slate-200',
    },
    {
      label: 'Salary for this role',
      value: role?.median_wage ? money(role.median_wage) : '—',
      sub: role?.median_wage ? `H-1B median · ${role.soc_title}` : 'no H-1B wages for this role',
      tone: role?.median_wage ? 'from-emerald-500/15 to-teal-500/10 text-emerald-800 ring-emerald-200' : 'from-slate-100 to-slate-50 text-slate-500 ring-slate-200',
    },
    {
      label: 'OPT / CPT',
      value: job.student_visa === 'welcome' ? 'Welcome' : job.student_visa === 'excluded' ? 'Ruled out' : 'Not mentioned',
      sub: job.student_visa === 'welcome' ? 'the posting invites F-1 students' : job.student_visa === 'excluded' ? 'the posting excludes F-1 students' : 'the posting doesn\'t say',
      tone: job.student_visa === 'welcome'
        ? 'from-emerald-500/15 to-lime-400/10 text-emerald-800 ring-emerald-200'
        : job.student_visa === 'excluded' ? 'from-rose-500/15 to-rose-50 text-rose-800 ring-rose-200' : 'from-slate-100 to-slate-50 text-slate-500 ring-slate-200',
    },
  ]
  return (
    <section>
      <h3 className="mb-3 font-display text-sm font-bold uppercase tracking-wider text-slate-500">Visa snapshot</h3>
      <div className="grid grid-cols-2 gap-3">
        {tiles.map((t) => (
          <div key={t.label} className={`rounded-2xl bg-gradient-to-br p-4 ring-1 ring-inset ${t.tone}`}>
            <p className="text-[11px] font-bold uppercase tracking-wider opacity-70">{t.label}</p>
            <p className="mt-1 font-display text-2xl font-extrabold tracking-tight">{t.value}</p>
            <p className="mt-0.5 text-xs opacity-80">{t.sub}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
