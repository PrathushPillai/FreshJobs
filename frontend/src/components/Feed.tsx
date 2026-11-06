import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type ExpLevel, type Job, type JobQuery, postedText, SOURCE_LABELS, timeAgo, WINDOW_HINT } from '../api'
import { LAST_QUERY_KEY } from './Elsewhere'
import JobDetail, { JobDetailContent } from './JobDetail'
import { FacetList } from './LocationFilter'
import RolesBar from './RolesBar'
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
  Pick<JobQuery, 'max_years' | 'since_hours' | 'posted_within_hours'> & { include_unstated: boolean; for_you: boolean }

const DEFAULT_FILTERS: Filters = {
  for_you: true,
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

/** Countries, plus the states and cities inside the one country (and state) picked, with open-job counts. */
function useLocationFacets(countries: string[], states: string[]) {
  const oneCountry = countries.length === 1 ? countries[0] : undefined
  const oneState = states.length === 1 ? states[0] : undefined
  return useQuery({
    queryKey: ['facets', oneCountry, oneState],
    queryFn: () => api.locationFacets(oneCountry, oneState),
    staleTime: 5 * 60_000,
  })
}

/** "State", "MA", "MA, NY", "MA +2": a pill's name, or what's picked in it. */
function pillLabel(name: string, values: string[]): string {
  if (values.length === 0) return name
  return values.length <= 2 ? values.join(', ') : `${values[0]} +${values.length - 1}`
}

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

/** How fresh a posting is, as a coloured badge: the thing this feed is for. */
function FreshnessBadge({ job }: { job: Job }) {
  const posted = job.listed_at ?? job.posted_at
  if (!posted) return <span className="whitespace-nowrap text-xs text-slate-400" title="The company's site doesn't show when this was posted">Date not given</span>
  const hours = (Date.now() - new Date(posted).getTime()) / 3_600_000
  const tone =
    job.listed_until ? 'bg-slate-100 text-slate-600'
      : hours < 1 ? 'bg-emerald-500 text-white shadow-sm shadow-emerald-500/30'
        : hours < 6 ? 'bg-emerald-100 text-emerald-800'
          : hours < 24 ? 'bg-sky-50 text-sky-800'
            : 'bg-slate-100 text-slate-600'
  return (
    <span
      title={job.listed_until ? WINDOW_HINT : `Posted ${new Date(posted).toLocaleString()}`}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${tone}`}
    >
      {hours < 1 && !job.listed_until && <span aria-hidden>⚡</span>}
      {postedText(posted, job.listed_until)}
    </span>
  )
}

const PIN = 'M10 2a6 6 0 0 0-6 6c0 4.5 6 10 6 10s6-5.5 6-10a6 6 0 0 0-6-6Zm0 8.5A2.5 2.5 0 1 1 10 5.5a2.5 2.5 0 0 1 0 5Z'

function JobCard({ job, selected, onSelect }: { job: Job; selected: boolean; onSelect: () => void }) {
  return (
    <li
      onClick={onSelect}
      className={`group relative cursor-pointer rounded-xl border bg-white p-4 transition ${
        selected ? 'border-emerald-500 shadow-md shadow-emerald-600/10 ring-1 ring-emerald-500' : 'border-slate-200 hover:border-slate-300 hover:shadow-md hover:shadow-slate-900/5'
      }`}
    >
      <div className="flex gap-3">
        <CompanyAvatar name={job.company} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className={`break-words text-[15px] font-semibold leading-snug ${selected ? 'text-emerald-800' : 'text-slate-900 group-hover:text-emerald-800'}`}>{job.title}</h3>
              <p className="mt-0.5 truncate text-sm font-medium text-slate-600">{job.company}</p>
            </div>
            <FreshnessBadge job={job} />
          </div>
          <p className="mt-1.5 flex min-w-0 items-center gap-1 text-sm text-slate-500">
            <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 shrink-0 fill-slate-400" aria-hidden>
              <path fillRule="evenodd" d={PIN} clipRule="evenodd" />
            </svg>
            <span className="truncate">{job.location || 'Location not listed'}</span>
            {job.remote && <span className="ml-1 shrink-0 rounded bg-violet-50 px-1.5 text-[11px] font-medium text-violet-700">Remote</span>}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <ExperienceBadge job={job} />
            <SponsorBadge filings={job.h1b_filings} />
            {isNew(job) && <Badge tone="green">Found {timeAgo(job.first_seen_at)}</Badge>}
            {job.is_repost && <Badge tone="amber">Repost</Badge>}
            {job.no_sponsor && <Badge tone="red">No sponsorship</Badge>}
            {job.closed_at && <Badge tone="slate">Closed</Badge>}
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-100 pt-2.5">
            <span className="truncate text-xs text-slate-400">via {SOURCE_LABELS[job.source] ?? job.source}</span>
            <div className="flex shrink-0 items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
              <TrackControl job={job} compact />
              <a href={job.url} target="_blank" rel="noreferrer" className="rounded-lg bg-emerald-600 px-3 py-1 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700">
                Apply ↗
              </a>
            </div>
          </div>
        </div>
      </div>
    </li>
  )
}

function CardSkeleton() {
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex gap-3">
        <div className="skeleton h-11 w-11 rounded-lg" />
        <div className="flex-1 space-y-2">
          <div className="skeleton h-4 w-3/4" />
          <div className="skeleton h-3 w-1/3" />
          <div className="skeleton h-3 w-1/2" />
        </div>
      </div>
    </li>
  )
}

/** "⚡ 784 posted in the last hour · 4,959 today": one click to the freshest jobs for the current filters. */
function FreshnessStrip({ base, active, onPick }: { base: JobQuery; active?: number; onPick: (hours: number | undefined) => void }) {
  const counts = [1, 6, 24].map((hours) => ({ hours, label: hours === 1 ? 'last hour' : hours === 24 ? 'today' : `last ${hours} h` }))
  const results = useQuery({
    queryKey: ['fresh-counts', base],
    queryFn: () => Promise.all(counts.map((c) => api.jobs({ ...base, posted_within_hours: c.hours, limit: 1, offset: 0 }))),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  return (
    <div className="flex flex-wrap items-center gap-2">
      {counts.map((c, i) => {
        const page = results.data?.[i]
        const on = active === c.hours
        return (
          <button
            key={c.hours}
            onClick={() => onPick(on ? undefined : c.hours)}
            className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-sm transition ${
              on ? 'bg-emerald-600 text-white shadow-sm' : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:ring-emerald-300'
            }`}
          >
            {i === 0 && <span aria-hidden>⚡</span>}
            <b className={on ? 'text-white' : 'text-slate-900'}>{page ? `${page.total.toLocaleString()}${page.total_capped ? '+' : ''}` : '…'}</b>
            posted {c.label}
          </button>
        )
      })}
    </div>
  )
}

export default function Feed() {
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [skip, setSkip] = useState<string[]>([])  // related roles switched off for this search
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
    setSkip([])
    try {
      if (q) localStorage.setItem(LAST_QUERY_KEY, q)
    } catch {
      // storage unavailable
    }
  }, [q])

  // About 1 in 5 postings never states a level; picking "Entry" shouldn't silently drop them.
  const { include_unstated, for_you, ...rest } = filters
  const exp_levels = rest.exp_levels.length && include_unstated ? [...rest.exp_levels, 'unknown' as const] : rest.exp_levels
  // "For you": titles matching the roles in Settings. A search looks through every job instead.
  const forYou = for_you && !q
  // A search includes related roles (exact-title matches always rank first); "Exact title" turns them
  // off, and each related role can be switched off on its own (the chips under the count).
  const exact = !filters.related
  const query: JobQuery = { ...rest, related: !exact, skip: exact ? [] : skip, exp_levels, q, alert_roles_only: forYou, limit: PAGE, offset }
  const jobs = useQuery({ queryKey: ['jobs', query], queryFn: () => api.jobs(query), placeholderData: keepPreviousData, refetchInterval: 60_000 })
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }))
  const toggleLevel = (level: ExpLevel) =>
    set({ exp_levels: filters.exp_levels.includes(level) ? filters.exp_levels.filter((l) => l !== level) : [...filters.exp_levels, level] })
  const related = jobs.data?.related_terms ?? []
  // How many of the results have the exact title, shown next to the total while related roles are on.
  const exactCount = useQuery({
    queryKey: ['jobs', 'exact-count', { ...query, limit: 1, offset: 0 }],
    queryFn: () => api.jobs({ ...query, related: false, skip: [], limit: 1, offset: 0 }),
    enabled: !!q && !exact && related.length > 0,
    placeholderData: keepPreviousData,
  })
  const items = jobs.data?.items

  // Two-pane view: keep a job open on the right, defaulting to the first result.
  useEffect(() => {
    if (wide && items?.length && !items.some((j) => j.id === selected)) setSelected(items[0].id)
  }, [wide, items, selected])

  const postedLabel = POSTED_WINDOWS.find((w) => w.hours === filters.posted_within_hours)?.label
  const foundLabel = FOUND_WINDOWS.find((w) => w.hours === filters.since_hours)?.label
  const places = useLocationFacets(filters.countries, filters.states)
  const oneCountry = filters.countries.length === 1 ? filters.countries[0] : undefined
  const expLabel = filters.exp_levels.length
    ? filters.exp_levels.map((l) => EXP_LABELS[l].split(' ')[0]).join(', ')
    : filters.max_years !== undefined ? `≤ ${filters.max_years} yrs` : 'Experience'
  const moreActive = filters.show_reposts || filters.tracked !== 'any'

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold tracking-tight text-slate-900">{forYou ? 'Jobs for you' : q ? 'Search results' : 'All jobs'}</h2>
            <p className="text-xs text-slate-500">
              {forYou ? 'Roles you picked in Settings, straight from company career sites' : q ? 'Searching every open job' : 'Every open job we track, straight from company career sites'}
            </p>
          </div>
          <div className="flex rounded-lg bg-slate-100 p-0.5 text-sm font-medium" role="tablist">
            {([true, false] as const).map((v) => (
              <button
                key={String(v)}
                role="tab"
                aria-selected={filters.for_you === v}
                onClick={() => set({ for_you: v })}
                className={`rounded-md px-3 py-1.5 transition ${filters.for_you === v ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
              >
                {v ? 'For you' : 'All jobs'}
              </button>
            ))}
          </div>
        </div>
        {forYou && (
          <div className="mb-3">
            <RolesBar />
          </div>
        )}
        <div className="flex items-center gap-2 rounded-xl border border-slate-300 bg-slate-50/60 px-3 transition focus-within:border-emerald-500 focus-within:bg-white focus-within:ring-4 focus-within:ring-emerald-100">
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
          <label
            className="flex shrink-0 cursor-pointer select-none items-center gap-2 border-l border-slate-200 py-1.5 pl-3 text-sm text-slate-600"
            title="On: only titles matching what you typed. Off: also related roles (exact matches still come first)."
          >
            <span className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${exact ? 'bg-emerald-600' : 'bg-slate-300'}`}>
              <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${exact ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </span>
            <input type="checkbox" className="sr-only" checked={exact} onChange={(e) => set({ related: !e.target.checked })} />
            Exact title
          </label>
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

          <FilterPill label={pillLabel('Country', filters.countries)} active={filters.countries.length > 0} onClear={() => set({ countries: [], states: [], cities: [] })}>
            <FacetList
              options={places.data?.countries}
              loading={places.isLoading}
              selected={filters.countries}
              onChange={(countries) => set({ countries, states: [], cities: [] })}
              placeholder="Search countries"
            />
          </FilterPill>

          <FilterPill label={pillLabel('State', filters.states)} active={filters.states.length > 0} onClear={() => set({ states: [], cities: [] })}>
            {oneCountry ? (
              <FacetList
                options={places.data?.states}
                loading={places.isFetching}
                selected={filters.states}
                onChange={(states) => set({ states, cities: [] })}
                placeholder={oneCountry === 'United States' ? 'Search states (CA, NY, TX…)' : 'Search states / provinces'}
              />
            ) : (
              <p className="max-w-56 text-sm text-slate-500">Pick one country first, then choose its states.</p>
            )}
          </FilterPill>

          <FilterPill label={pillLabel('City', filters.cities)} active={filters.cities.length > 0} onClear={() => set({ cities: [] })}>
            {oneCountry ? (
              <FacetList
                options={places.data?.cities}
                loading={places.isFetching}
                selected={filters.cities}
                onChange={(cities) => set({ cities })}
                placeholder={filters.states.length === 1 ? `Search cities in ${filters.states[0]}` : 'Search cities'}
              />
            ) : (
              <p className="max-w-56 text-sm text-slate-500">Pick one country first, then choose its cities.</p>
            )}
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

          <FilterPill
            label={`Sort: ${SORTS.find((s) => s.value === filters.sort)?.label}`}
            active={filters.sort !== DEFAULT_FILTERS.sort}
            onClear={() => set({ sort: DEFAULT_FILTERS.sort })}
          >
            {SORTS.map((s) => (
              <Choice key={s.value} selected={filters.sort === s.value} onClick={() => set({ sort: s.value })}>
                {s.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill label="More" active={moreActive} onClear={() => set({ show_reposts: false, tracked: 'any' })}>
            <div className="space-y-2">
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

      <FreshnessStrip base={{ ...query, limit: 1, offset: 0 }} active={filters.posted_within_hours} onPick={(hours) => set({ posted_within_hours: hours })} />

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 px-1 text-sm text-slate-500">
        <span>
          {jobs.data ? (
            <>
              <b className="text-slate-800">{jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}</b> jobs{q && <> for “{q}”</>}
              {q && exact && <> · exact title only</>}
              {q && !exact && related.length > 0 && exactCount.data && (
                <>
                  {' '}
                  · <b className="text-slate-800">{exactCount.data.total.toLocaleString()}{exactCount.data.total_capped && '+'}</b> with that exact title, shown first; the rest are related roles
                </>
              )}
            </>
          ) : jobs.isLoading ? (
            'Searching…'
          ) : null}
          {jobs.isFetching && jobs.data && <span className="ml-2 text-xs text-slate-400">updating…</span>}
        </span>
      </div>
      {q && !exact && related.length > 0 && (
        <div className="-mt-1 flex flex-wrap items-center gap-1.5 px-1">
          <span className="mr-1 text-xs font-medium text-slate-500">Including related roles (click to switch one off)</span>
          {related.slice(0, 16).map((r) => {
            const on = !skip.includes(r)
            return (
              <button
                key={r}
                onClick={() => setSkip(on ? [...skip, r] : skip.filter((x) => x !== r))}
                className={`rounded-full px-2.5 py-0.5 text-xs transition ${on ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-300 hover:ring-red-300' : 'bg-white text-slate-400 line-through ring-1 ring-slate-200 hover:ring-emerald-400'}`}
                title={on ? `Stop including "${r}"` : `Include "${r}" again`}
              >
                {on ? '✓ ' : ''}
                {r}
              </button>
            )
          })}
          {related.length > 16 && <span className="text-xs text-slate-400">+{related.length - 16} more included</span>}
          {skip.length > 0 && (
            <button onClick={() => setSkip([])} className="text-xs text-slate-500 hover:text-emerald-700">
              Include all
            </button>
          )}
        </div>
      )}

      {jobs.isError && <p className="text-sm text-red-600">{(jobs.error as Error).message}</p>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="min-w-0 space-y-3">
          <ul className="space-y-2.5">
            {!items && jobs.isLoading && Array.from({ length: 6 }, (_, i) => <CardSkeleton key={i} />)}
            {items?.map((job) => (
              <JobCard key={job.id} job={job} selected={wide && job.id === selected} onSelect={() => setSelected(job.id)} />
            ))}
            {items?.length === 0 && (
              <li className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
                <p className="font-medium text-slate-800">No jobs match these filters</p>
                <p className="mt-1 text-sm text-slate-500">
                  {forYou ? 'Try "All jobs", widen the date, or add roles in Settings.' : 'Try a wider date range, another location, or Reset all.'}
                </p>
              </li>
            )}
          </ul>

          {jobs.data && jobs.data.total > PAGE && (
            <div className="flex items-center justify-center gap-3 text-sm">
              <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)} className="rounded-full border border-slate-300 bg-white px-4 py-1.5 disabled:opacity-40">
                Previous
              </button>
              <span className="text-slate-500">
                {offset + 1}–{Math.min(offset + PAGE, jobs.data.total)} of {jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}
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
          <aside className="thin-scroll sticky top-20 h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            {selected !== null ? <JobDetailContent id={selected} /> : <p className="text-sm text-slate-500">Select a job to see the details.</p>}
          </aside>
        )}
      </div>

      {!wide && selected !== null && <JobDetail id={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
