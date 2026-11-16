import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useState } from 'react'
import { type AlertFrequency, type AlertSettings, api, type User } from '../api'
import { Toggle } from './ui'

const FREQUENCIES: { value: AlertFrequency; label: string; hint: string }[] = [
  { value: 'instant', label: 'As they come in', hint: 'At most one email an hour' },
  { value: 'hourly', label: 'Hourly digest', hint: 'Once an hour, if there’s anything new' },
  { value: 'daily', label: 'Daily digest', hint: 'Each morning at 8 am Eastern' },
]

function Card({ icon, title, hint, children }: { icon: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-3xl bg-white p-5 shadow-sm ring-1 ring-slate-200/70 sm:p-6">
      <div className="mb-4 flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-50 to-violet-100 text-xl" aria-hidden>{icon}</span>
        <div>
          <h2 className="font-display text-lg font-bold text-slate-900">{title}</h2>
          {hint && <p className="text-sm text-slate-500">{hint}</p>}
        </div>
      </div>
      {children}
    </section>
  )
}

/** The banner shown until the user confirms their email (alerts need it). */
export function VerifyEmailBanner({ user }: { user: User }) {
  const resend = useMutation({ mutationFn: api.resendVerification })
  if (user.email_verified) return null
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-900 ring-1 ring-amber-200">
      <span aria-hidden>✉️</span>
      <span className="flex-1">
        Confirm <b>{user.email}</b> to switch on job alerts: we sent you a link.
      </span>
      <button
        onClick={() => resend.mutate()}
        disabled={resend.isPending || resend.isSuccess}
        className="rounded-full bg-white px-3.5 py-1.5 font-semibold text-amber-900 ring-1 ring-amber-300 hover:bg-amber-100 disabled:opacity-60"
      >
        {resend.isSuccess ? (resend.data?.sent ? 'Sent ✓' : 'Couldn’t send (email isn’t set up)') : resend.isPending ? 'Sending…' : 'Resend link'}
      </button>
    </div>
  )
}

/** Job alerts: off until switched on; frequency, minimum match, daily cap, sponsorship and seniority, preview. */
export function AlertsCard() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['alert-settings'], queryFn: api.alertSettings })
  const [preview, setPreview] = useState(false)
  const save = useMutation({
    mutationFn: api.saveAlertSettings,
    onSuccess: (data) => queryClient.setQueryData(['alert-settings'], data),
  })
  const test = useMutation({ mutationFn: api.testEmail })
  const s = settings.data
  if (!s) return <Card icon="🔔" title="Job alerts"><div className="skeleton h-24" /></Card>
  const set = (patch: Partial<AlertSettings>) => save.mutate(patch)
  const off = !s.email_alerts

  return (
    <Card icon="🔔" title="Job alerts" hint="New jobs for your roles, by email, the moment you want them. Off until you switch it on.">
      <div className={`flex flex-wrap items-center gap-4 rounded-2xl p-4 ring-1 transition ${s.email_alerts ? 'bg-gradient-to-r from-emerald-50 to-teal-50 ring-emerald-200' : 'bg-slate-50 ring-slate-200'}`}>
        <div className="min-w-0 flex-1">
          <p className="font-display font-bold text-slate-900">{s.email_alerts ? 'Alerts are on' : 'Alerts are off'}</p>
          <p className="text-sm text-slate-600">
            {s.email_alerts ? <>Sending to <b>{s.email}</b>{s.last_alert_at ? ` · last one ${new Date(s.last_alert_at).toLocaleString()}` : ''}</> : 'Switch on to get new matching jobs by email.'}
          </p>
        </div>
        <button
          onClick={() => set({ email_alerts: !s.email_alerts })}
          disabled={save.isPending || (!s.email_verified && off)}
          title={!s.email_verified && off ? 'Confirm your email first' : undefined}
          className={`rounded-full px-5 py-2.5 text-sm font-bold shadow-sm transition disabled:opacity-50 ${
            s.email_alerts ? 'bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50' : 'bg-gradient-to-r from-brand-600 to-violet-600 text-white hover:brightness-110'
          }`}
        >
          {s.email_alerts ? 'Turn off' : 'Turn on alerts'}
        </button>
      </div>
      {!s.email_verified && <p className="mt-2 text-sm text-amber-700">Confirm your email address to switch alerts on.</p>}
      {save.data?.confirmation_sent && <p className="mt-2 text-sm text-emerald-700">✓ We emailed you a confirmation.</p>}
      {save.isError && <p className="mt-2 text-sm text-rose-700">{(save.error as Error).message}</p>}
      {s.email_mode !== 'on' && (
        <p className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500 ring-1 ring-slate-200">
          {s.email_mode === 'test' ? 'Test mode: emails go to a local test mail server, not to real inboxes.' : 'Email sending isn’t set up on this server yet.'}
        </p>
      )}

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-sm font-semibold text-slate-700">How often</p>
          <div className="space-y-2">
            {FREQUENCIES.map((f) => (
              <button
                key={f.value}
                onClick={() => set({ alert_frequency: f.value })}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-left ring-1 transition ${
                  s.alert_frequency === f.value ? 'bg-brand-50 ring-2 ring-brand-500' : 'bg-white ring-slate-200 hover:ring-slate-300'
                }`}
              >
                <span className={`h-4 w-4 shrink-0 rounded-full border-2 ${s.alert_frequency === f.value ? 'border-[5px] border-brand-600' : 'border-slate-300'}`} />
                <span>
                  <span className="block text-sm font-semibold text-slate-900">{f.label}</span>
                  <span className="block text-xs text-slate-500">{f.hint}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-4">
          <div>
            <p className="mb-1 flex items-baseline justify-between text-sm font-semibold text-slate-700">
              Only strong resume matches
              <span className="font-display text-brand-700">{s.alert_min_score ? `${s.alert_min_score}%+` : 'Any'}</span>
            </p>
            <input
              type="range"
              min={0}
              max={90}
              step={5}
              defaultValue={s.alert_min_score ?? 0}
              onMouseUp={(e) => set({ alert_min_score: Number((e.target as HTMLInputElement).value) || null })}
              onKeyUp={(e) => set({ alert_min_score: Number((e.target as HTMLInputElement).value) || null })}
              onTouchEnd={(e) => set({ alert_min_score: Number((e.target as HTMLInputElement).value) || null })}
              className="w-full accent-brand-600"
              aria-label="Minimum resume match"
            />
            <p className="text-xs text-slate-500">Uses your resume in My profile. Leave at “Any” to get every new job for your roles.</p>
          </div>
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-slate-700">At most</span>
            <select
              value={s.alert_daily_cap}
              onChange={(e) => set({ alert_daily_cap: Number(e.target.value) })}
              className="w-full rounded-xl border-0 bg-slate-50 px-3 py-2.5 text-sm ring-1 ring-slate-200 focus:ring-2 focus:ring-brand-500"
            >
              {[1, 3, 5, 10, 20].map((n) => (
                <option key={n} value={n}>{n} email{n > 1 ? 's' : ''} a day</option>
              ))}
            </select>
          </label>
          <div className="space-y-1">
            <Toggle label="Leave out jobs that rule out sponsorship" checked={s.hide_no_sponsor} onChange={(v) => set({ hide_no_sponsor: v })} />
            <Toggle label="Leave out senior roles" checked={s.exclude_senior} onChange={(v) => set({ exclude_senior: v })} />
          </div>
        </div>
      </div>

      <p className="mt-4 text-sm text-slate-500">
        For your roles: <b className="text-slate-700">{s.roles.join(', ') || 'none yet'}</b> (change them in the “Your roles” bar on the Feed).
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
        <button onClick={() => setPreview(!preview)} className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50">
          {preview ? 'Hide preview' : '👀 Preview an alert'}
        </button>
        <button
          onClick={() => test.mutate()}
          disabled={test.isPending || s.email_mode === 'off'}
          className="rounded-full bg-white px-4 py-2 text-sm font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50 disabled:opacity-40"
        >
          {test.isPending ? 'Sending…' : 'Send me a test email'}
        </button>
        {test.data && <span className={`text-sm ${test.data.sent ? 'text-emerald-700' : 'text-rose-700'}`}>{test.data.sent ? 'Sent ✓' : test.data.detail}</span>}
      </div>
      {preview && (
        <iframe
          title="Alert email preview"
          src="/api/alerts/preview"
          className="mt-4 h-[640px] w-full rounded-2xl bg-[#f5f3ff] ring-1 ring-slate-200"
        />
      )}
    </Card>
  )
}

/** Email status and changing the password. */
export function AccountCard({ user }: { user: User }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      setCurrent('')
      setNext('')
    },
  })
  const input = 'w-full rounded-xl border-0 bg-slate-50 px-3.5 py-2.5 text-sm ring-1 ring-slate-200 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-500'
  return (
    <Card icon="🔐" title="Account" hint="Your login. Only you can see your resume, roles, tracker and alerts.">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl bg-slate-50 p-4 ring-1 ring-slate-200">
        <span className="flex h-11 w-11 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-fuchsia-500 font-display text-lg font-bold text-white">
          {(user.name || user.email)[0].toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-slate-900">{user.name || user.email}</p>
          <p className="truncate text-sm text-slate-500">{user.email}</p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${user.email_verified ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
          {user.email_verified ? '✓ Email confirmed' : 'Email not confirmed'}
        </span>
        {user.is_admin && <span className="rounded-full bg-brand-100 px-2.5 py-1 text-xs font-bold text-brand-800">Admin</span>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          change.mutate()
        }}
        className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
      >
        <label className="block text-sm">
          <span className="mb-1 block font-semibold text-slate-700">Current password</span>
          <input type="password" required value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" className={input} />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-semibold text-slate-700">New password</span>
          <input type="password" required minLength={8} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" className={input} />
        </label>
        <button disabled={change.isPending} className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-60">
          {change.isPending ? 'Saving…' : 'Change password'}
        </button>
      </form>
      {change.isSuccess && <p className="mt-2 text-sm text-emerald-700">✓ Password changed. Other devices were logged out.</p>}
      {change.isError && <p className="mt-2 text-sm text-rose-700">{(change.error as Error).message}</p>}
      {!user.is_admin && <DeleteAccount />}
    </Card>
  )
}


/** Delete my account: everything goes (resume, roles, tracker, scores, alerts). Needs the password. */
function DeleteAccount() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const del = useMutation({
    mutationFn: () => api.deleteAccount(password),
    onSuccess: () => {
      queryClient.clear()
      queryClient.setQueryData(['me'], null)
    },
  })
  return (
    <div className="mt-6 rounded-2xl p-4 ring-1 ring-rose-200">
      <p className="font-semibold text-rose-800">Delete my account</p>
      <p className="text-sm text-slate-500">Removes your resume, roles, tracker, scores and alerts for good.</p>
      {open ? (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            del.mutate()
          }}
          className="mt-3 flex flex-wrap items-center gap-2"
        >
          <input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Your password, to confirm" autoComplete="current-password"
            className="min-w-56 flex-1 rounded-xl border-0 bg-slate-50 px-3.5 py-2.5 text-sm ring-1 ring-slate-200 focus:outline-none focus:ring-2 focus:ring-rose-400" />
          <button disabled={del.isPending} className="rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-rose-700 disabled:opacity-60">{del.isPending ? 'Deleting…' : 'Delete everything'}</button>
          <button type="button" onClick={() => setOpen(false)} className="px-2 text-sm font-semibold text-slate-500">Cancel</button>
          {del.isError && <p className="w-full text-sm text-rose-700">{(del.error as Error).message}</p>}
        </form>
      ) : (
        <button onClick={() => setOpen(true)} className="mt-3 rounded-full bg-white px-4 py-2 text-sm font-semibold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50">Delete my account…</button>
      )}
    </div>
  )
}
