import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, formatDateTime, SOURCE_LABELS, timeAgo } from '../api'
import { ExperienceBadge, SponsorBadge, TrackControl } from './ui'

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

export default function JobDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const job = useQuery({ queryKey: ['job', id], queryFn: () => api.job(id) })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const j = job.data
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="float-right text-sm text-slate-500 hover:text-slate-900">
          Close ✕
        </button>
        {job.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {j && (
          <div className="space-y-4">
            <div>
              <h2 className="pr-16 text-xl font-semibold">{j.title}</h2>
              <p className="text-sm text-slate-600">
                {j.company} · {j.location || 'Location not listed'}
                {j.department && ` · ${j.department}`}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <a href={j.url} target="_blank" rel="noreferrer" className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700">
                Apply on the company's site
              </a>
              <TrackControl job={j} />
            </div>
            {j.no_sponsor && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                The description suggests this role doesn't sponsor visas, or needs US citizenship or a clearance.
              </p>
            )}
            {j.is_repost && (
              <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
                This looks like a repost or duplicate of a job we saw earlier (#{j.repost_of_id}).
              </p>
            )}
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-slate-500">Posted</dt>
              <dd>{j.posted_at ? `${formatDateTime(j.posted_at)} (${timeAgo(j.posted_at)})` : 'Not given by the source'}</dd>
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
            <div className="whitespace-pre-line border-t border-slate-200 pt-4 text-sm leading-relaxed text-slate-800">{j.description || 'No description.'}</div>
          </div>
        )}
      </aside>
    </div>
  )
}
