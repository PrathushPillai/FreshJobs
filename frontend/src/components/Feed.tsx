import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type ExpLevel, type Job, type JobQuery, SOURCE_LABELS, timeAgo } from '../api'
import { LAST_QUERY_KEY } from './Elsewhere'
import JobDetail, { JobDetailContent } from './JobDetail'
import LocationFilter from './LocationFilter'
import { Badge, Choice, CompanyAvatar, EXP_LABELS, ExperienceBadge, FilterPill, SponsorBadge, Toggle, TrackControl } from './ui'

const PAGE = 50
// "Date posted" uses the company's own posting date; "Found" uses when we first saw the job.
const POSTED_WINDOWS = [
  { label: 'Any time', hours: undefined },
  { label: 'Past hour', hours: 1 },
  { label: 'Past 6 hours', hours: 6 },
  { label: 'Past 24 hours', hours: 24 },
  { label: 'Past 3 days', hours: 72 },
  { label: 'Past week', hours: 168 },
  { label: 'Past 2 weeks', hours: 336 },
  { label: 'Past month', hours: 720 },
]
const FOUND_WINDOWS = [
  { label: 'Any time', hours: undefined },
  { label: 'Past hour', hours: 1 },
  { label: 'Past 6 hours', hours: 6 },
  { label: 'Past 24 hours', hours: 24 },
  { label: 'Past 3 days', hours: 72 },
  { label: 'Past week', hours: 168 },
]
const SORTS = [
  { value: 'posted', label: 'Most recently posted' },
  { value: 'newest', label: 'Most recently found' },
  { value: 'sponsor', label: 'Most H-1B filings' },
  { value: 'company', label: 'Company A–Z' },
] as const
const EXP_LEVELS: ExpLevel[] = ['entry', 'mid', 'senior']

type Filters = Required<Pick<JobQuery, 'related' | 'sort' | 'countries' | 'states' | 'cities' | 'exp_levels' | 'remote_only' | 'sponsors_only' | 'hide_no_sponsor' | 'show_reposts' | 'tracked'>> &
  Pick<JobQuery, 'max_years' | 'since_hours' | 'posted_within_hours'> & { include_unstated: boolean }

const DEFAULT_FILTERS: Filters = {
  related: true,
  sort: 'posted',
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
  posted_within_hours: undefined,
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

function useWideScreen() {
  const query = '(min-width: 1024px)'
  const [wide, setWide] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setWide(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return wide
}

function JobCard({ job, selected, onSelect }: { job: Job; selected: boolean; onSelect: () => void }) {
  // listed_at: when it went live (the company's time, or when we caught it if they only give a date).
  const posted = job.listed_at ?? job.posted_at
  return (
    <li
      onClick={onSelect}
      className={`cursor-pointer border-l-4 px-4 py-3.5 transition ${selected ? 'border-emerald-600 bg-emerald-50/60' : 'border-transparent hover:bg-slate-50'}`}
    >
      <div className="flex gap-3">
        <CompanyAvatar name={job.company} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h3 className={`min-w-0 break-words font-semibold leading-snug ${selected ? 'text-emerald-800' : 'text-slate-900'}`}>{job.title}</h3>
            {isNew(job) && <Badge tone="green">NEW</Badge>}
          </div>
          <p className="truncate text-sm text-slate-700">{job.company}</p>
          <p className="truncate text-sm text-slate-500">{job.location || 'Location not listed'}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            <ExperienceBadge job={job} />
            <SponsorBadge filings={job.h1b_filings} />
            {job.remote && <Badge tone="slate">Remote</Badge>}
            {job.is_repost && <Badge tone="amber">Repost</Badge>}
            {job.no_sponsor && <Badge tone="red">No sponsorship</Badge>}
            {job.closed_at && <Badge tone="slate">Closed</Badge>}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-slate-500">
              {posted ? (
                <span title={new Date(posted).toLocaleString()}>
                  Posted <span className="font-medium text-slate-700">{timeAgo(posted)}</span>
                </span>
              ) : (
                <span title="The company's site doesn't show when this was posted">Date not given</span>
              )}
              {!job.baseline && (
                <span title={new Date(job.first_seen_at).toLocaleString()}>
                  {' · '}Found <span className={isNew(job) ? 'font-medium text-emerald-700' : ''}>{timeAgo(job.first_seen_at)}</span>
                </span>
              )}
              <span className="text-slate-400"> · {SOURCE_LABELS[job.source] ?? job.source}</span>
            </p>
            <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
              <TrackControl job={job} compact />
              <a href={job.url} target="_blank" rel="noreferrer" className="rounded-full bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-700">
                Apply
              </a>
            </div>
          </div>
        </div>
      </div>
    </li>
  )
}

export default function Feed() {
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [filters, setFilters] = useState<Filters>(loadFilters)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const wide = useWideScreen()

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
  useEffect(() => {
    setOffset(0)
    try {
      if (q) localStorage.setItem(LAST_QUERY_KEY, q)
    } catch {
      // storage unavailable
    }
  }, [q])

  // About 1 in 5 postings never states a level; picking "Entry" shouldn't silently drop them.
  const { include_unstated, ...rest } = filters
  const exp_levels = rest.exp_levels.length && include_unstated ? [...rest.exp_levels, 'unknown' as const] : rest.exp_levels
  const query: JobQuery = { ...rest, exp_levels, q, limit: PAGE, offset }
  const jobs = useQuery({ queryKey: ['jobs', query], queryFn: () => api.jobs(query), placeholderData: keepPreviousData, refetchInterval: 60_000 })
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }))
  const toggleLevel = (level: ExpLevel) =>
    set({ exp_levels: filters.exp_levels.includes(level) ? filters.exp_levels.filter((l) => l !== level) : [...filters.exp_levels, level] })
  const related = jobs.data?.related_terms ?? []
  const items = jobs.data?.items

  // Two-pane view: keep a job open on the right, defaulting to the first result.
  useEffect(() => {
    if (wide && items?.length && !items.some((j) => j.id === selected)) setSelected(items[0].id)
  }, [wide, items, selected])

  const postedLabel = POSTED_WINDOWS.find((w) => w.hours === filters.posted_within_hours)?.label
  const foundLabel = FOUND_WINDOWS.find((w) => w.hours === filters.since_hours)?.label
  const locationLabel = [...filters.cities, ...filters.states].join(', ') || filters.countries.join(', ')
  const expLabel = filters.exp_levels.length
    ? filters.exp_levels.map((l) => EXP_LABELS[l].split(' ')[0]).join(', ')
    : filters.max_years !== undefined ? `≤ ${filters.max_years} yrs` : 'Experience'
  const moreActive = filters.show_reposts || filters.tracked !== 'any' || !filters.related

  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
        <div className="flex items-center gap-2 rounded-xl border border-slate-300 px-3 focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-100">
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5 shrink-0 text-slate-400" aria-hidden>
            <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 1 0 3.4 9.83l3.63 3.64a.75.75 0 1 0 1.06-1.06l-3.63-3.64A5.5 5.5 0 0 0 9 3.5ZM5 9a4 4 0 1 1 8 0 4 4 0 0 1-8 0Z" clipRule="evenodd" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Job title, skill or company — e.g. data analyst, react, Stripe"
            className="min-w-0 flex-1 bg-transparent py-2.5 text-base outline-none"
          />
          {search && (
            <button onClick={() => setSearch('')} className="text-sm text-slate-400 hover:text-slate-700" aria-label="Clear search">
              ✕
            </button>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <FilterPill label={postedLabel !== 'Any time' ? `Posted: ${postedLabel}` : 'Date posted'} active={filters.posted_within_hours !== undefined} onClear={() => set({ posted_within_hours: undefined })}>
            <p className="mb-1 px-2 text-xs text-slate-500">When the company posted it</p>
            {POSTED_WINDOWS.map((w) => (
              <Choice key={w.label} selected={filters.posted_within_hours === w.hours} onClick={() => set({ posted_within_hours: w.hours })}>
                {w.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill label={foundLabel !== 'Any time' ? `Found: ${foundLabel}` : 'Found by us'} active={filters.since_hours !== undefined} onClear={() => set({ since_hours: undefined })}>
            <p className="mb-1 px-2 text-xs text-slate-500">When FreshJobs first saw it (minutes after posting)</p>
            {FOUND_WINDOWS.map((w) => (
              <Choice key={w.label} selected={filters.since_hours === w.hours} onClick={() => set({ since_hours: w.hours })}>
                {w.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill
            label={expLabel}
            active={filters.exp_levels.length > 0 || filters.max_years !== undefined}
            onClear={() => set({ exp_levels: [], max_years: undefined, include_unstated: true })}
          >
            <div className="space-y-2">
              {EXP_LEVELS.map((level) => (
                <Toggle key={level} label={EXP_LABELS[level]} checked={filters.exp_levels.includes(level)} onChange={() => toggleLevel(level)} />
              ))}
              {filters.exp_levels.length > 0 && (
                <Toggle label="Also jobs that don't say" checked={filters.include_unstated} onChange={(v) => set({ include_unstated: v })} />
              )}
              <select
                value={filters.max_years ?? ''}
                onChange={(e) => set({ max_years: e.target.value === '' ? undefined : Number(e.target.value) })}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
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
          </FilterPill>

          <FilterPill label={locationLabel || 'Location'} active={!!locationLabel} onClear={() => set({ countries: [], states: [], cities: [] })}>
            <div className="w-[min(36rem,calc(100vw-3rem))]">
              <LocationFilter countries={filters.countries} states={filters.states} cities={filters.cities} onChange={(v) => set(v)} />
            </div>
          </FilterPill>

          <button
            onClick={() => set({ remote_only: !filters.remote_only })}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${filters.remote_only ? 'border-emerald-600 bg-emerald-600 text-white' : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-100'}`}
          >
            Remote
          </button>

          <FilterPill
            label={filters.sponsors_only ? 'H-1B sponsors' : 'Visa'}
            active={filters.sponsors_only || !filters.hide_no_sponsor}
            onClear={() => set({ sponsors_only: false, hide_no_sponsor: true })}
          >
            <div className="space-y-2">
              <Toggle label="Only companies that filed H-1Bs" checked={filters.sponsors_only} onChange={(v) => set({ sponsors_only: v })} />
              <Toggle label="Hide jobs that rule out sponsorship" checked={filters.hide_no_sponsor} onChange={(v) => set({ hide_no_sponsor: v })} />
            </div>
          </FilterPill>

          <FilterPill label={`Sort: ${SORTS.find((s) => s.value === filters.sort)?.label}`}>
            {SORTS.map((s) => (
              <Choice key={s.value} selected={filters.sort === s.value} onClick={() => set({ sort: s.value })}>
                {s.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill label="More" active={moreActive} onClear={() => set({ related: true, show_reposts: false, tracked: 'any' })}>
            <div className="space-y-2">
              <Toggle label="Include related roles in search" checked={filters.related} onChange={(v) => set({ related: v })} />
              <Toggle label="Show reposts & duplicates" checked={filters.show_reposts} onChange={(v) => set({ show_reposts: v })} />
              <select
                value={filters.tracked}
                onChange={(e) => set({ tracked: e.target.value as Filters['tracked'] })}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
              >
                <option value="any">Tracked and untracked</option>
                <option value="untracked">Hide jobs I've tracked</option>
                <option value="tracked">Only jobs I've tracked</option>
              </select>
            </div>
          </FilterPill>

          <button onClick={() => setFilters(DEFAULT_FILTERS)} className="ml-auto text-sm text-slate-500 hover:text-red-600">
            Reset all
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 px-1 text-sm text-slate-500">
        <span>
          {jobs.data ? (
            <>
              <b className="text-slate-800">{jobs.data.total.toLocaleString()}</b> jobs{q && <> for “{q}”</>}
            </>
          ) : jobs.isLoading ? (
            'Searching…'
          ) : null}
          {jobs.isFetching && jobs.data && <span className="ml-2 text-xs text-slate-400">updating…</span>}
        </span>
        {q && filters.related && related.length > 0 && (
          <span className="truncate text-xs" title={related.join(', ')}>
            Exact matches first, then related: {related.slice(0, 5).join(', ')}
            {related.length > 5 && ` +${related.length - 5}`}
          </span>
        )}
      </div>

      {jobs.isError && <p className="text-sm text-red-600">{(jobs.error as Error).message}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="min-w-0 space-y-3">
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-2xl border border-slate-200 bg-white">
            {items?.map((job) => (
              <JobCard key={job.id} job={job} selected={wide && job.id === selected} onSelect={() => setSelected(job.id)} />
            ))}
            {items?.length === 0 && <li className="px-4 py-12 text-center text-sm text-slate-500">No jobs match these filters.</li>}
          </ul>

          {jobs.data && jobs.data.total > PAGE && (
            <div className="flex items-center justify-center gap-3 text-sm">
              <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)} className="rounded-full border border-slate-300 bg-white px-4 py-1.5 disabled:opacity-40">
                Previous
              </button>
              <span className="text-slate-500">
                {offset + 1}–{Math.min(offset + PAGE, jobs.data.total)} of {jobs.data.total.toLocaleString()}
              </span>
              <button
                disabled={offset + PAGE >= jobs.data.total}
                onClick={() => setOffset(offset + PAGE)}
                className="rounded-full border border-slate-300 bg-white px-4 py-1.5 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </div>

        {wide && (
          <aside className="sticky top-20 h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6">
            {selected !== null ? <JobDetailContent id={selected} /> : <p className="text-sm text-slate-500">Select a job to see the details.</p>}
          </aside>
        )}
      </div>

      {!wide && selected !== null && <JobDetail id={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
