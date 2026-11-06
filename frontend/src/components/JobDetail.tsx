import { useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import { api, formatDateTime, type JobDetail as JobDetailType, postedText, SOURCE_LABELS, timeAgo, WINDOW_HINT } from '../api'
import { Badge, CompanyAvatar, ExperienceBadge, RepeatBadge, SponsorBadge, TrackControl } from './ui'

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
    <section className="space-y-3 rounded-lg border border-slate-200 p-4">
      <h3 className="font-medium">Find the recruiter</h3>
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
export function JobDetailContent({ id }: { id: number }) {
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
  // When it went live: the company's own time, or when we caught it if the company only gives a date.
  const listed = j?.listed_at ?? j?.posted_at ?? null
  if (job.isLoading) return <p className="p-6 text-sm text-slate-500">Loading…</p>
  if (!j) return null
  return (
    <div className="space-y-5">
      <div className="flex items-start gap-4">
        <CompanyAvatar name={j.company} size="lg" />
        <div className="min-w-0">
          <h2 className="text-xl font-semibold leading-snug text-slate-900">{j.title}</h2>
          <p className="mt-0.5 text-sm text-slate-600">
            <span className="font-medium text-slate-800">{j.company}</span> · {j.location || 'Location not listed'}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {listed ? <span title={j.listed_until ? WINDOW_HINT : undefined}>Posted {postedText(listed, j.listed_until)}</span> : 'Posting date not given'}
            {!j.baseline && ` · found by us ${timeAgo(j.first_seen_at)}`}
            {j.department && ` · ${j.department}`}
          </p>
        </div>
      </div>
      {(j.closed_at || closedNow) ? (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          This job has closed{check.data?.detail ? ` (${check.data.detail})` : ''}. It's hidden from the feed now.
        </p>
      ) : (
        <p className="text-xs text-slate-500">
          {check.isFetching
            ? 'Checking the apply page…'
            : check.data?.open || recentlyChecked
              ? `✓ Apply page is up (checked ${timeAgo(check.data?.checked_at ?? j.checked_at!)})`
              : 'Couldn’t confirm the apply page is up; it may still be open.'}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <ExperienceBadge job={j} />
        <SponsorBadge filings={j.h1b_filings} />
        {j.remote && <Badge tone="slate">Remote</Badge>}
        {j.no_sponsor && <Badge tone="red">No sponsorship</Badge>}
        <RepeatBadge job={j} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <a href={j.url} target="_blank" rel="noreferrer" className="rounded-full bg-emerald-600 px-5 py-2 text-sm font-semibold text-white hover:bg-emerald-700">
          Apply on company site ↗
        </a>
        <TrackControl job={j} />
      </div>
      {j.no_sponsor && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          The description suggests this role doesn't sponsor visas, or needs US citizenship or a clearance.
        </p>
      )}
      <RepeatNote job={j} />
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-lg bg-slate-50 p-4 text-sm">
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
        <dt className="text-slate-500">Source</dt>
        <dd>{SOURCE_LABELS[j.source] ?? j.source}</dd>
        <dt className="text-slate-500">History</dt>
        <dd>{j.events.map((e) => `${e.kind} ${timeAgo(e.at)}`).join(' → ')}</dd>
      </dl>
      <Recruiters jobId={j.id} />
      <div>
        <h3 className="mb-2 font-semibold text-slate-900">About the job</h3>
        <div className="whitespace-pre-line text-sm leading-relaxed text-slate-800">{j.description || 'No description.'}</div>
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
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="float-right text-sm text-slate-500 hover:text-slate-900">
          Close ✕
        </button>
        <JobDetailContent id={id} />
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
