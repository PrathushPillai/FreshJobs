export type ExpLevel = 'entry' | 'mid' | 'senior' | 'unknown'

export interface Place {
  city: string | null
  state: string | null
  country: string | null
}

export interface Job {
  id: number
  company: string
  company_id: number
  source: string
  title: string
  location: string
  places: Place[]
  remote: boolean
  url: string
  department: string
  posted_at: string | null
  first_seen_at: string
  closed_at: string | null
  is_repost: boolean
  repost_of_id: number | null
  no_sponsor: boolean
  seniority: string
  min_years: number | null
  exp_level: ExpLevel
  baseline: boolean
  h1b_filings: number | null
  application: { id: number; status: AppStatus } | null
}

export interface JobDetail extends Job {
  description: string
  events: { kind: string; at: string; detail: string }[]
  sponsor: { filings: number; employers: string[]; top_titles: string[]; source: string } | null
}

export interface Company {
  id: number
  name: string
  ats: string
  slug: string
  enabled: boolean
  last_polled_at: string | null
  last_status: string | null
  open_jobs: number
}

export interface Prefs {
  include_keywords: string[]
  exclude_keywords: string[]
  email_alerts: boolean
  hide_no_sponsor: boolean
  exclude_senior: boolean
}

export interface Status {
  polling: boolean
  slow_polling: boolean
  poll_minutes: number
  last_poll: { started_at: string; finished_at: string; new_jobs: number; matches: number; errors: number; discovered?: string[] } | null
  email_configured: boolean
  hunter_configured: boolean
  adzuna_configured: boolean
  companies: number
  open_jobs: number
  new_last_24h: number
  tracked: number
  h1b_sponsors: number
  jsearch_configured: boolean
}

export const APP_STATUSES = ['saved', 'applied', 'assessment', 'interviewing', 'offer', 'rejected', 'withdrawn', 'ghosted'] as const
export type AppStatus = (typeof APP_STATUSES)[number]

export interface Application {
  id: number
  job_id: number | null
  company: string
  title: string
  url: string
  location: string
  status: AppStatus
  applied_at: string | null
  notes: string
  contact: string
  created_at: string
  updated_at: string
  job_closed: boolean | null
}

export interface Facet {
  name: string
  count: number
}

export interface RecruiterInfo {
  company: string
  links: { label: string; url: string }[]
  hunter_configured: boolean
  hunter: {
    error?: string
    domain?: string | null
    pattern?: string | null
    organization?: string | null
    cached?: boolean
    contacts?: { name: string | null; email: string; position: string | null; department: string | null; linkedin: string | null; confidence: number | null }[]
  } | null
}

export interface JobQuery {
  q?: string
  related?: boolean
  sort?: 'newest' | 'posted' | 'company' | 'sponsor'
  countries?: string[]
  states?: string[]
  cities?: string[]
  exp_levels?: ExpLevel[]
  max_years?: number
  remote_only?: boolean
  sponsors_only?: boolean
  hide_no_sponsor?: boolean
  show_reposts?: boolean
  alert_roles_only?: boolean
  tracked?: 'any' | 'tracked' | 'untracked'
  since_hours?: number
  status?: 'open' | 'closed' | 'all'
  limit?: number
  offset?: number
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `${res.status} ${res.statusText}`)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

function toParams(query: object): URLSearchParams {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === '' || v === null) continue
    if (Array.isArray(v)) v.forEach((item) => params.append(k, String(item)))
    else params.set(k, String(v))
  }
  return params
}

export const api = {
  jobs: (query: JobQuery) => request<{ total: number; related_terms: string[]; items: Job[] }>(`/api/jobs?${toParams(query)}`),
  job: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  recruiters: (id: number, lookup = false, refresh = false) =>
    request<RecruiterInfo>(`/api/jobs/${id}/recruiters?${toParams({ lookup, refresh })}`),
  locationFacets: (country?: string, state?: string) =>
    request<{ countries: Facet[]; states: Facet[]; cities: Facet[] }>(`/api/facets/locations?${toParams({ country, state })}`),
  applications: () => request<Application[]>('/api/applications'),
  track: (body: Partial<Application> & { job_id?: number }) =>
    request<Application>('/api/applications', { method: 'POST', body: JSON.stringify(body) }),
  updateApplication: (id: number, body: Partial<Pick<Application, 'status' | 'notes' | 'contact' | 'applied_at'>>) =>
    request<Application>(`/api/applications/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteApplication: (id: number) => request<void>(`/api/applications/${id}`, { method: 'DELETE' }),
  companies: () => request<Company[]>('/api/companies'),
  addCompany: (body: { name: string; ats: string; slug: string }) =>
    request<Company>('/api/companies', { method: 'POST', body: JSON.stringify(body) }),
  addCompanyFromUrl: (url: string) =>
    request<{ added: boolean; company: Company }>('/api/companies/from-url', { method: 'POST', body: JSON.stringify({ url }) }),
  setCompanyEnabled: (id: number, enabled: boolean) =>
    request<Company>(`/api/companies/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  prefs: () => request<Prefs>('/api/prefs'),
  savePrefs: (prefs: Prefs) => request<Prefs>('/api/prefs', { method: 'PUT', body: JSON.stringify(prefs) }),
  status: () => request<Status>('/api/status'),
  poll: () => request<{ started: boolean }>('/api/poll', { method: 'POST' }),
}

export function timeAgo(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export const STATUS_STYLES: Record<AppStatus, string> = {
  saved: 'bg-slate-100 text-slate-700',
  applied: 'bg-sky-100 text-sky-800',
  assessment: 'bg-violet-100 text-violet-800',
  interviewing: 'bg-amber-100 text-amber-800',
  offer: 'bg-emerald-100 text-emerald-800',
  rejected: 'bg-red-100 text-red-700',
  withdrawn: 'bg-slate-100 text-slate-500',
  ghosted: 'bg-slate-200 text-slate-600',
}

export const SOURCE_LABELS: Record<string, string> = {
  greenhouse: 'Greenhouse',
  lever: 'Lever',
  ashby: 'Ashby',
  workday: 'Workday',
  smartrecruiters: 'SmartRecruiters',
  workable: 'Workable',
  amazon: 'Amazon',
  microsoft: 'Microsoft',
  google: 'Google',
  apple: 'Apple',
  oracle_hcm: 'Oracle HCM',
  icims: 'iCIMS',
  eightfold: 'Eightfold',
  successfactors: 'SuccessFactors',
  themuse: 'The Muse',
  remotive: 'Remotive',
  remoteok: 'RemoteOK',
  adzuna: 'Adzuna',
  jsearch: 'Google Jobs',
}
