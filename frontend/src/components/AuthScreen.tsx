import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, type ReactNode, useState } from 'react'
import { api, type User } from '../api'
import { PrivacyDialog, SupportDialog } from './SupportDialog'

type Mode = 'login' | 'signup' | 'forgot' | 'reset'

/** How strong a password looks: 0-4, for the meter under the sign-up and reset fields. */
function strength(pw: string): number {
  let s = 0
  if (pw.length >= 8) s++
  if (pw.length >= 12) s++
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++
  if (/\d/.test(pw) || /[^A-Za-z0-9]/.test(pw)) s++
  return s
}
const STRENGTH = [
  { label: 'Too short', bar: 'bg-rose-400' },
  { label: 'Weak', bar: 'bg-rose-400' },
  { label: 'Okay', bar: 'bg-amber-400' },
  { label: 'Good', bar: 'bg-emerald-400' },
  { label: 'Strong', bar: 'bg-emerald-500' },
]

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-semibold text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

const INPUT =
  'w-full rounded-xl border-0 bg-slate-50 px-4 py-3 text-[15px] text-slate-900 ring-1 ring-slate-200 transition placeholder:text-slate-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-500'

function PasswordInput({ value, onChange, placeholder, autoComplete, showMeter }: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  autoComplete: string
  showMeter?: boolean
}) {
  const [show, setShow] = useState(false)
  const s = strength(value)
  return (
    <div>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          required
          minLength={showMeter ? 8 : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          className={`${INPUT} pr-16`}
        />
        <button
          type="button"
          onClick={() => setShow(!show)}
          className="absolute inset-y-0 right-2 my-auto h-8 rounded-lg px-2.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 hover:text-slate-800"
          aria-label={show ? 'Hide password' : 'Show password'}
        >
          {show ? 'Hide' : 'Show'}
        </button>
      </div>
      {showMeter && value && (
        <div className="mt-2 flex items-center gap-2">
          <div className="flex flex-1 gap-1">
            {[1, 2, 3, 4].map((i) => (
              <span key={i} className={`h-1.5 flex-1 rounded-full transition ${i <= s ? STRENGTH[s].bar : 'bg-slate-200'}`} />
            ))}
          </div>
          <span className="w-16 text-right text-xs font-medium text-slate-500">{STRENGTH[value.length < 8 ? 0 : s].label}</span>
        </div>
      )}
    </div>
  )
}

function Submit({ busy, children }: { busy: boolean; children: ReactNode }) {
  return (
    <button
      disabled={busy}
      className="relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-xl bg-gradient-to-r from-brand-600 via-violet-600 to-fuchsia-600 px-4 py-3.5 text-[15px] font-bold text-white shadow-lg shadow-brand-600/30 transition hover:-translate-y-px hover:brightness-110 disabled:translate-y-0 disabled:opacity-70"
    >
      {busy && <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden />}
      {children}
    </button>
  )
}

const FEATURES = [
  { icon: '⚡', title: 'Minutes fresh', text: 'Straight from 25,000+ company career sites, long before LinkedIn.' },
  { icon: '🛂', title: 'Visa signals on every job', text: 'H-1B history, E-Verify, OPT/CPT and "says it sponsors".' },
  { icon: '🎯', title: 'Your resume, matched', text: 'A 0–100 score with the skills you have and the ones you miss.' },
]

/** Sign up, log in, forgot password and reset password: the whole screen until the user is logged in. */
export default function AuthScreen({ resetToken, onDone }: { resetToken?: string | null; onDone: () => void }) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<Mode>(resetToken ? 'reset' : 'login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [forgotSent, setForgotSent] = useState(false)
  const [dialog, setDialog] = useState<'support' | 'privacy' | null>(null)

  const signedIn = (user: User) => {
    queryClient.setQueryData(['me'], user)
    queryClient.invalidateQueries()
    onDone()
  }
  const submit = useMutation({
    mutationFn: async () => {
      if (mode === 'signup') return api.signup(name, email, password)
      if (mode === 'login') return api.login(email, password)
      if (mode === 'reset') return api.resetPassword(resetToken ?? '', password)
      await api.forgotPassword(email)
      return null
    },
    onSuccess: (user) => {
      if (user) signedIn(user)
      else setForgotSent(true)
    },
  })
  const go = (m: Mode) => {
    submit.reset()
    setForgotSent(false)
    setPassword('')
    setMode(m)
  }
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    submit.mutate()
  }
  const error = (submit.error as Error | null)?.message

  const heading = {
    login: { title: 'Welcome back', sub: 'Log in to see what was posted since you left.' },
    signup: { title: 'Create your account', sub: 'Free. Your feed, resume match, tracker and alerts, all in one place.' },
    forgot: { title: 'Forgot your password?', sub: "Enter your email and we'll send you a link to choose a new one." },
    reset: { title: 'Choose a new password', sub: 'At least 8 characters. You’ll be logged in right after.' },
  }[mode]

  return (
    <div className="hero relative min-h-screen overflow-hidden">
      <div className="hero-grid pointer-events-none absolute inset-0" aria-hidden />
      <div className="pointer-events-none absolute -left-32 top-1/3 h-96 w-96 rounded-full bg-fuchsia-500/25 blur-3xl" aria-hidden />
      <div className="pointer-events-none absolute -right-24 -top-24 h-[28rem] w-[28rem] rounded-full bg-emerald-400/20 blur-3xl" aria-hidden />

      <div className="relative mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-5 py-10 lg:grid-cols-[1.1fr_1fr]">
        {/* The pitch */}
        <section className="text-white">
          <div className="flex items-center gap-2.5">
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-400 via-brand-600 to-fuchsia-600 shadow-lg shadow-brand-600/40" aria-hidden>
              <svg viewBox="0 0 24 24" className="h-6 w-6 fill-white drop-shadow"><path d="M13.5 2 4 14h6.5L9 22l11-13h-6.6z" /></svg>
            </span>
            <span className="font-display text-2xl font-extrabold tracking-tight">
              Fresh<span className="bg-gradient-to-r from-emerald-300 to-teal-300 bg-clip-text text-transparent">Jobs</span>
            </span>
          </div>
          <h1 className="mt-8 max-w-xl font-display text-4xl font-extrabold leading-[1.1] tracking-tight sm:text-5xl">
            Apply first.{' '}
            <span className="bg-gradient-to-r from-emerald-300 via-teal-200 to-sky-300 bg-clip-text text-transparent">Get seen first.</span>
          </h1>
          <p className="mt-4 max-w-lg text-lg text-brand-100/90">
            New roles from company career sites, minutes after they’re posted, with the visa details that matter to international students.
          </p>
          <ul className="mt-8 hidden max-w-lg space-y-3 sm:block">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex items-start gap-3 rounded-2xl bg-white/[0.06] p-3.5 ring-1 ring-white/10 backdrop-blur">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-xl" aria-hidden>{f.icon}</span>
                <span>
                  <span className="block font-display font-bold">{f.title}</span>
                  <span className="block text-sm text-brand-100/80">{f.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        {/* The form */}
        <section className="animate-rise">
          <div className="mx-auto w-full max-w-md rounded-3xl bg-white/95 p-7 shadow-2xl shadow-brand-950/40 ring-1 ring-white/40 backdrop-blur-xl sm:p-8">
            {(mode === 'login' || mode === 'signup') && (
              <div className="mb-6 grid grid-cols-2 rounded-2xl bg-slate-100 p-1 text-sm font-semibold" role="tablist">
                {(['login', 'signup'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="tab"
                    aria-selected={mode === m}
                    onClick={() => go(m)}
                    className={`rounded-xl py-2.5 transition ${mode === m ? 'bg-white text-brand-800 shadow' : 'text-slate-500 hover:text-slate-800'}`}
                  >
                    {m === 'login' ? 'Log in' : 'Sign up'}
                  </button>
                ))}
              </div>
            )}
            <h2 className="font-display text-2xl font-extrabold text-slate-900">{heading.title}</h2>
            <p className="mt-1 text-sm text-slate-500">{heading.sub}</p>

            {mode === 'forgot' && forgotSent ? (
              <div className="mt-6 rounded-2xl bg-emerald-50 p-4 text-sm text-emerald-800 ring-1 ring-emerald-200">
                <p className="font-semibold">Check your inbox ✉️</p>
                <p className="mt-1">
                  If <b>{email}</b> has an account, a reset link is on its way. It works once, for 1 hour.
                </p>
              </div>
            ) : (
              <form onSubmit={onSubmit} className="mt-6 space-y-4">
                {mode === 'signup' && (
                  <Field label="Your name">
                    <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Alex Kim" autoComplete="name" className={INPUT} />
                  </Field>
                )}
                {mode !== 'reset' && (
                  <Field label="Email">
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@university.edu"
                      autoComplete="email"
                      className={INPUT}
                    />
                  </Field>
                )}
                {mode !== 'forgot' && (
                  <Field
                    label={mode === 'reset' ? 'New password' : 'Password'}
                    hint={
                      mode === 'login' ? (
                        <button type="button" onClick={() => go('forgot')} className="font-semibold text-brand-700 hover:underline">
                          Forgot your password?
                        </button>
                      ) : undefined
                    }
                  >
                    <PasswordInput
                      value={password}
                      onChange={setPassword}
                      placeholder={mode === 'login' ? 'Your password' : 'At least 8 characters'}
                      autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                      showMeter={mode !== 'login'}
                    />
                  </Field>
                )}
                {error && (
                  <p role="alert" className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-sm font-medium text-rose-700 ring-1 ring-rose-200">
                    {error}
                  </p>
                )}
                <Submit busy={submit.isPending}>
                  {mode === 'login' ? 'Log in' : mode === 'signup' ? 'Create account' : mode === 'forgot' ? 'Send reset link' : 'Save and log in'}
                </Submit>
              </form>
            )}

            <p className="mt-6 text-center text-sm text-slate-500">
              {mode === 'login' && (
                <>New here? <button onClick={() => go('signup')} className="font-semibold text-brand-700 hover:underline">Create an account</button></>
              )}
              {mode === 'signup' && (
                <>Already have one? <button onClick={() => go('login')} className="font-semibold text-brand-700 hover:underline">Log in</button></>
              )}
              {(mode === 'forgot' || mode === 'reset') && (
                <button onClick={() => go('login')} className="font-semibold text-brand-700 hover:underline">← Back to log in</button>
              )}
            </p>
            {mode === 'signup' && (
              <p className="mt-4 text-center text-xs text-slate-400">
                Email alerts stay off until you switch them on. We never share your email or resume.
              </p>
            )}
            <p className="mt-4 flex justify-center gap-4 border-t border-slate-100 pt-4 text-xs">
              <button onClick={() => setDialog('support')} className="font-semibold text-slate-500 hover:text-brand-700">Need help? Contact us</button>
              <button onClick={() => setDialog('privacy')} className="font-semibold text-slate-500 hover:text-brand-700">Privacy</button>
            </p>
          </div>
          {dialog === 'support' && <SupportDialog loggedIn={false} page="login screen" onClose={() => setDialog(null)} />}
          {dialog === 'privacy' && <PrivacyDialog onClose={() => setDialog(null)} />}
        </section>
      </div>
    </div>
  )
}
