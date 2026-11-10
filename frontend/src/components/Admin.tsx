import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useState } from 'react'
import { type AdminEvent, type AdminUser, api, timeAgo } from '../api'
import { Modal } from './SupportDialog'
import { PageHeader } from './ui'

/** How each activity kind reads in the log. */
const KINDS: Record<string, { label: string; icon: string; tone?: 'bad' | 'warn' | 'good' | 'admin' }> = {
  signup: { label: 'Signed up', icon: '✨', tone: 'good' },
  login: { label: 'Logged in', icon: '🔓' },
  logout: { label: 'Logged out', icon: '↩' },
  login_failed: { label: 'Failed login', icon: '⚠️', tone: 'warn' },
  login_locked: { label: 'Locked out (too many tries)', icon: '⛔', tone: 'bad' },
  email_verified: { label: 'Confirmed email', icon: '✅', tone: 'good' },
  verification_resent: { label: 'Asked for a new confirmation link', icon: '✉️' },
  password_reset_requested: { label: 'Asked to reset password', icon: '🔑' },
  password_reset: { label: 'Reset password', icon: '🔑' },
  password_changed: { label: 'Changed password', icon: '🔑' },
  account_deleted: { label: 'Deleted their account', icon: '🗑', tone: 'warn' },
  alerts_on: { label: 'Turned alerts on', icon: '🔔', tone: 'good' },
  alerts_off: { label: 'Turned alerts off', icon: '🔕' },
  alert_sent: { label: 'Alert sent', icon: '📬', tone: 'good' },
  alert_failed: { label: 'Alert failed', icon: '❌', tone: 'bad' },
  unsubscribed: { label: 'Unsubscribed (email link)', icon: '🔕' },
  resume_uploaded: { label: 'Uploaded a resume', icon: '📄' },
  resume_deleted: { label: 'Removed their resume', icon: '📄' },
  recruiter_lookup: { label: 'Recruiter email lookup (Hunter.io)', icon: '🔎' },
  email_sent: { label: 'Email sent', icon: '✉️' },
  email_failed: { label: 'Email failed', icon: '❌', tone: 'bad' },
  support_request: { label: 'Support request', icon: '🛟', tone: 'warn' },
  admin_viewed_user: { label: 'Admin viewed the account', icon: '👁', tone: 'admin' },
  admin_disabled_user: { label: 'Admin switched the account off', icon: '⏸', tone: 'admin' },
  admin_enabled_user: { label: 'Admin switched the account back on', icon: '▶️', tone: 'admin' },
  admin_alerts_off: { label: 'Admin stopped their alerts', icon: '🔕', tone: 'admin' },
  admin_resent_verification: { label: 'Admin resent the confirmation link', icon: '✉️', tone: 'admin' },
  admin_deleted_user: { label: 'Admin deleted the account', icon: '🗑', tone: 'admin' },
}
const TONE = { bad: 'bg-rose-50 text-rose-700 ring-rose-200', warn: 'bg-amber-50 text-amber-800 ring-amber-200', good: 'bg-emerald-50 text-emerald-700 ring-emerald-200', admin: 'bg-brand-50 text-brand-700 ring-brand-200' }

function Tile({ label, value, sub, tone = 'default' }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'default' | 'bad' | 'good' }) {
  return (
    <div className={`rounded-2xl p-4 ring-1 ${tone === 'bad' ? 'bg-rose-50 ring-rose-200' : tone === 'good' ? 'bg-emerald-50 ring-emerald-200' : 'bg-white ring-slate-200/80'}`}>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 font-display text-2xl font-extrabold text-slate-900">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

function EventRow({ e, showUser = true, onUser }: { e: AdminEvent; showUser?: boolean; onUser?: (id: number) => void }) {
  const k = KINDS[e.kind] ?? { label: e.kind, icon: '•' }
  return (
    <li className="flex items-start gap-3 py-2.5">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-sm ring-1 ${k.tone ? TONE[k.tone] : 'bg-slate-50 ring-slate-200'}`} aria-hidden>{k.icon}</span>
      <div className="min-w-0 flex-1 text-sm">
        <p className="text-slate-900">
          <b className="font-semibold">{k.label}</b>
          {showUser && e.user_email && (
            <> · <button onClick={() => e.user_id && onUser?.(e.user_id)} className="text-brand-700 hover:underline">{e.user_email}</button></>
          )}
          {e.actor_email && <span className="text-slate-500"> · by {e.actor_email}</span>}
        </p>
        {e.detail && <p className="truncate text-xs text-slate-500" title={e.detail}>{e.detail}</p>}
      </div>
      <span className="shrink-0 text-xs text-slate-400" title={new Date(e.at).toLocaleString()}>{timeAgo(e.at)}</span>
    </li>
  )
}

function UserPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const queryClient = useQueryClient()
  const view = useQuery({ queryKey: ['admin-user', id], queryFn: () => api.adminUser(id) })
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [starting, setStarting] = useState(false)
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['admin-user', id] })
    queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    queryClient.invalidateQueries({ queryKey: ['admin-activity'] })
  }
  const act = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: done })
  const v = view.data
  return (
    <Modal title={v ? v.account.name || v.account.email : 'Account'} onClose={onClose}>
      {!v ? (
        <div className="skeleton h-40" />
      ) : (
        <div className="space-y-5 text-sm">
          <p className="-mt-2 rounded-xl bg-brand-50 px-3 py-2 text-xs text-brand-800 ring-1 ring-brand-200">
            👁 Opening an account is recorded in its activity log. The resume itself is never shown here.
          </p>
          <div className="flex flex-wrap gap-1.5">
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">{v.account.email}</span>
            <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${v.account.email_verified ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{v.account.email_verified ? '✓ Confirmed' : 'Not confirmed'}</span>
            {v.account.disabled && <span className="rounded-full bg-rose-100 px-2.5 py-1 text-xs font-bold text-rose-800">Switched off</span>}
            {v.account.is_admin && <span className="rounded-full bg-brand-100 px-2.5 py-1 text-xs font-bold text-brand-800">Admin</span>}
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">Joined {timeAgo(v.account.created_at)}</span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">{v.account.last_login_at ? `Last login ${timeAgo(v.account.last_login_at)}` : 'Never logged in again'}</span>
            <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600">{v.account.sessions} active session{v.account.sessions === 1 ? '' : 's'}</span>
          </div>

          <section>
            <h3 className="mb-1.5 font-display font-bold text-slate-900">Roles (For you)</h3>
            <div className="flex flex-wrap gap-1.5">
              {v.roles.length ? v.roles.map((r) => <span key={r} className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-800 ring-1 ring-brand-200">{r}</span>) : <span className="text-slate-500">None</span>}
            </div>
          </section>

          <section>
            <h3 className="mb-1.5 font-display font-bold text-slate-900">Alerts</h3>
            <p className="text-slate-600">
              {v.alerts.on ? <>On · {v.alerts.frequency} · at most {v.alerts.daily_cap}/day{v.alerts.min_score ? ` · ${v.alerts.min_score}%+ match` : ''} · {v.alerts.sent_today} sent today</> : 'Off'}
              {v.alerts.last_alert_at && <> · last one {timeAgo(v.alerts.last_alert_at)}</>}
            </p>
            {v.alerts.on && (
              <div className="mt-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
                <p className="text-xs font-semibold text-slate-600">
                  Next alert: {v.alerts.next_alert_count} job{v.alerts.next_alert_count === 1 ? '' : 's'} waiting · {v.alerts.due_now ? 'due at the next check' : 'not due yet'}
                </p>
                <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
                  {v.alerts.next_alert.map((j, i) => <li key={i}>• {j.title} · {j.company}</li>)}
                </ul>
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-1.5 font-display font-bold text-slate-900">Resume</h3>
            {v.resume.uploaded ? (
              <>
                <p className="text-slate-600">{v.resume.filename} · uploaded {v.resume.uploaded_at ? timeAgo(v.resume.uploaded_at) : ''} · {v.resume.years ?? '?'} years · {v.resume.degree || 'degree not read'} · {v.resume.scored.toLocaleString()} jobs scored</p>
                <div className="mt-1.5 flex flex-wrap gap-1">{v.resume.skills.map((s) => <span key={s} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{s}</span>)}</div>
                {v.resume.top_matches.length > 0 && (
                  <p className="mt-1.5 text-xs text-slate-500">Top matches: {v.resume.top_matches.map((m) => `${m.title} (${m.score})`).join(' · ')}</p>
                )}
              </>
            ) : <p className="text-slate-500">No resume uploaded.</p>}
          </section>

          <section>
            <h3 className="mb-1.5 font-display font-bold text-slate-900">Tracker ({v.tracker.length})</h3>
            {v.tracker.length ? (
              <ul className="max-h-36 space-y-1 overflow-y-auto text-xs text-slate-600">
                {v.tracker.map((a, i) => <li key={i}><b className="text-slate-800">{a.status}</b> · {a.title} · {a.company}</li>)}
              </ul>
            ) : <p className="text-slate-500">Nothing tracked.</p>}
          </section>

          <section>
            <h3 className="mb-1 font-display font-bold text-slate-900">Activity</h3>
            <ul className="max-h-64 divide-y divide-slate-100 overflow-y-auto">{v.activity.map((e) => <EventRow key={e.id} e={e} showUser={false} />)}</ul>
          </section>

          {!v.account.is_admin && (
            <section className="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
              <button
                onClick={async () => {
                  if (starting) return
                  setStarting(true)
                  try {
                    await api.startViewAs(id)
                    window.location.assign('/')  // a clean page as the viewed user (the login cookie changed)
                  } catch (e) {
                    setStarting(false)
                    alert((e as Error).message)
                  }
                }}
                disabled={starting}
                className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-3.5 py-2 text-xs font-bold text-white shadow-sm hover:brightness-110 disabled:opacity-70"
              >
                {starting ? 'Opening…' : '👁 View the portal as this user'}
              </button>
              {!v.account.email_verified && (
                <button onClick={() => act.mutate(() => api.adminResend(id))} className="rounded-full bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50">✉️ Resend confirmation</button>
              )}
              {v.alerts.on && (
                <button onClick={() => act.mutate(() => api.adminAlertsOff(id))} className="rounded-full bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50">🔕 Stop their alerts</button>
              )}
              <button onClick={() => act.mutate(() => api.adminDisable(id, !v.account.disabled))} className="rounded-full bg-white px-3.5 py-2 text-xs font-semibold text-amber-800 ring-1 ring-amber-300 hover:bg-amber-50">
                {v.account.disabled ? '▶️ Switch account back on' : '⏸ Switch account off'}
              </button>
              {confirmDelete ? (
                <span className="flex items-center gap-2 rounded-full bg-rose-50 px-3 py-1.5 text-xs text-rose-800 ring-1 ring-rose-200">
                  Delete {v.account.email} and all their data? This can’t be undone.
                  <button onClick={() => act.mutate(() => api.adminDelete(id).then(onClose))} className="rounded-full bg-rose-600 px-3 py-1 font-bold text-white hover:bg-rose-700">Delete</button>
                  <button onClick={() => setConfirmDelete(false)} className="font-semibold">Cancel</button>
                </span>
              ) : (
                <button onClick={() => setConfirmDelete(true)} className="rounded-full bg-white px-3.5 py-2 text-xs font-semibold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50">🗑 Delete account</button>
              )}
              {act.isError && <p className="w-full text-xs text-rose-700">{(act.error as Error).message}</p>}
            </section>
          )}
        </div>
      )}
    </Modal>
  )
}

function UsersTable({ onOpen }: { onOpen: (id: number) => void }) {
  const [q, setQ] = useState('')
  const users = useQuery({ queryKey: ['admin-users', q], queryFn: () => api.adminUsers(q), placeholderData: keepPreviousData })
  return (
    <div>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name or email" className="mb-3 w-full max-w-sm rounded-xl border-0 bg-white px-3.5 py-2.5 text-sm ring-1 ring-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-500" />
      <div className="overflow-x-auto rounded-2xl bg-white ring-1 ring-slate-200">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
            <tr>{['User', 'Joined', 'Last seen', 'Email', 'Alerts', 'Resume', 'Tracked', 'Events (7 d)'].map((h) => <th key={h} className="px-3 py-2.5 font-semibold">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {(users.data ?? []).map((u: AdminUser) => (
              <tr key={u.id} onClick={() => onOpen(u.id)} className="cursor-pointer hover:bg-brand-50/40">
                <td className="px-3 py-2.5">
                  <p className="font-semibold text-slate-900">{u.name || '—'} {u.is_admin && <span className="ml-1 rounded-full bg-brand-100 px-1.5 text-[10px] font-bold text-brand-800">ADMIN</span>}{u.disabled && <span className="ml-1 rounded-full bg-rose-100 px-1.5 text-[10px] font-bold text-rose-800">OFF</span>}</p>
                  <p className="text-xs text-slate-500">{u.email}</p>
                </td>
                <td className="px-3 py-2.5 text-slate-600">{timeAgo(u.created_at)}</td>
                <td className="px-3 py-2.5 text-slate-600">{u.last_activity_at ? timeAgo(u.last_activity_at) : '—'}</td>
                <td className="px-3 py-2.5">{u.email_verified ? <span className="text-emerald-700">✓ confirmed</span> : <span className="text-amber-700">not confirmed</span>}</td>
                <td className="px-3 py-2.5">{u.alerts_on ? <span className="text-emerald-700">on · {u.alert_frequency}</span> : <span className="text-slate-400">off</span>}</td>
                <td className="px-3 py-2.5">{u.has_resume ? '📄 yes' : <span className="text-slate-400">no</span>}</td>
                <td className="px-3 py-2.5 text-slate-700">{u.tracked}</td>
                <td className="px-3 py-2.5 text-slate-700">{u.events_7d}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.data?.length === 0 && <p className="p-4 text-sm text-slate-500">No users match.</p>}
      </div>
    </div>
  )
}

function ActivityLog({ onUser }: { onUser: (id: number) => void }) {
  const [kind, setKind] = useState('')
  const [problems, setProblems] = useState(false)
  const log = useQuery({
    queryKey: ['admin-activity', kind, problems],
    queryFn: () => api.adminActivity({ kind: kind || undefined, problems: problems || undefined, limit: 200 }),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  })
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-xl border-0 bg-white px-3 py-2 text-sm ring-1 ring-slate-200">
          <option value="">Everything</option>
          {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} className="accent-brand-600" /> Only problems
        </label>
        <span className="text-xs text-slate-400">Updates every 30 s</span>
      </div>
      <ul className="divide-y divide-slate-100 rounded-2xl bg-white px-4 ring-1 ring-slate-200">
        {(log.data ?? []).map((e) => <EventRow key={e.id} e={e} onUser={onUser} />)}
        {log.data?.length === 0 && <li className="py-4 text-sm text-slate-500">Nothing yet.</li>}
      </ul>
    </div>
  )
}

function SupportInbox() {
  const queryClient = useQueryClient()
  const items = useQuery({ queryKey: ['admin-support'], queryFn: api.adminSupport, refetchInterval: 60_000 })
  const mark = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'open' | 'done' }) => api.adminSupportStatus(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-support'] }),
  })
  return (
    <ul className="space-y-3">
      {(items.data ?? []).map((r) => (
        <li key={r.id} className={`rounded-2xl bg-white p-4 ring-1 ${r.status === 'open' ? 'ring-amber-200' : 'ring-slate-200 opacity-70'}`}>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <b className="text-slate-900">{r.topic_label}</b>
            <span className="text-slate-500">from {r.name ? `${r.name} · ` : ''}<a href={`mailto:${r.email}`} className="text-brand-700 hover:underline">{r.email}</a></span>
            {!r.user_id && <span className="rounded-full bg-slate-100 px-2 text-xs text-slate-600">not logged in</span>}
            {!r.emailed && <span className="rounded-full bg-rose-100 px-2 text-xs font-semibold text-rose-700">email didn’t send</span>}
            <span className="ml-auto text-xs text-slate-400">{timeAgo(r.at)}</span>
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{r.message}</p>
          <div className="mt-3 flex gap-2">
            <a href={`mailto:${r.email}?subject=${encodeURIComponent('Re: your FreshJobs request')}`} className="rounded-full bg-brand-600 px-3.5 py-1.5 text-xs font-bold text-white hover:bg-brand-700">Reply</a>
            <button onClick={() => mark.mutate({ id: r.id, status: r.status === 'open' ? 'done' : 'open' })} className="rounded-full bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50">
              {r.status === 'open' ? 'Mark done' : 'Reopen'}
            </button>
          </div>
        </li>
      ))}
      {items.data?.length === 0 && <li className="rounded-2xl bg-white p-4 text-sm text-slate-500 ring-1 ring-slate-200">No support requests yet.</li>}
    </ul>
  )
}

/** The admin dashboard: users, activity, support, and the portal's health. */
export default function Admin() {
  const overview = useQuery({ queryKey: ['admin-overview'], queryFn: api.adminOverview, refetchInterval: 60_000 })
  const [view, setView] = useState<'users' | 'activity' | 'support'>('users')
  const [open, setOpen] = useState<number | null>(null)
  const o = overview.data
  const pct = o ? Math.round((o.emails_24h / o.email_daily_limit) * 100) : 0
  return (
    <div className="space-y-5">
      <PageHeader icon="🛡️" title="Admin">
        Everyone using FreshJobs, what’s happening, and anything that needs you. Opening an account is recorded; resumes stay private.
      </PageHeader>
      {o && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <Tile label="Users" value={o.users} sub={`${o.signups_7d} new this week`} />
            <Tile label="Active today" value={o.active_24h} sub="logged in or used it" />
            <Tile label="Confirmed" value={`${o.confirmed}/${o.users}`} sub={o.disabled ? `${o.disabled} switched off` : 'email addresses'} />
            <Tile label="Alerts on" value={o.alerts_on} sub={`${o.alerts_24h} sent in 24 h`} tone={o.alerts_on ? 'good' : 'default'} />
            <Tile label="Emails (24 h)" value={`${o.emails_24h}/${o.email_daily_limit}`} sub={`${pct}% of the daily limit${o.emails_failed_24h ? ` · ${o.emails_failed_24h} failed` : ''}`} tone={o.emails_failed_24h || pct > 80 ? 'bad' : 'default'} />
            <Tile label="Failed logins (24 h)" value={o.failed_logins_24h} sub={`${o.recruiter_lookups_24h} Hunter lookups`} tone={o.failed_logins_24h > 20 ? 'bad' : 'default'} />
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-slate-500">
            <span>Email: <b className="text-slate-700">{o.email_mode === 'on' ? `sending as ${o.email_sender}` : o.email_mode === 'test' ? 'test mode (local)' : 'not set up'}</b></span>
            {o.last_poll && <span>Last check: <b className="text-slate-700">{o.last_poll.finished_at ? timeAgo(o.last_poll.finished_at) : 'running'}</b> · {o.last_poll.new_jobs ?? 0} new jobs · {o.last_poll.errors ?? 0} source errors</span>}
          </div>
          {o.problems.length > 0 && (
            <section className="rounded-2xl bg-rose-50/60 px-4 py-2 ring-1 ring-rose-200">
              <h3 className="pt-2 font-display text-sm font-bold text-rose-800">Needs a look</h3>
              <ul className="divide-y divide-rose-100">{o.problems.map((e) => <EventRow key={e.id} e={e} onUser={setOpen} />)}</ul>
            </section>
          )}
        </>
      )}
      <div className="flex gap-1 rounded-full bg-white p-1 ring-1 ring-slate-200 w-fit" role="tablist">
        {(['users', 'activity', 'support'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={view === t} onClick={() => setView(t)} className={`rounded-full px-4 py-1.5 text-sm font-semibold capitalize ${view === t ? 'bg-brand-600 text-white' : 'text-slate-600 hover:bg-slate-50'}`}>
            {t}
          </button>
        ))}
      </div>
      {view === 'users' && <UsersTable onOpen={setOpen} />}
      {view === 'activity' && <ActivityLog onUser={setOpen} />}
      {view === 'support' && <SupportInbox />}
      {open !== null && <UserPanel id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
