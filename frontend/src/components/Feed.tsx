import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type ExpLevel, type Job, type JobQuery, type Status, postedText, REPEAT_KINDS, REPEAT_LABELS, SOURCE_LABELS, timeAgo, WINDOW_HINT } from '../api'
import { LAST_QUERY_KEY } from './Elsewhere'
import JobDetail, { JobDetailContent } from './JobDetail'
import { FacetList } from './LocationFilter'
import RolesBar from './RolesBar'
import { Badge, Choice, CompanyAvatar, EXP_LABELS, ExperienceBadge, FilterPill, RepeatBadge, SponsorBadge, Toggle, TrackControl, VisaSignalBadges } from './ui'

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

type Filters = Required<Pick<JobQuery, 'related' | 'sort' | 'countries' | 'states' | 'cities' | 'exp_levels' | 'remote_only' | 'sponsors_only' | 'everify_only' | 'hide_no_sponsor' | 'show_reposts' | 'repeat_kinds' | 'tracked'>> &
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
  everify_only: false,
  // Shown by default: some employers that say they don't sponsor can still be persuaded. Each such job
  // carries a red "No sponsorship" badge, and the Visa pill can hide them.
  hide_no_sponsor: false,
  show_reposts: false,
  repeat_kinds: REPEAT_KINDS,
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

// Filters saved before no-sponsorship jobs were shown by default had hiding switched on; switch it off once.
const SHOW_NO_SPONSOR_MIGRATION = 'freshjobs.migrated.showNoSponsor'

function loadFilters(): Filters {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    const filters: Filters = saved ? { ...DEFAULT_FILTERS, ...JSON.parse(saved) } : DEFAULT_FILTERS
    if (!localStorage.getItem(SHOW_NO_SPONSOR_MIGRATION)) {
      localStorage.setItem(SHOW_NO_SPONSOR_MIGRATION, '1')
      return { ...filters, hide_no_sponsor: false }
    }
    return filters
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
      : hours < 1 ? 'bg-gradient-to-r from-emerald-500 to-teal-500 text-white shadow-md shadow-emerald-500/30'
        : hours < 6 ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200'
          : hours < 24 ? 'bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200'
            : 'bg-slate-100 text-slate-500'
  return (
    <span
      title={job.listed_until ? WINDOW_HINT : `Posted ${new Date(posted).toLocaleString()}`}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-bold ${tone}`}
    >
      {hours < 1 && !job.listed_until && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-75" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-white" />
        </span>
      )}
      {postedText(posted, job.listed_until)}
    </span>
  )
}

const PIN = 'M10 2a6 6 0 0 0-6 6c0 4.5 6 10 6 10s6-5.5 6-10a6 6 0 0 0-6-6Zm0 8.5A2.5 2.5 0 1 1 10 5.5a2.5 2.5 0 0 1 0 5Z'

function JobCard({ job, selected, onSelect, index }: { job: Job; selected: boolean; onSelect: () => void; index: number }) {
  return (
    <li
      onClick={onSelect}
      style={{ animationDelay: `${Math.min(index, 10) * 30}ms` }}
      className={`group relative animate-rise cursor-pointer overflow-hidden rounded-2xl bg-white p-4 transition duration-200 ${
        selected
          ? 'shadow-xl shadow-brand-600/15 ring-2 ring-brand-500'
          : 'shadow-sm ring-1 ring-slate-200/80 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-slate-900/[0.07] hover:ring-brand-200'
      }`}
    >
      {selected && <span className="absolute inset-y-0 left-0 w-1 bg-gradient-to-b from-brand-500 to-fuchsia-500" aria-hidden />}
      <div className="flex gap-3.5">
        <CompanyAvatar name={job.company} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className={`break-words font-display text-[15px] font-bold leading-snug transition ${selected ? 'text-brand-700' : 'text-slate-900 group-hover:text-brand-700'}`}>{job.title}</h3>
              <p className="mt-0.5 truncate text-sm font-semibold text-slate-600">{job.company}</p>
            </div>
            <FreshnessBadge job={job} />
          </div>
          <p className="mt-1.5 flex min-w-0 items-center gap-1 text-[13px] text-slate-500">
            <svg viewBox="0 0 20 20" className="h-3.5 w-3.5 shrink-0 fill-slate-400" aria-hidden>
              <path fillRule="evenodd" d={PIN} clipRule="evenodd" />
            </svg>
            <span className="truncate">{job.location || 'Location not listed'}</span>
            {job.remote && <span className="ml-1 shrink-0 rounded-full bg-violet-50 px-2 text-[11px] font-semibold text-violet-700 ring-1 ring-inset ring-violet-200">Remote</span>}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-1">
            <ExperienceBadge job={job} />
            <SponsorBadge filings={job.h1b_filings} />
            <VisaSignalBadges job={job} />
            {isNew(job) && <Badge tone="green" dot>Found {timeAgo(job.first_seen_at)}</Badge>}
            <RepeatBadge job={job} />
            {job.no_sponsor && <Badge tone="red" dot>No sponsorship</Badge>}
            {job.closed_at && <Badge tone="slate">Closed</Badge>}
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-dashed border-slate-200 pt-3">
            <span className="truncate text-xs font-medium text-slate-400">via {SOURCE_LABELS[job.source] ?? job.source}</span>
            <div className="flex shrink-0 items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
              <TrackControl job={job} compact />
              <a
                href={job.url}
                target="_blank"
                rel="noreferrer"
                className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-3.5 py-1.5 text-xs font-bold text-white shadow-md shadow-brand-600/25 transition hover:brightness-110"
              >
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
    <li className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200/80">
      <div className="flex gap-3.5">
        <div className="skeleton h-12 w-12 rounded-xl" />
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
  // All three counts come from one request (one scan of the day's matches, newest first).
  const results = useQuery({
    queryKey: ['fresh-counts', base],
    queryFn: () => api.jobs({ ...base, posted_within_hours: undefined, fresh_counts: true, limit: 1, offset: 0 }),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  const styles = [
    { dot: 'bg-emerald-500', on: 'from-emerald-500 to-teal-500 shadow-emerald-500/30' },
    { dot: 'bg-teal-500', on: 'from-teal-500 to-sky-500 shadow-teal-500/30' },
    { dot: 'bg-sky-500', on: 'from-sky-500 to-brand-500 shadow-sky-500/30' },
  ]
  return (
    <div className="grid grid-cols-3 gap-2 sm:flex sm:flex-wrap">
      {counts.map((c, i) => {
        const n = results.data?.fresh?.[String(c.hours) as '1' | '6' | '24']
        const capped = c.hours === 24 && results.data?.total_capped
        const on = active === c.hours
        return (
          <button
            key={c.hours}
            onClick={() => onPick(on ? undefined : c.hours)}
            title={on ? 'Show every date again' : `Only jobs posted in the ${c.label}`}
            className={`flex flex-col items-start rounded-2xl px-4 py-2 text-left transition sm:flex-row sm:items-center sm:gap-2 sm:rounded-full sm:py-1.5 ${
              on
                ? `bg-gradient-to-r text-white shadow-lg ${styles[i].on}`
                : 'bg-white text-slate-600 shadow-sm ring-1 ring-slate-200/80 hover:-translate-y-px hover:ring-slate-300'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <span className={`relative flex h-2 w-2`}>
                {i === 0 && !on && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${on ? 'bg-white' : styles[i].dot}`} />
              </span>
              <b className={`font-display text-base font-extrabold ${on ? 'text-white' : 'text-slate-900'}`}>
                {n !== undefined ? `${n.toLocaleString()}${capped ? '+' : ''}` : '…'}
              </b>
            </span>
            <span className={`text-xs sm:text-sm ${on ? 'text-white/90' : ''}`}>posted {c.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export default function Feed({ stats }: { stats?: Status }) {
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
    enabled: !!q && !exact,  // in parallel with the results, not after them
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
      <section className="hero relative overflow-hidden rounded-3xl px-5 pb-6 pt-5 text-white shadow-2xl shadow-brand-950/30 sm:px-8 sm:pb-8 sm:pt-7">
        <div className="hero-grid pointer-events-none absolute inset-0" aria-hidden />
        <div className="pointer-events-none absolute -right-16 -top-24 h-72 w-72 rounded-full bg-fuchsia-500/20 blur-3xl" aria-hidden />
        <div className="relative">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-emerald-200 ring-1 ring-white/15 backdrop-blur">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              {stats ? `${stats.new_last_24h.toLocaleString()} new jobs in the last 24 hours` : 'Checking company career sites every few minutes'}
            </span>
            <div className="flex rounded-full bg-white/10 p-1 text-sm font-semibold ring-1 ring-white/15 backdrop-blur" role="tablist">
              {([true, false] as const).map((v) => (
                <button
                  key={String(v)}
                  role="tab"
                  aria-selected={filters.for_you === v}
                  onClick={() => set({ for_you: v })}
                  className={`rounded-full px-4 py-1.5 transition ${filters.for_you === v ? 'bg-white text-brand-800 shadow' : 'text-white/80 hover:text-white'}`}
                >
                  {v ? '✦ For you' : 'All jobs'}
                </button>
              ))}
            </div>
          </div>
          <h2 className="mt-4 font-display text-2xl font-extrabold leading-tight tracking-tight sm:text-4xl">
            {forYou ? (
              <>Fresh roles <span className="bg-gradient-to-r from-emerald-300 via-teal-200 to-sky-300 bg-clip-text text-transparent">picked for you</span></>
            ) : q ? (
              <>Results for <span className="bg-gradient-to-r from-emerald-300 via-teal-200 to-sky-300 bg-clip-text text-transparent">“{q}”</span></>
            ) : (
              <>Every open job, <span className="bg-gradient-to-r from-emerald-300 via-teal-200 to-sky-300 bg-clip-text text-transparent">minutes fresh</span></>
            )}
          </h2>
          <p className="mt-1.5 max-w-2xl text-sm text-brand-100/80 sm:text-base">
            {forYou
              ? 'Your roles, straight from company career sites, with H-1B, E-Verify and OPT signals on every job.'
              : q
                ? 'Searching every open job, related roles included. Exact matches come first.'
                : `${stats ? stats.open_jobs.toLocaleString() : 'All the'} open jobs from ${stats ? stats.companies.toLocaleString() : 'thousands of'} company boards, newest first.`}
          </p>
          <div className="mt-5 flex items-center gap-2 rounded-2xl bg-white p-1.5 pl-4 text-slate-900 shadow-2xl shadow-brand-950/40 ring-4 ring-white/10 transition focus-within:ring-white/30">
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5 shrink-0 text-brand-500" aria-hidden>
              <path fillRule="evenodd" d="M9 3.5a5.5 5.5 0 1 0 3.4 9.83l3.63 3.64a.75.75 0 1 0 1.06-1.06l-3.63-3.64A5.5 5.5 0 0 0 9 3.5ZM5 9a4 4 0 1 1 8 0 4 4 0 0 1-8 0Z" clipRule="evenodd" />
            </svg>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search any role, skill or company: data engineer, react, Stripe…"
              className="min-w-0 flex-1 bg-transparent py-3 text-base outline-none placeholder:text-slate-400"
            />
            {search && (
              <button onClick={() => setSearch('')} className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Clear search">
                ✕
              </button>
            )}
            <label
              className="flex shrink-0 cursor-pointer select-none items-center gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-sm font-medium text-slate-600 ring-1 ring-slate-200 hover:bg-slate-100"
              title="On: only titles matching what you typed. Off: also related roles (exact matches still come first)."
            >
              <span className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${exact ? 'bg-brand-600' : 'bg-slate-300'}`}>
                <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${exact ? 'translate-x-4' : 'translate-x-0.5'}`} />
              </span>
              <input type="checkbox" className="sr-only" checked={exact} onChange={(e) => set({ related: !e.target.checked })} />
              <span className="hidden sm:inline">Exact title</span>
            </label>
          </div>
        </div>
      </section>

      {/* z-20: the dropdowns open over the job list below, not under it (the blur makes this a stacking context). */}
      <section className="relative z-20 rounded-3xl bg-white/80 p-4 shadow-sm ring-1 ring-slate-200/70 backdrop-blur">
        {forYou && (
          <div className="mb-3">
            <RolesBar />
          </div>
        )}
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
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium shadow-sm transition ${
              filters.remote_only
                ? 'bg-gradient-to-r from-brand-600 to-violet-600 text-white shadow-brand-600/25'
                : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:ring-slate-300'
            }`}
          >
            ⌂ Remote
          </button>

          <FilterPill
            label={filters.sponsors_only ? 'H-1B sponsors' : 'Visa'}
            active={filters.sponsors_only || filters.everify_only || filters.hide_no_sponsor}
            onClear={() => set({ sponsors_only: false, everify_only: false, hide_no_sponsor: false })}
          >
            <div className="space-y-2">
              <Toggle label="Only companies that filed H-1Bs" checked={filters.sponsors_only} onChange={(v) => set({ sponsors_only: v })} />
              <Toggle
                label="Only E-Verify employers (needed for STEM OPT)"
                checked={filters.everify_only}
                onChange={(v) => set({ everify_only: v })}
              />
              <Toggle
                label="Hide jobs that rule out sponsorship or OPT/CPT"
                checked={filters.hide_no_sponsor}
                onChange={(v) => set({ hide_no_sponsor: v })}
              />
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

          <FilterPill label="More" active={moreActive} onClear={() => set({ show_reposts: false, repeat_kinds: REPEAT_KINDS, tracked: 'any' })}>
            <div className="space-y-2">
              <Toggle
                label="Show repeated jobs (hidden by default)"
                checked={filters.show_reposts}
                onChange={(v) => set({ show_reposts: v, repeat_kinds: REPEAT_KINDS })}
              />
              {filters.show_reposts && (
                <div className="ml-5 space-y-1 border-l border-slate-200 pl-3">
                  {REPEAT_KINDS.map((k) => (
                    <label key={k} title={REPEAT_LABELS[k].hint} className="flex cursor-pointer select-none items-start gap-1.5 text-sm text-slate-700">
                      <input
                        type="checkbox"
                        className="mt-1 accent-brand-600"
                        checked={filters.repeat_kinds.includes(k)}
                        onChange={(e) => {
                          const kinds = e.target.checked ? [...filters.repeat_kinds, k] : filters.repeat_kinds.filter((x) => x !== k)
                          set(kinds.length ? { repeat_kinds: kinds } : { show_reposts: false, repeat_kinds: REPEAT_KINDS })
                        }}
                      />
                      <span>
                        {REPEAT_LABELS[k].label}s
                        <span className="block text-xs text-slate-400">{REPEAT_LABELS[k].hint}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
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

          <button onClick={() => setFilters(DEFAULT_FILTERS)} className="ml-auto rounded-full px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-rose-50 hover:text-rose-600">
            ↺ Reset all
          </button>
        </div>
      </section>

      <FreshnessStrip base={{ ...query, limit: 1, offset: 0 }} active={filters.posted_within_hours} onPick={(hours) => set({ posted_within_hours: hours })} />

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 px-1 text-sm text-slate-500">
        <span>
          {jobs.data ? (
            <>
              <b className="font-display text-base font-extrabold text-slate-900">{jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}</b> jobs{q && <> for “{q}”</>}
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
          {jobs.isFetching && jobs.data && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-brand-500">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" /> updating
            </span>
          )}
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
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium transition ${on ? 'bg-brand-50 text-brand-800 ring-1 ring-brand-200 hover:ring-rose-300' : 'bg-white text-slate-400 line-through ring-1 ring-slate-200 hover:ring-brand-400'}`}
                title={on ? `Stop including "${r}"` : `Include "${r}" again`}
              >
                {on ? '✓ ' : ''}
                {r}
              </button>
            )
          })}
          {related.length > 16 && <span className="text-xs text-slate-400">+{related.length - 16} more included</span>}
          {skip.length > 0 && (
            <button onClick={() => setSkip([])} className="text-xs text-slate-500 hover:text-brand-700">
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
            {items?.map((job, i) => (
              <JobCard key={job.id} index={i} job={job} selected={wide && job.id === selected} onSelect={() => setSelected(job.id)} />
            ))}
            {items?.length === 0 && (
              <li className="rounded-3xl border-2 border-dashed border-slate-200 bg-white/70 px-6 py-14 text-center">
                <p className="text-3xl" aria-hidden>🔍</p>
                <p className="mt-2 font-display text-lg font-bold text-slate-800">No jobs match these filters</p>
                <p className="mt-1 text-sm text-slate-500">
                  {forYou ? 'Try "All jobs", widen the date, or add roles in Settings.' : 'Try a wider date range, another location, or Reset all.'}
                </p>
              </li>
            )}
          </ul>

          {jobs.data && jobs.data.total > PAGE && (
            <div className="flex items-center justify-center gap-3 text-sm">
              <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)} className="rounded-full bg-white px-4 py-1.5 font-medium shadow-sm ring-1 ring-slate-200 hover:ring-brand-300 disabled:opacity-40">
                Previous
              </button>
              <span className="text-slate-500">
                {offset + 1}–{Math.min(offset + PAGE, jobs.data.total)} of {jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}
              </span>
              <button
                disabled={offset + PAGE >= jobs.data.total}
                onClick={() => setOffset(offset + PAGE)}
                className="rounded-full bg-white px-4 py-1.5 font-medium shadow-sm ring-1 ring-slate-200 hover:ring-brand-300 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </div>

        {wide && (
          <aside className="thin-scroll sticky top-24 h-[calc(100vh-7rem)] overflow-y-auto rounded-3xl bg-white p-6 shadow-xl shadow-slate-900/[0.06] ring-1 ring-slate-200/80">
            {selected !== null ? <JobDetailContent id={selected} /> : <p className="text-sm text-slate-500">Select a job to see the details.</p>}
          </aside>
        )}
      </div>

      {!wide && selected !== null && <JobDetail id={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}
