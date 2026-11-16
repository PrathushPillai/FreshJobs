import { useMutation } from '@tanstack/react-query'
import { type ReactNode, useEffect, useState } from 'react'
import { api, type SupportTopic } from '../api'

const TOPICS: { value: SupportTopic; label: string; icon: string }[] = [
  { value: 'question', label: 'Question', icon: '💬' },
  { value: 'bug', label: 'Something’s broken', icon: '🐞' },
  { value: 'idea', label: 'Feature idea', icon: '💡' },
  { value: 'account', label: 'Account or login', icon: '🔐' },
  { value: 'other', label: 'Something else', icon: '✉️' },
]

/** A centred pop-up over a dimmed page; Esc or a click outside closes it. */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/60 p-0 backdrop-blur-sm sm:items-center sm:p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className="max-h-[92vh] w-full max-w-lg animate-rise overflow-y-auto rounded-t-3xl bg-white p-6 shadow-2xl sm:rounded-3xl"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <h2 className="font-display text-xl font-extrabold text-slate-900">{title}</h2>
          <button onClick={onClose} className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Contact & support: the message goes to the portal's admin by email, with your address to reply to. */
export function SupportDialog({ loggedIn, page, onClose }: { loggedIn: boolean; page?: string; onClose: () => void }) {
  const [topic, setTopic] = useState<SupportTopic>('question')
  const [message, setMessage] = useState('')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const send = useMutation({ mutationFn: () => api.sendSupport({ topic, message, page, ...(loggedIn ? {} : { email, name }) }) })
  const input = 'w-full rounded-xl border-0 bg-slate-50 px-3.5 py-2.5 text-sm ring-1 ring-slate-200 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-500'

  return (
    <Modal title="Contact & support" onClose={onClose}>
      {send.isSuccess ? (
        <div className="rounded-2xl bg-emerald-50 p-5 text-emerald-900 ring-1 ring-emerald-200">
          <p className="font-display text-lg font-bold">Thanks, we got it ✓</p>
          <p className="mt-1 text-sm">We’ll reply to {loggedIn ? 'your account’s email' : <b>{email}</b>}, usually within a day.</p>
          <button onClick={onClose} className="mt-4 rounded-full bg-emerald-700 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-800">Close</button>
        </div>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            send.mutate()
          }}
          className="space-y-4"
        >
          <p className="text-sm text-slate-500">A question, a bug, an idea: it comes straight to the person who runs FreshJobs.</p>
          <div className="flex flex-wrap gap-2">
            {TOPICS.map((t) => (
              <button
                key={t.value}
                type="button"
                onClick={() => setTopic(t.value)}
                className={`rounded-full px-3.5 py-1.5 text-sm font-semibold ring-1 transition ${
                  topic === t.value ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-700 ring-slate-200 hover:ring-slate-300'
                }`}
              >
                <span aria-hidden>{t.icon}</span> {t.label}
              </button>
            ))}
          </div>
          {!loggedIn && (
            <div className="grid gap-3 sm:grid-cols-2">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" className={input} />
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email to reply to" className={input} />
            </div>
          )}
          <textarea
            required
            minLength={10}
            maxLength={4000}
            rows={6}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder={topic === 'bug' ? 'What did you do, what happened, and what did you expect?' : 'How can we help?'}
            className={input}
          />
          {send.isError && <p className="rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">{(send.error as Error).message}</p>}
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-400">{message.length}/4000</span>
            <button
              disabled={send.isPending}
              className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-5 py-2.5 text-sm font-bold text-white shadow-md shadow-brand-600/25 hover:brightness-110 disabled:opacity-60"
            >
              {send.isPending ? 'Sending…' : 'Send message'}
            </button>
          </div>
        </form>
      )}
    </Modal>
  )
}

/** What FreshJobs keeps about you, and who can see it. */
export function PrivacyDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Privacy" onClose={onClose}>
      <div className="space-y-3 text-sm leading-relaxed text-slate-600">
        <p><b className="text-slate-900">What we keep:</b> your name, email and a hashed password (never the password itself); your roles and settings; your tracker; and, if you upload one, your resume and what we read from it (skills, years, degree), used only to score jobs for you.</p>
        <p><b className="text-slate-900">Who sees it:</b> only you. The portal’s admin sees account activity (sign-ups, logins, alerts sent) to keep the portal running. If they need to look at your account to fix a problem, that look is recorded. Your resume text is never shown to the admin.</p>
        <p><b className="text-slate-900">What we never do:</b> sell or share your data, or email you unless you switch alerts on. Every alert has a one-click unsubscribe.</p>
        <p><b className="text-slate-900">Your control:</b> remove your resume any time in My profile, or delete your whole account under My profile → Account.</p>
      </div>
    </Modal>
  )
}
