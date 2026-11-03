import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type ExpLevel, formatDateTime, type Job, type JobQuery, SOURCE_LABELS, timeAgo } from '../api'
import JobDetail from './JobDetail'
import LocationFilter from './LocationFilter'
import { Badge, EXP_LABELS, ExperienceBadge, SponsorBadge, Toggle, TrackControl } from './ui'

const PAGE = 50
const WINDOWS = [
  { label: 'Any time', hours: undefined },
  { label: '1 h', hours: 1 },
  { label: '6 h', hours: 6 },
  { label: '24 h', hours: 24 },
  { label: '3 days', hours: 72 },
  { label: '7 days', hours: 168 },
]
const EXP_LEVELS: ExpLevel[] = ['entry', 'mid', 'senior']

type Filters = Required<Pick<JobQuery, 'related' | 'sort' | 'countries' | 'states' | 'cities' | 'exp_levels' | 'remote_only' | 'sponsors_only' | 'hide_no_sponsor' | 'show_reposts' | 'tracked'>> &
  Pick<JobQuery, 'max_years' | 'since_hours'> & { include_unstated: boolean }

const DEFAULT_FILTERS: Filters = {
  related: true,
  sort: 'newest',
  countries: ['United States'],
  states: [],
  cities: [],
  exp_levels: [],
  include_unstated: true,
  remote_only: false,
  sponsors_only: false,
  hide_no_sponsor: true,
  show_reposts: false,
  tracked: 'any',
  max_years: undefined,
  since_hours: undefined,
}
const STORAGE_KEY = 'freshjobs.filters.v2'

function loadFilters(): Filters {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved ? { ...DEFAULT_FILTERS, ...JSON.parse(saved) } : DEFAULT_FILTERS
  } catch {
    return DEFAULT_FILTERS
  }
}

function isNew(job: Job) {
  return !job.baseline && Date.now() - new Date(job.first_seen_at).getTime() < 60 * 60_000
}

function PostedTime({ job }: { job: Job }) {
  // posted_at is the company's own date; first_seen_at is when this app first saw it.
  const posted = job.posted_at ?? (job.baseline ? job.first_seen_at : null)
  return (
    <div className="text-right text-xs leading-tight text-slate-500">
      {posted && (
        <div title={new Date(posted).toLocaleString()}>
          Posted <span className="font-medium text-slate-700">{timeAgo(posted)}</span> · {formatDateTime(posted)}
        </div>
      )}
      {!job.baseline && (
        <div title={new Date(job.first_seen_at).toLocaleString()}>
          Found by us <span className={isNew(job) ? 'font-medium text-emerald-700' : ''}>{timeAgo(job.first_seen_at)}</span>
        </div>
      )}
    </div>
  )
}

export default function Feed() {
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState<Filters>(loadFilters)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const [showMore, setShowMore] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    setOffset(0)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
    } catch {
      // storage unavailable: filters just won't persist
    }
  }, [filters])
  useEffect(() => setOffset(0), [q])

  // About 1 in 5 postings never states a level; picking "Entry" shouldn't silently drop them.
  const { include_unstated, ...rest } = filters
  const exp_levels = rest.exp_levels.length && include_unstated ? [...rest.exp_levels, 'unknown' as const] : rest.exp_levels
  const query: JobQuery = { ...rest, exp_levels, q, limit: PAGE, offset }
  const jobs = useQuery({ queryKey: ['jobs', query], queryFn: () => api.jobs(query), placeholderData: keepPreviousData, refetchInterval: 60_000 })
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }))
  const toggleLevel = (level: ExpLevel) =>
    set({ exp_levels: filters.exp_levels.includes(level) ? filters.exp_levels.filter((l) => l !== level) : [...filters.exp_levels, level] })
  const related = jobs.data?.related_terms ?? []

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search any role or company, e.g. data analyst, product designer, nurse, Stripe…"
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500"
          />
          <select
            value={filters.sort}
            onChange={(e) => set({ sort: e.target.value as Filters['sort'] })}
            className="rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
            aria-label="Sort"
          >
            <option value="newest">Sort: newest found</option>
            <option value="posted">Sort: newest posted</option>
            <option value="sponsor">Sort: most H-1B filings</option>
            <option value="company">Sort: company A–Z</option>
          </select>
        </div>

        {q && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
            <Toggle label="Include related roles" checked={filters.related} onChange={(v) => set({ related: v })} />
            {filters.related && related.length > 0 && (
              <span className="ml-1">
                Also matching: {related.slice(0, 8).join(', ')}
                {related.length > 8 && ` +${related.length - 8} more`}
              </span>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-sm font-medium text-slate-700">Found within</span>
          {WINDOWS.map((w) => (
            <button
              key={w.label}
              onClick={() => set({ since_hours: w.hours })}
              className={`rounded-full px-2.5 py-1 text-xs ${filters.since_hours === w.hours ? 'bg-emerald-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
            >
              {w.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-sm font-medium text-slate-700">Experience</span>
          {EXP_LEVELS.map((level) => (
            <button
              key={level}
              onClick={() => toggleLevel(level)}
              className={`rounded-full px-2.5 py-1 text-xs ${filters.exp_levels.includes(level) ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
            >
              {EXP_LABELS[level]}
            </button>
          ))}
          {filters.exp_levels.length > 0 && (
            <span className="ml-1">
              <Toggle label="+ jobs that don't say" checked={filters.include_unstated} onChange={(v) => set({ include_unstated: v })} />
            </span>
          )}
          <select
            value={filters.max_years ?? ''}
            onChange={(e) => set({ max_years: e.target.value === '' ? undefined : Number(e.target.value) })}
            className="ml-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
            aria-label="Maximum years required"
          >
            <option value="">Any years required</option>
            {[0, 1, 2, 3, 4, 5, 7, 10].map((y) => (
              <option key={y} value={y}>
                Asks ≤ {y} years
              </option>
            ))}
          </select>
        </div>

        <LocationFilter countries={filters.countries} states={filters.states} cities={filters.cities} onChange={(v) => set(v)} />

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Toggle label="Remote only" checked={filters.remote_only} onChange={(v) => set({ remote_only: v })} />
          <Toggle label="H-1B sponsors only" checked={filters.sponsors_only} onChange={(v) => set({ sponsors_only: v })} />
          <Toggle label="Hide no-sponsorship jobs" checked={filters.hide_no_sponsor} onChange={(v) => set({ hide_no_sponsor: v })} />
          <button onClick={() => setShowMore(!showMore)} className="text-xs text-slate-500 underline-offset-2 hover:underline">
            {showMore ? 'Fewer options' : 'More options'}
          </button>
          {showMore && (
            <>
              <Toggle label="Show reposts & duplicates" checked={filters.show_reposts} onChange={(v) => set({ show_reposts: v })} />
              <select
                value={filters.tracked}
                onChange={(e) => set({ tracked: e.target.value as Filters['tracked'] })}
                className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
              >
                <option value="any">Tracked and untracked</option>
                <option value="untracked">Hide jobs I've tracked</option>
                <option value="tracked">Only jobs I've tracked</option>
              </select>
              <button onClick={() => setFilters(DEFAULT_FILTERS)} className="text-xs text-red-600 hover:underline">
                Reset filters
              </button>
            </>
          )}
        </div>
      </div>

      <div className="flex items-baseline justify-between text-sm text-slate-500">
        <span>{jobs.data ? `${jobs.data.total.toLocaleString()} jobs` : jobs.isLoading ? 'Loading…' : ''}</span>
        <span className="hidden text-xs sm:inline">Straight from company job boards, checked every few minutes</span>
      </div>

      {jobs.isError && <p className="text-sm text-red-600">{(jobs.error as Error).message}</p>}

      <ul className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200 bg-white">
        {jobs.data?.items.map((job) => (
          <li key={job.id} className="flex flex-col gap-2 px-4 py-3 hover:bg-slate-50 md:flex-row md:items-center md:gap-4">
            <button onClick={() => setSelected(job.id)} className="min-w-0 flex-1 text-left">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-slate-900">{job.title}</span>
                {isNew(job) && <Badge tone="green">NEW</Badge>}
                <ExperienceBadge job={job} />
                <SponsorBadge filings={job.h1b_filings} />
                {job.remote && <Badge tone="slate">Remote</Badge>}
                {job.is_repost && <Badge tone="amber">Repost</Badge>}
                {job.no_sponsor && <Badge tone="red">No sponsorship</Badge>}
                {job.closed_at && <Badge tone="slate">Closed</Badge>}
              </div>
              <div className="mt-0.5 truncate text-sm text-slate-500">
                <span className="font-medium text-slate-700">{job.company}</span>
                {job.location && ` · ${job.location}`}
                <span className="text-slate-400"> · via {SOURCE_LABELS[job.source] ?? job.source}</span>
              </div>
            </button>
            <div className="flex shrink-0 items-center justify-between gap-3 md:justify-end">
              <PostedTime job={job} />
              <TrackControl job={job} compact />
              <a
                href={job.url}
                target="_blank"
                rel="noreferrer"
                className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
              >
                Apply
              </a>
            </div>
          </li>
        ))}
        {jobs.data?.items.length === 0 && <li className="px-4 py-10 text-center text-sm text-slate-500">No jobs match these filters.</li>}
      </ul>

      {jobs.data && jobs.data.total > PAGE && (
        <div className="flex items-center justify-center gap-3 text-sm">
          <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)} className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40">
            Previous
          </button>
          <span className="text-slate-500">
            {offset + 1}–{Math.min(offset + PAGE, jobs.data.total)} of {jobs.data.total.toLocaleString()}
          </span>
          <button
            disabled={offset + PAGE >= jobs.data.total}
            onClick={() => setOffset(offset + PAGE)}
            className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}

      {selected !== null && <JobDetail id={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
