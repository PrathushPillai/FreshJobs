import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Prefs, type Status } from '../api'
import { PageHeader } from './ui'

const toList = (text: string) =>
  text
    .split(/[\n,]/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)

function Integration({ name, ok, hint }: { name: string; ok: boolean; hint: string }) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className={ok ? 'text-brand-600' : 'text-slate-400'}>{ok ? '●' : '○'}</span>
      <span>
        <b>{name}</b> {ok ? 'connected' : 'not set up'}
        {!ok && <span className="block text-xs text-slate-500">{hint}</span>}
      </span>
    </li>
  )
}

export default function Settings({ status, isAdmin }: { status: Status | undefined; isAdmin: boolean }) {
  const emailMode = status?.email_mode ?? (status?.email_configured ? 'on' : 'off')
  const queryClient = useQueryClient()
  const prefs = useQuery({ queryKey: ['prefs'], queryFn: api.prefs })
  const [draft, setDraft] = useState<Prefs | null>(null)
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')

  useEffect(() => {
    if (prefs.data) {
      setDraft(prefs.data)
      setInclude(prefs.data.include_keywords.join(', '))
      setExclude(prefs.data.exclude_keywords.join(', '))
    }
  }, [prefs.data])

  const save = useMutation({
    mutationFn: api.savePrefs,
    onSuccess: (data) => {
      queryClient.setQueryData(['prefs'], data)
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['alert-settings'] })
    },
  })

  if (!draft) return <p className="text-sm text-slate-500">Loading…</p>

  return (
    <div className="space-y-5">
      <PageHeader icon="⚙️" title="Settings">
        The roles in your “For you” feed and your alerts{isAdmin ? ', and the data sources connected to the portal' : ''}. Alert emails are set up in My profile.
      </PageHeader>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ ...draft, include_keywords: toList(include), exclude_keywords: toList(exclude) })
        }}
        className="max-w-3xl space-y-5 rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70 p-5"
      >
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Your roles</span>
          <span className="mb-1 block text-xs text-slate-500">
            “For you” and your alerts show jobs whose title is one of these roles, in any spelling. Separate them with commas. The “Your roles” bar on the Feed edits the same list.
          </span>
          <textarea value={include} onChange={(e) => setInclude(e.target.value)} rows={4} className="w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Leave out titles containing</span>
          <textarea value={exclude} onChange={(e) => setExclude(e.target.value)} rows={2} className="w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        {isAdmin && (
          <div className="space-y-2 border-t border-slate-100 pt-4">
            <h3 className="text-sm font-medium">Connected services (admin)</h3>
            <ul className="space-y-1.5">
              <Integration
                name="Email"
                ok={emailMode === 'on'}
                hint={
                  emailMode === 'test'
                    ? 'Test mode: .env points at a local test mail server, so no email reaches a real inbox. Add real SMTP settings (see .env.example).'
                    : 'Add SMTP settings to .env (see .env.example) so the portal can send account emails and alerts.'
                }
              />
              <Integration name="Adzuna" ok={status?.adzuna_configured ?? false} hint="Free key from developer.adzuna.com. Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env for jobs from thousands more employers." />
              <Integration name="Google Jobs (LinkedIn, Indeed, Glassdoor)" ok={status?.jsearch_configured ?? false} hint="Free key from JSearch on RapidAPI. Set JSEARCH_API_KEY in .env, then add searches on the Sources page." />
              <Integration name={`H-1B sponsor data${status?.h1b_sponsors ? ` (${status.h1b_sponsors.toLocaleString()} employers)` : ''}`} ok={(status?.h1b_sponsors ?? 0) > 0} hint="Download the DOL LCA disclosure file into backend/data/h1b/ and run: python -m app.h1b import <file> (see README)." />
              <Integration name="Hunter.io" ok={status?.hunter_configured ?? false} hint="Free key from hunter.io. Set HUNTER_API_KEY in .env for recruiter email lookup." />
            </ul>
            <p className="text-xs text-slate-500">After editing .env, restart the backend.</p>
          </div>
        )}
        <div className="flex items-center gap-3">
          <button disabled={save.isPending} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            Save
          </button>
          {save.isSuccess && <span className="text-sm text-brand-700">Saved</span>}
          {save.isError && <span className="text-sm text-red-600">{(save.error as Error).message}</span>}
        </div>
      </form>
    </div>
  )
}
