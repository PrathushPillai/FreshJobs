import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, type ExpLevel, type Job, type JobQuery, type Status, postedText, REPEAT_KINDS, REPEAT_LABELS, SOURCE_LABELS, timeAgo, WINDOW_HINT } from '../api'
import { LAST_QUERY_KEY } from './Elsewhere'
import JobDetail, { JobDetailContent } from './JobDetail'
import { FacetList } from './LocationFilter'
import RolesBar from './RolesBar'
import { Badge, Choice, CompanyAvatar, EXP_LABELS, EmployerKindBadge, ExperienceBadge, FilterPill, MatchLine, MatchRing, RepeatBadge, SponsorBadge, SponsorLikelyBadge, Toggle, TrackControl, VisaSignalBadges } from './ui'

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
  { value: 'match', label: 'Best match' },
  { value: 'posted', label: 'Most recently posted' },
  { value: 'newest', label: 'Most recently found' },
  { value: 'sponsor', label: 'Most H-1B filings' },
  { value: 'company', label: 'Company A–Z' },
] as const
const EXP_LEVELS: ExpLevel[] = ['entry', 'mid', 'senior']
// The Visa pill's sponsorship verdict choice (backend sponsorship.sponsor_likelihood).
const SPONSOR_CHOICES = [
  { value: 'any', label: 'All jobs', hint: 'Every employer, sponsor or not' },
  { value: 'likely', label: 'Likely sponsors', hint: 'Says it sponsors, or 10+ H-1B filings' },
  { value: 'possible', label: 'Likely or may sponsor', hint: 'Also employers with a few H-1Bs, or E-Verify (STEM OPT)' },
] as const

type Filters = Required<Pick<JobQuery, 'related' | 'sort' | 'countries' | 'states' | 'cities' | 'exp_levels' | 'remote_only' | 'sponsors_only' | 'sponsor_likely' | 'everify_only' | 'hide_consultancies' | 'hide_no_sponsor' | 'show_reposts' | 'repeat_kinds' | 'tracked'>> &
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
  sponsor_likely: 'any',  // 'likely' / 'possible': the Visa pill's sponsorship choice
  everify_only: false,
  hide_consultancies: false,  // shown with a badge by default; More can hide them
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
const STORAGE_KEY = 'freshjobs.filters.v2'  // before v3: one set shared by both views; seeds both
// For you and All jobs each keep their own filters and sort, so Best match in For you doesn't change All jobs.
const VIEWS_KEY = 'freshjobs.filters.v3'
type View = 'forYou' | 'all'
type ViewFilters = { view: View; forYou: Filters; all: Filters }

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

function loadViews(): ViewFilters {
  try {
    const saved = localStorage.getItem(VIEWS_KEY)
    if (saved) {
      const v = JSON.parse(saved) as Partial<ViewFilters>
      return {
        view: v.view === 'all' ? 'all' : 'forYou',
        forYou: { ...DEFAULT_FILTERS, ...v.forYou, for_you: true },
        all: { ...DEFAULT_FILTERS, ...v.all, for_you: false },
      }
    }
  } catch {
    // storage unavailable or unreadable: start from the old shared set
  }
  const old = loadFilters()  // the one set both views shared before; each view starts from it
  return { view: old.for_you ? 'forYou' : 'all', forYou: { ...old, for_you: true }, all: { ...old, for_you: false } }
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

/** One card per role: the same title at the same employer in several places (a chain hiring in three cities posts
 * three jobs) is one card with links to each place, as in the alert emails (backend alerts.group_jobs). Each place is
 * still its own job with its own apply link; only the page's own jobs are grouped, in their order. */
function groupSameRole(items: Job[]): { job: Job; others: Job[] }[] {
  const groups = new Map<string, { job: Job; others: Job[] }>()
  for (const job of items) {
    const key = `${job.company.trim().toLowerCase()}|${job.title.trim().toLowerCase()}`
    const group = groups.get(key)
    if (group) group.others.push(job)
    else groups.set(key, { job, others: [] })
  }
  return [...groups.values()]
}

/** "Los Angeles, California, United States" -> "Los Angeles, California": enough to tell the places apart. */
function shortPlace(job: Job): string {
  const parts = (job.location || 'Location not listed').split(',').map((p) => p.trim()).filter(Boolean)
  return parts.length > 2 && /^(united states|usa|us)$/i.test(parts[parts.length - 1]) ? parts.slice(0, -1).join(', ') : parts.join(', ')
}

function JobCard({ job: first, others = [], selectedId, onPick, index }: {
  job: Job; others?: Job[]; selectedId: number | null; onPick: (id: number) => void; index: number
}) {
  // The card shows the place that's open (or the first one); the other places are links.
  const job = others.find((o) => o.id === selectedId) ?? first
  const selected = selectedId === job.id
  const onSelect = () => onPick(job.id)
  const places = [first, ...others]
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
        <CompanyAvatar key={job.logo_domain ?? job.company} name={job.company} domain={job.logo_domain} />
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
          {others.length > 0 && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[12px] text-slate-500">
              <span className="font-semibold text-slate-600">{places.length} places:</span>
              {places.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onPick(p.id) }}
                  title={`Open the ${shortPlace(p)} posting`}
                  className={`rounded-full px-2 py-0.5 ring-1 ring-inset transition ${
                    p.id === job.id ? 'bg-brand-50 font-semibold text-brand-700 ring-brand-300' : 'bg-white text-slate-600 ring-slate-200 hover:text-brand-700 hover:ring-brand-300'
                  }`}
                >
                  {shortPlace(p)}
                </button>
              ))}
            </div>
          )}
          <div className="mt-2.5 flex flex-wrap items-center gap-1">
            <ExperienceBadge job={job} />
            <SponsorLikelyBadge job={job} />
            <SponsorBadge filings={job.h1b_filings} />
            <VisaSignalBadges job={job} />
            <EmployerKindBadge kind={job.employer_kind} />
            {isNew(job) && <Badge tone="green" dot>Found {timeAgo(job.first_seen_at)}</Badge>}
            <RepeatBadge job={job} />
            {job.no_sponsor && <Badge tone="red" dot>No sponsorship</Badge>}
            {job.closed_at && <Badge tone="slate">Closed</Badge>}
          </div>
          {job.match && <MatchLine match={job.match} />}
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
        // Counting stops at 2,000 (COUNT_CAP): any bucket that reaches it means "2,000 or more".
        const capped = n !== undefined && n >= 2000
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

/** "🔥 Top matches posted today": the best-fitting jobs of the last 24 hours, one click to open. */
function TopMatches({ base, onOpen }: { base: JobQuery; onOpen: (id: number) => void }) {
  const top = useQuery({
    queryKey: ['top-matches', base],
    queryFn: () => api.jobs({ ...base, sort: 'match', posted_within_hours: 24, limit: 8, offset: 0 }),
    refetchInterval: 60_000,
  })
  const items = (top.data?.items ?? []).filter((j) => j.match && j.match.score >= 50)
  if (!items.length) return null
  return (
    <section className="rounded-3xl bg-gradient-to-r from-emerald-50 via-white to-brand-50 p-4 ring-1 ring-emerald-100">
      <h3 className="mb-3 font-display text-sm font-bold uppercase tracking-wider text-emerald-800">🔥 Your top matches posted today</h3>
      <div className="no-scrollbar -mx-1 flex gap-3 overflow-x-auto px-1 pb-1">
        {items.map((j) => (
          <button
            key={j.id}
            onClick={() => onOpen(j.id)}
            className="flex w-64 shrink-0 items-center gap-3 rounded-2xl bg-white p-3 text-left shadow-sm ring-1 ring-slate-200/80 transition hover:-translate-y-0.5 hover:shadow-md hover:ring-brand-300"
          >
            <MatchRing score={j.match!.score} size={44} />
            <span className="min-w-0">
              <span className="line-clamp-2 font-display text-sm font-bold leading-snug text-slate-900">{j.title}</span>
              <span className="block truncate text-xs text-slate-500">{j.company} · {postedText(j.listed_at ?? j.posted_at ?? j.first_seen_at, j.listed_until)}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}

// Once a resume is uploaded, "Best match" becomes the sort, once (the user can change it back).
const MATCH_SORT_MIGRATION = 'freshjobs.migrated.matchSort'

export default function Feed({ stats, onOpenProfile }: { stats?: Status; onOpenProfile: () => void }) {
  const profile = useQuery({ queryKey: ['profile'], queryFn: api.profile, staleTime: 60_000 })
  const hasResume = !!profile.data?.has_resume
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [skip, setSkip] = useState<string[]>([])  // related roles switched off for this search
  const [views, setViews] = useState<ViewFilters>(loadViews)
  const filters = views[views.view]
  const setFilters = (update: Filters | ((f: Filters) => Filters)) =>
    setViews((v) => ({ ...v, [v.view]: { ...(typeof update === 'function' ? update(v[v.view]) : update), for_you: v.view === 'forYou' } }))
  const setView = (forYouView: boolean) => setViews((v) => ({ ...v, view: forYouView ? 'forYou' : 'all' }))
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const wide = useWideScreen()
  useEffect(() => {
    if (!hasResume) return
    try {
      if (localStorage.getItem(MATCH_SORT_MIGRATION)) return
      localStorage.setItem(MATCH_SORT_MIGRATION, '1')
    } catch {
      return
    }
    setViews((v) => ({ ...v, forYou: { ...v.forYou, sort: 'match' } }))  // For you only; All jobs keeps its sort
  }, [hasResume])

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300)
    return () => clearTimeout(t)
  }, [search])
  useEffect(() => {
    setOffset(0)
    try {
      localStorage.setItem(VIEWS_KEY, JSON.stringify(views))
    } catch {
      // storage unavailable: filters just won't persist
    }
  }, [views])
  useEffect(() => {
    setOffset(0)
    setSkip([])
    try {
      if (q) localStorage.setItem(LAST_QUERY_KEY, q)
    } catch {
      // storage unavailable
    }
  }, [q])

  // The API query for a set of filters (the applied ones, or a staged draft being prefetched).
  const queryFor = (fl: Filters, page: number): JobQuery => {
    // About 1 in 5 postings never states a level; picking "Entry" shouldn't silently drop them.
    const { include_unstated, for_you: forYouView, ...rest } = fl
    const levels = rest.exp_levels.length && include_unstated ? [...rest.exp_levels, 'unknown' as const] : rest.exp_levels
    // A search includes related roles (exact-title matches always rank first); "Exact title" turns them
    // off, and each related role can be switched off on its own (the chips under the count).
    const exactOnly = !fl.related
    // "For you": titles matching the roles in Settings. A search looks through every job instead.
    return { ...rest, related: !exactOnly, skip: exactOnly ? [] : skip, exp_levels: levels, q, alert_roles_only: forYouView && !q, limit: PAGE, offset: page }
  }
  const forYou = filters.for_you && !q
  const exact = !filters.related
  const query = queryFor(filters, offset)
  const jobs = useQuery({ queryKey: ['jobs', query], queryFn: () => api.jobs(query), placeholderData: keepPreviousData, refetchInterval: 60_000 })
  const set = (patch: Partial<Filters>) => setFilters((fl) => ({ ...fl, ...patch }))

  // Filter panels stage their changes in `draft` until Apply, so a filter takes effect only when the user says
  // so. While they choose, the draft's first page loads in the background (and lands in the backend's response
  // cache), so Apply usually shows the new results at once.
  const [draft, setDraft] = useState<Filters | null>(null)
  const f = draft ?? filters  // what the open panel shows
  const stage = (patch: Partial<Filters>) => setDraft((d) => ({ ...(d ?? filters), ...patch }))
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(filters)
  const [appliedAt, setAppliedAt] = useState(0)
  const applyDraft = () => {
    if (draft && dirty) {
      setFilters(draft)
      setAppliedAt(Date.now())
    }
    setDraft(null)
  }
  const cancelDraft = () => setDraft(null)
  const staging = { onApply: applyDraft, onCancel: cancelDraft, dirty }
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!draft || !dirty) return
    const t = setTimeout(() => {
      const dq = queryFor(draft, 0)
      queryClient.prefetchQuery({ queryKey: ['jobs', dq], queryFn: () => api.jobs(dq), staleTime: 30_000 })
    }, 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- queryFor reads q and skip, which the draft key covers
  }, [draft, dirty, q, skip])
  // "Applying filters…" until the new results arrive, then "✓ Filters applied" for a few seconds.
  const [showApplied, setShowApplied] = useState(false)
  useEffect(() => {
    if (!appliedAt || jobs.isFetching) return
    setShowApplied(true)
    const t = setTimeout(() => setShowApplied(false), 3000)
    return () => clearTimeout(t)
  }, [appliedAt, jobs.isFetching])
  const applying = appliedAt > 0 && jobs.isFetching && jobs.isPlaceholderData
  const toggleLevel = (level: ExpLevel) =>
    stage({ exp_levels: f.exp_levels.includes(level) ? f.exp_levels.filter((l) => l !== level) : [...f.exp_levels, level] })
  const related = jobs.data?.related_terms ?? []
  // How many of the results have the exact title, shown next to the total while related roles are on.
  const exactCount = useQuery({
    queryKey: ['jobs', 'exact-count', { ...query, limit: 1, offset: 0 }],
    queryFn: () => api.jobs({ ...query, related: false, skip: [], limit: 1, offset: 0 }),
    enabled: !!q && !exact,  // in parallel with the results, not after them
    placeholderData: keepPreviousData,
  })
  // Jobs without a saved score (searches, All jobs) get one a moment after the page shows, so the list never
  // waits for the resume scoring.
  const unscored = hasResume ? (jobs.data?.items ?? []).filter((j) => !j.match).map((j) => j.id) : []
  const lateScores = useQuery({
    queryKey: ['matches', unscored],
    queryFn: () => api.matches(unscored),
    enabled: unscored.length > 0,
    staleTime: 5 * 60_000,
  })
  const items = jobs.data?.items.map((j) => (j.match || !lateScores.data?.[j.id] ? j : { ...j, match: lateScores.data[j.id] }))
  // Next/Previous: show the new page from its top, not from the pager at the bottom.
  const resultsTop = useRef<HTMLDivElement>(null)
  const goToPage = (next: number) => {
    setOffset(next)
    resultsTop.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // Two-pane view: keep a job open on the right, defaulting to the first result. A job opened from outside the
  // list (the top-matches strip) stays open: it's usually not on this page, and it used to be replaced at once by
  // the first result (2026-11-12). A new search or filter goes back to the list's first job.
  const [openedOutside, setOpenedOutside] = useState(false)
  const listKey = JSON.stringify(query)
  useEffect(() => {
    setOpenedOutside(false)
  }, [listKey])
  useEffect(() => {
    if (wide && items?.length && !openedOutside && !items.some((j) => j.id === selected)) setSelected(items[0].id)
  }, [wide, items, selected, openedOutside])

  const postedLabel = POSTED_WINDOWS.find((w) => w.hours === filters.posted_within_hours)?.label
  const foundLabel = FOUND_WINDOWS.find((w) => w.hours === filters.since_hours)?.label
  const places = useLocationFacets(filters.countries, filters.states)
  const oneCountry = filters.countries.length === 1 ? filters.countries[0] : undefined
  const expLabel = filters.exp_levels.length
    ? filters.exp_levels.map((l) => EXP_LABELS[l].split(' ')[0]).join(', ')
    : filters.max_years !== undefined ? `≤ ${filters.max_years} yrs` : 'Experience'
  const moreActive = filters.show_reposts || filters.tracked !== 'any' || filters.hide_consultancies

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
                  onClick={() => setView(v)}
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
          <FilterPill {...staging} label={postedLabel !== 'Any time' ? `Posted: ${postedLabel}` : 'Date posted'} active={filters.posted_within_hours !== undefined} onClear={() => set({ posted_within_hours: undefined })}>
            <p className="mb-1 px-2 text-xs text-slate-500">When the company posted it</p>
            {POSTED_WINDOWS.map((w) => (
              <Choice key={w.label} selected={f.posted_within_hours === w.hours} onClick={() => stage({ posted_within_hours: w.hours })}>
                {w.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill {...staging} label={foundLabel !== 'Any time' ? `Found: ${foundLabel}` : 'Found by us'} active={filters.since_hours !== undefined} onClear={() => set({ since_hours: undefined })}>
            <p className="mb-1 px-2 text-xs text-slate-500">When FreshJobs first saw it (minutes after posting)</p>
            {FOUND_WINDOWS.map((w) => (
              <Choice key={w.label} selected={f.since_hours === w.hours} onClick={() => stage({ since_hours: w.hours })}>
                {w.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill {...staging}
            label={expLabel}
            active={filters.exp_levels.length > 0 || filters.max_years !== undefined}
            onClear={() => set({ exp_levels: [], max_years: undefined, include_unstated: true })}
          >
            <div className="space-y-2">
              {EXP_LEVELS.map((level) => (
                <Toggle key={level} label={EXP_LABELS[level]} checked={f.exp_levels.includes(level)} onChange={() => toggleLevel(level)} />
              ))}
              {f.exp_levels.length > 0 && (
                <Toggle label="Also jobs that don't say" checked={f.include_unstated} onChange={(v) => stage({ include_unstated: v })} />
              )}
              <select
                value={f.max_years ?? ''}
                onChange={(e) => stage({ max_years: e.target.value === '' ? undefined : Number(e.target.value) })}
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

          <FilterPill {...staging} label={pillLabel('Country', filters.countries)} active={filters.countries.length > 0} onClear={() => set({ countries: [], states: [], cities: [] })}>
            <FacetList
              options={places.data?.countries}
              loading={places.isLoading}
              selected={f.countries}
              onChange={(countries) => stage({ countries, states: [], cities: [] })}
              placeholder="Search countries"
            />
          </FilterPill>

          <FilterPill {...staging} label={pillLabel('State', filters.states)} active={filters.states.length > 0} onClear={() => set({ states: [], cities: [] })}>
            {oneCountry ? (
              <FacetList
                options={places.data?.states}
                loading={places.isFetching}
                selected={f.states}
                onChange={(states) => stage({ states, cities: [] })}
                placeholder={oneCountry === 'United States' ? 'Search states (CA, NY, TX…)' : 'Search states / provinces'}
              />
            ) : (
              <p className="max-w-56 text-sm text-slate-500">Pick one country first, then choose its states.</p>
            )}
          </FilterPill>

          <FilterPill {...staging} label={pillLabel('City', filters.cities)} active={filters.cities.length > 0} onClear={() => set({ cities: [] })}>
            {oneCountry ? (
              <FacetList
                options={places.data?.cities}
                loading={places.isFetching}
                selected={f.cities}
                onChange={(cities) => stage({ cities })}
                placeholder={f.states.length === 1 ? `Search cities in ${f.states[0]}` : 'Search cities'}
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

          <FilterPill {...staging}
            label={filters.sponsor_likely === 'likely' ? 'Likely sponsors' : filters.sponsor_likely === 'possible' ? 'Likely or may sponsor' : filters.sponsors_only ? 'H-1B sponsors' : 'Visa'}
            active={filters.sponsor_likely !== 'any' || filters.sponsors_only || filters.everify_only || filters.hide_no_sponsor}
            onClear={() => set({ sponsor_likely: 'any', sponsors_only: false, everify_only: false, hide_no_sponsor: false })}
          >
            <div className="space-y-2">
              <p className="px-1 text-[11px] font-bold uppercase tracking-wide text-slate-400">Will they sponsor?</p>
              {SPONSOR_CHOICES.map((c) => (
                <Choice key={c.value} selected={f.sponsor_likely === c.value} onClick={() => stage({ sponsor_likely: c.value })}>
                  <span>
                    {c.label}
                    <span className="block text-xs font-normal text-slate-500">{c.hint}</span>
                  </span>
                </Choice>
              ))}
              <div className="my-1 border-t border-slate-100" />
              <Toggle label="Only companies that filed H-1Bs" checked={f.sponsors_only} onChange={(v) => stage({ sponsors_only: v })} />
              <Toggle
                label="Only E-Verify employers (needed for STEM OPT)"
                checked={f.everify_only}
                onChange={(v) => stage({ everify_only: v })}
              />
              <Toggle
                label="Hide jobs that rule out sponsorship or OPT/CPT"
                checked={f.hide_no_sponsor}
                onChange={(v) => stage({ hide_no_sponsor: v })}
              />
            </div>
          </FilterPill>

          <FilterPill {...staging}
            label={`Sort: ${SORTS.find((s) => s.value === filters.sort)?.label}`}
            active={filters.sort !== DEFAULT_FILTERS.sort}
            onClear={() => set({ sort: DEFAULT_FILTERS.sort })}
          >
            {SORTS.map((s) => (
              <Choice key={s.value} selected={f.sort === s.value} onClick={() => stage({ sort: s.value })}>
                {s.label}
              </Choice>
            ))}
          </FilterPill>

          <FilterPill {...staging} label="More" active={moreActive} onClear={() => set({ show_reposts: false, repeat_kinds: REPEAT_KINDS, tracked: 'any', hide_consultancies: false })}>
            <div className="space-y-2">
              <Toggle
                label="Hide consultancies & staffing firms"
                checked={f.hide_consultancies}
                onChange={(v) => stage({ hide_consultancies: v })}
              />
              <Toggle
                label="Show repeated jobs (hidden by default)"
                checked={f.show_reposts}
                onChange={(v) => stage({ show_reposts: v, repeat_kinds: REPEAT_KINDS })}
              />
              {f.show_reposts && (
                <div className="ml-5 space-y-1 border-l border-slate-200 pl-3">
                  {REPEAT_KINDS.map((k) => (
                    <label key={k} title={REPEAT_LABELS[k].hint} className="flex cursor-pointer select-none items-start gap-1.5 text-sm text-slate-700">
                      <input
                        type="checkbox"
                        className="mt-1 accent-brand-600"
                        checked={f.repeat_kinds.includes(k)}
                        onChange={(e) => {
                          const kinds = e.target.checked ? [...f.repeat_kinds, k] : f.repeat_kinds.filter((x) => x !== k)
                          stage(kinds.length ? { repeat_kinds: kinds } : { show_reposts: false, repeat_kinds: REPEAT_KINDS })
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
                value={f.tracked}
                onChange={(e) => stage({ tracked: e.target.value as Filters['tracked'] })}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
              >
                <option value="any">Tracked and untracked</option>
                <option value="untracked">Hide jobs I've tracked</option>
                <option value="tracked">Only jobs I've tracked</option>
              </select>
            </div>
          </FilterPill>

          <button onClick={() => setFilters(DEFAULT_FILTERS)} title="Resets this view's filters; the other view keeps its own" className="ml-auto rounded-full px-3 py-1.5 text-sm font-medium text-slate-500 hover:bg-rose-50 hover:text-rose-600">
            ↺ Reset all
          </button>
        </div>
      </section>

      <FreshnessStrip base={{ ...query, limit: 1, offset: 0 }} active={filters.posted_within_hours} onPick={(hours) => set({ posted_within_hours: hours })} />

      {forYou && hasResume && <TopMatches base={{ ...query, limit: 8, offset: 0 }} onOpen={(id) => { setSelected(id); setOpenedOutside(true) }} />}
      {forYou && profile.data && !hasResume && (
        <button
          onClick={onOpenProfile}
          className="flex w-full items-center gap-4 rounded-3xl bg-gradient-to-r from-brand-600 to-violet-600 p-4 text-left text-white shadow-lg shadow-brand-600/20 transition hover:brightness-110"
        >
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-white/15 text-2xl" aria-hidden>🎯</span>
          <span className="min-w-0 flex-1">
            <span className="block font-display font-bold">See how well you match every job</span>
            <span className="block text-sm text-brand-100">Upload your resume: skills, experience, education and visa fit, scored for each job.</span>
          </span>
          <span className="shrink-0 rounded-full bg-white px-4 py-1.5 text-sm font-bold text-brand-700">Upload resume →</span>
        </button>
      )}

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 px-1 text-sm text-slate-500">
        <span>
          {applying && (
            <span role="status" className="mr-2 inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-semibold text-brand-700 ring-1 ring-brand-200">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" aria-hidden />
              Applying filters…
            </span>
          )}
          {!applying && showApplied && (
            <span role="status" className="mr-2 inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200">
              ✓ Filters applied
            </span>
          )}
          {jobs.data ? (
            <>
              <b className="font-display text-base font-extrabold text-slate-900">{jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}</b> jobs{q && <> for “{q}”</>}
              {q && exact && <> · exact title only</>}
              {q && !exact && related.length > 0 && exactCount.data && (
                <>
                  {' '}
                  {exactCount.data.total_capped && jobs.data.total_capped ? (
                    <>· exact-title matches first, then related roles</>
                  ) : (
                    <>
                      · <b className="text-slate-800">{exactCount.data.total.toLocaleString()}{exactCount.data.total_capped && '+'}</b> with that exact title, shown first; the rest are related roles
                    </>
                  )}
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

      <div ref={resultsTop} className="grid scroll-mt-24 gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="min-w-0 space-y-3">
          <ul className="space-y-2.5">
            {!items && jobs.isLoading && Array.from({ length: 6 }, (_, i) => <CardSkeleton key={i} />)}
            {items && groupSameRole(items).map(({ job, others }, i) => (
              <JobCard key={job.id} index={i} job={job} others={others} selectedId={wide ? selected : null}
                       onPick={(id) => { setSelected(id); setOpenedOutside(false) }} />
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
              <button disabled={offset === 0} onClick={() => goToPage(offset - PAGE)} className="rounded-full bg-white px-4 py-1.5 font-medium shadow-sm ring-1 ring-slate-200 hover:ring-brand-300 disabled:opacity-40">
                Previous
              </button>
              <span className="text-slate-500">
                {offset + 1}–{Math.min(offset + PAGE, jobs.data.total)} of {jobs.data.total.toLocaleString()}{jobs.data.total_capped && '+'}
              </span>
              <button
                disabled={offset + PAGE >= jobs.data.total}
                onClick={() => goToPage(offset + PAGE)}
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
