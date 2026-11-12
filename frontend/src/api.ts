export type ExpLevel = 'entry' | 'mid' | 'senior' | 'unknown'

export interface Place {
  city: string | null
  state: string | null
  country: string | null
}

export type SponsorLikely = 'likely' | 'possible' | 'unlikely' | 'ruled_out'

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
  listed_at: string | null
  listed_until: string | null
  checked_at: string | null
  closed_at: string | null
  is_repost: boolean
  repost_of_id: number | null
  repost_kind: RepeatKind | null
  no_sponsor: boolean
  /** What the description says about F-1 students on OPT/CPT/STEM OPT. */
  student_visa: 'welcome' | 'excluded' | null
  /** The description says the employer uses E-Verify (needed to hire on STEM OPT). */
  everify_text: boolean
  /** The posting says it sponsors visas ("We do sponsor visas!"). */
  sponsors_text: boolean
  /** The employer in USCIS's E-Verify list: enrolled, every account ended, or not found. */
  everify: 'yes' | 'ended' | null
  /** The employer's website, for its logo; null means show initials. */
  logo_domain: string | null
  /** How well the uploaded resume fits this job; null without a resume. */
  match: MatchBrief | null
  seniority: string
  min_years: number | null
  exp_level: ExpLevel
  baseline: boolean
  h1b_filings: number | null
  /** A consultancy or staffing firm (backend h1b.job_employer_kind): a badge, and the opt-in hide filter. */
  employer_kind: 'consultancy' | 'staffing' | '' | null
  /** How likely the employer is to sponsor (backend sponsorship.sponsor_likelihood). */
  sponsor_likely: SponsorLikely
  application: { id: number; status: AppStatus } | null
}

/** One occupation (SOC code) an employer filed H-1B LCAs for, with the offered yearly wages. */
export interface H1BRole {
  soc_code: string
  soc_title: string
  filings: number
  median_wage: number | null
  low_wage: number | null
  high_wage: number | null
  top_titles: string[]
  top_cities: string[]
}

export interface H1BHistory {
  total: number
  /** The occupation that fits this job: by similar LCA titles ("title") or the title's usual occupation. */
  match: (H1BRole & { matched_by: 'title' | 'occupation' }) | null
  roles: H1BRole[]
}

/** "$148k" */
export function money(n: number | null): string {
  if (n === null) return '-'
  return n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : `$${Math.round(n / 1000)}k`
}

/** Why a job is hidden as a repeat: same job re-posted, the same job on another site, or an old job re-listed. */
export type RepeatKind = 'repost' | 'duplicate' | 'resurfaced'
export const REPEAT_KINDS: RepeatKind[] = ['repost', 'duplicate', 'resurfaced']
export const REPEAT_LABELS: Record<RepeatKind, { label: string; hint: string }> = {
  repost: { label: 'Repost', hint: 'The same job was posted before by this company' },
  duplicate: { label: 'Duplicate', hint: 'The same job is also listed, still open, on another site we track' },
  resurfaced: { label: 'Resurfaced', hint: 'Newly listed, but the company dates it more than a week ago: an old job re-listed' },
}

export interface JobDetail extends Job {
  description: string
  repost_of: { id: number; title: string; company: string; url: string; first_seen_at: string; closed_at: string | null } | null
  h1b_roles: H1BHistory | null
  match: MatchDetail | null
  /** LinkedIn post search: "hiring" + this role + this company, past month (a link only). */
  hiring_posts_url: string
  uscis: {
    years: { fiscal_year: number; new_approvals: number; new_denials: number; approvals: number; denials: number }[]
    names: string[]
  } | null
  everify_info: { status: 'yes' | 'ended'; since: string | null; ended: string | null; name: string; workforce: string; accounts: number } | null
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

/** A job's resume match as cards show it (app/matching.py). */
export interface MatchBrief {
  score: number
  matched: string[]
  missing: string[]
  skills_asked: number
  notes: string[]
}

/** The full match, for the job panel: every part out of 100 and every matched and missing skill. */
export interface MatchDetail extends MatchBrief {
  parts: { skills: number; fit: number; role: number; experience: number; education: number }
}

export interface Profile {
  has_resume: boolean
  filename?: string
  uploaded_at?: string
  skills?: string[]
  years?: number | null
  degree?: '' | 'bachelors' | 'masters' | 'phd'
  target_roles?: string[]
  suggested_roles?: string[]
  needs_sponsorship?: boolean
  visa_status?: string
  opt_end?: string | null
  locations?: string[]
  resume_preview?: string
  your_roles: string[]
  scoring: { running: boolean; scored: number; total: number }
  scored?: number
  version?: number
  /** Set when the file's words came out run together (some PDFs): what to upload instead. */
  text_warning?: string | null
  /** Every skill the matcher knows, for the skill picker. */
  vocabulary: string[]
}

export type ProfileEdit = Partial<Pick<Profile, 'skills' | 'years' | 'degree' | 'target_roles' | 'needs_sponsorship' | 'visa_status' | 'opt_end' | 'locations'>>

/** Match colour by score: strong, good, fair, weak. */
export function matchTone(score: number): 'strong' | 'good' | 'fair' | 'weak' {
  return score >= 75 ? 'strong' : score >= 60 ? 'good' : score >= 45 ? 'fair' : 'weak'
}

export interface User {
  id: number
  email: string
  name: string
  is_admin: boolean
  email_verified: boolean
  /** Set while the admin views the portal as this user (read-only). */
  viewing_as?: { admin_email: string; until?: string | null }
}

export type AlertFrequency = 'instant' | 'hourly' | 'daily'

export interface AlertSettings {
  email: string
  email_verified: boolean
  email_mode: 'off' | 'test' | 'on'
  email_alerts: boolean
  alert_frequency: AlertFrequency
  alert_min_score: number | null
  alert_daily_cap: number
  hide_no_sponsor: boolean
  exclude_senior: boolean
  roles: string[]
  last_alert_at: string | null
  confirmation_sent?: boolean
}

export interface AdminEvent {
  id: number
  at: string
  kind: string
  detail: string
  ip: string
  user_id: number | null
  user_email: string | null
  actor_email: string | null
}

export interface AdminOverview {
  users: number
  confirmed: number
  disabled: number
  alerts_on: number
  with_resume: number
  signups_7d: number
  active_24h: number
  emails_24h: number
  emails_failed_24h: number
  email_daily_limit: number
  email_mode: 'off' | 'test' | 'on'
  email_sender: string
  alerts_24h: number
  failed_logins_24h: number
  recruiter_lookups_24h: number
  last_poll: { finished_at: string | null; sources: number | null; errors: number | null; new_jobs: number | null } | null
  problems: AdminEvent[]
}

export interface AdminUser {
  id: number
  email: string
  name: string
  is_admin: boolean
  disabled: boolean
  email_verified: boolean
  created_at: string
  last_login_at: string | null
  last_activity_at: string | null
  events_7d: number
  alerts_on: boolean
  alert_frequency: AlertFrequency | null
  roles: string[]
  has_resume: boolean
  tracked: number
}

export interface AdminUserView {
  account: { id: number; email: string; name: string; is_admin: boolean; disabled: boolean; email_verified: boolean; created_at: string; last_login_at: string | null; sessions: number }
  roles: string[]
  excluded: string[]
  alerts: {
    on: boolean; frequency: AlertFrequency; min_score: number | null; daily_cap: number; sent_today: number
    last_alert_at: string | null; due_now: boolean; hide_no_sponsor: boolean; exclude_senior: boolean
    next_alert: { title: string; company: string; first_seen_at: string }[]; next_alert_count: number
  }
  resume: {
    uploaded: boolean; filename: string; uploaded_at: string | null; skills: string[]; years: number | null; degree: string
    target_roles: string[]; visa_status: string; needs_sponsorship: boolean | null; scored: number
    top_matches: { job_id: number; title: string; score: number }[]
  }
  tracker: { company: string; title: string; status: string; applied_at: string | null; created_at: string }[]
  activity: AdminEvent[]
}

export interface SupportItem {
  id: number
  at: string
  user_id: number | null
  email: string
  name: string
  topic: string
  topic_label: string
  message: string
  page: string
  emailed: boolean
  status: 'open' | 'done'
}

export type SupportTopic = 'question' | 'bug' | 'idea' | 'account' | 'other'

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
  /** "test" means a local test mail server: alerts don't reach a real inbox. */
  email_mode?: 'off' | 'test' | 'on'
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
  also?: string[]
  skip?: string[]
  sort?: 'newest' | 'posted' | 'company' | 'sponsor' | 'match'
  countries?: string[]
  states?: string[]
  cities?: string[]
  exp_levels?: ExpLevel[]
  max_years?: number
  remote_only?: boolean
  sponsors_only?: boolean
  /** 'likely': likely sponsors only; 'possible': likely or possible; 'any': no filter. */
  sponsor_likely?: 'any' | 'likely' | 'possible'
  everify_only?: boolean
  /** Leave out consultancies and staffing firms (off by default: they get a badge instead). */
  hide_consultancies?: boolean
  /** Instead of a page: how many matches were posted in the last 1, 6 and 24 hours (JobPage.fresh). */
  fresh_counts?: boolean
  hide_no_sponsor?: boolean
  show_reposts?: boolean
  repeat_kinds?: RepeatKind[]
  alert_roles_only?: boolean
  tracked?: 'any' | 'tracked' | 'untracked'
  since_hours?: number
  posted_within_hours?: number
  status?: 'open' | 'closed' | 'all'
  limit?: number
  offset?: number
}

/** The backend isn't answering yet (still starting, or stopped): the dev server's proxy replies 500 with
 * an empty body, or the request fails outright. The page shows "starting up" and retries. */
export class BackendDown extends Error {
  constructor() {
    super('FreshJobs is starting up. This can take a few minutes after a restart; the page loads by itself.')
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
  } catch {
    throw new BackendDown()
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    // The session ended (logged out elsewhere, expired): App shows the login screen.
    window.dispatchEvent(new Event('freshjobs:logged-out'))
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    if (res.status >= 500 && !text.trim()) throw new BackendDown()
    let body: { detail?: unknown } = {}
    try {
      body = JSON.parse(text)
    } catch {
      /* not JSON */
    }
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
  /** Scores for jobs a list showed without one (scored on the server now). */
  matches: (ids: number[]) => request<Record<string, MatchBrief>>(`/api/matches?${toParams({ ids })}`),
  jobs: (query: JobQuery) => request<{ total: number; total_capped: boolean; related_terms: string[]; items: Job[]; fresh?: { '1': number; '6': number; '24': number } | null }>(`/api/jobs?${toParams(query)}`),
  job: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  verify: (id: number) =>
    request<{ open: boolean | null; detail: string; checked_at: string; closed_at: string | null }>(`/api/jobs/${id}/verify`, { method: 'POST' }),
  /** Fetch a job's description from its source when it was saved without one. */
  describe: (id: number) => request<{ description: string | null; fetched: boolean }>(`/api/jobs/${id}/describe`, { method: 'POST' }),
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
  companies: (q = '', limit = 200) => request<Company[]>(`/api/companies?${toParams({ q, limit })}`),
  addCompany: (body: { name: string; ats: string; slug: string }) =>
    request<Company>('/api/companies', { method: 'POST', body: JSON.stringify(body) }),
  addCompanyFromUrl: (url: string) =>
    request<{ added: boolean; company: Company }>('/api/companies/from-url', { method: 'POST', body: JSON.stringify({ url }) }),
  setCompanyEnabled: (id: number, enabled: boolean) =>
    request<Company>(`/api/companies/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  prefs: () => request<Prefs>('/api/prefs'),
  roleSuggestions: (roles: string[]) => request<{ role: string; open_jobs: number }[]>(`/api/roles/suggestions?${toParams({ roles })}`),
  profile: () => request<Profile>('/api/profile'),
  uploadResume: (filename: string, data_base64: string) =>
    request<Profile>('/api/profile/resume', { method: 'POST', body: JSON.stringify({ filename, data_base64 }) }),
  updateProfile: (edit: ProfileEdit) => request<Profile>('/api/profile', { method: 'PUT', body: JSON.stringify(edit) }),
  deleteResume: () => request<Profile>('/api/profile/resume', { method: 'DELETE' }),
  savePrefs: (prefs: Prefs) => request<Prefs>('/api/prefs', { method: 'PUT', body: JSON.stringify(prefs) }),
  status: () => request<Status>('/api/status'),
  poll: () => request<{ started: boolean }>('/api/poll', { method: 'POST' }),
  me: () => request<User>('/api/auth/me'),
  signup: (name: string, email: string, password: string) =>
    request<User>('/api/auth/signup', { method: 'POST', body: JSON.stringify({ name, email, password }) }),
  login: (email: string, password: string) =>
    request<User>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
  verifyEmail: (token: string) => request<User>('/api/auth/verify', { method: 'POST', body: JSON.stringify({ token }) }),
  resendVerification: () => request<{ sent: boolean; already_verified?: boolean }>('/api/auth/resend-verification', { method: 'POST' }),
  forgotPassword: (email: string) => request<{ ok: boolean }>('/api/auth/forgot', { method: 'POST', body: JSON.stringify({ email }) }),
  resetPassword: (token: string, password: string) =>
    request<User>('/api/auth/reset', { method: 'POST', body: JSON.stringify({ token, password }) }),
  changePassword: (current: string, next: string) =>
    request<{ ok: boolean }>('/api/auth/password', { method: 'POST', body: JSON.stringify({ current, new: next }) }),
  deleteAccount: (password: string) => request<{ ok: boolean }>('/api/auth/account', { method: 'DELETE', body: JSON.stringify({ password }) }),
  sendSupport: (body: { topic: SupportTopic; message: string; email?: string; name?: string; page?: string }) =>
    request<{ ok: boolean; id: number }>('/api/support', { method: 'POST', body: JSON.stringify(body) }),
  adminOverview: () => request<AdminOverview>('/api/admin/overview'),
  adminUsers: (q: string) => request<AdminUser[]>(`/api/admin/users?${toParams({ q })}`),
  adminActivity: (p: { user_id?: number; kind?: string; problems?: boolean; limit?: number }) =>
    request<AdminEvent[]>(`/api/admin/activity?${toParams(p)}`),
  adminUser: (id: number) => request<AdminUserView>(`/api/admin/users/${id}`),
  adminDisable: (id: number, disabled: boolean) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}/disable`, { method: 'POST', body: JSON.stringify({ disabled }) }),
  adminAlertsOff: (id: number) => request<{ ok: boolean }>(`/api/admin/users/${id}/alerts-off`, { method: 'POST' }),
  adminResend: (id: number) => request<{ sent: boolean; already_verified?: boolean }>(`/api/admin/users/${id}/resend-verification`, { method: 'POST' }),
  startViewAs: (id: number) => request<User>(`/api/admin/users/${id}/view-as`, { method: 'POST' }),
  stopViewAs: () => request<User>('/api/admin/view-as/stop', { method: 'POST' }),
  adminDelete: (id: number) => request<{ ok: boolean }>(`/api/admin/users/${id}`, { method: 'DELETE' }),
  adminSupport: () => request<SupportItem[]>('/api/admin/support'),
  adminSupportStatus: (id: number, status: 'open' | 'done') =>
    request<{ ok: boolean }>(`/api/admin/support/${id}`, { method: 'POST', body: JSON.stringify({ status }) }),
  alertSettings: () => request<AlertSettings>('/api/alerts/settings'),
  saveAlertSettings: (patch: Partial<Pick<AlertSettings, 'email_alerts' | 'alert_frequency' | 'alert_min_score' | 'alert_daily_cap' | 'hide_no_sponsor' | 'exclude_senior'>>) =>
    request<AlertSettings>('/api/alerts/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  testEmail: () => request<{ sent: boolean; detail: string; mode: string }>('/api/alerts/test', { method: 'POST' }),
}

export function timeAgo(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} d ago`
}

/** When a job went live. A window ("in the last 6 h", "20 min–6 h ago") when we found it after a gap in
 * checking (the server was off) and the company gives no exact time, else a single time. */
export function postedText(listed: string, until: string | null): string {
  if (!until) return timeAgo(listed)
  const span = (iso: string) => timeAgo(iso).replace(/ ago$/, '')
  const untilMinutes = (Date.now() - new Date(until).getTime()) / 60_000
  return untilMinutes < 5 ? `in the last ${span(listed)}` : `${span(until)}–${span(listed)} ago`
}

export const WINDOW_HINT = 'The company gives no exact time and we found this after a gap in checking, so it went live somewhere in this window.'

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
  rippling: 'Rippling',
  gem: 'Gem',
  polymer: 'Polymer',
  jazzhr: 'JazzHR',
  bamboohr: 'BambooHR',
  recruitee: 'Recruitee',
  breezy: 'Breezy HR',
  pinpoint: 'Pinpoint',
  simplify: 'SimplifyJobs',
  themuse: 'The Muse',
  workable_search: 'Workable job search',
  remotive: 'Remotive',
  remoteok: 'RemoteOK',
  adzuna: 'Adzuna',
  jsearch: 'Google Jobs',
}
