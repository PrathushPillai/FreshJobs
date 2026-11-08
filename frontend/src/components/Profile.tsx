import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { api, type Profile as ProfileData, type ProfileEdit } from '../api'
import { PageHeader, Toggle } from './ui'

const DEGREES = [
  { value: '', label: 'Not stated' },
  { value: 'bachelors', label: "Bachelor's" },
  { value: 'masters', label: "Master's" },
  { value: 'phd', label: 'PhD' },
] as const

const VISAS = [
  { value: '', label: 'Choose…' },
  { value: 'f1_opt', label: 'F-1 OPT' },
  { value: 'stem_opt', label: 'F-1 STEM OPT' },
  { value: 'f1_student', label: 'F-1 student (CPT / internship)' },
  { value: 'h1b', label: 'H-1B (transfer)' },
  { value: 'citizen_gc', label: 'US citizen or green card' },
  { value: 'other', label: 'Other' },
]

/** Read a file as base64 (without the data: prefix) for the upload request. */
function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** Removable chips plus an input to add more (with suggestions when `options` is given). */
function ChipEditor({ values, onChange, placeholder, options, tone = 'brand' }: {
  values: string[]
  onChange: (v: string[]) => void
  placeholder: string
  options?: string[]
  tone?: 'brand' | 'slate'
}) {
  const [draft, setDraft] = useState('')
  const listId = useRef(`opts-${Math.random().toString(36).slice(2)}`).current
  const add = (v: string) => {
    const clean = v.trim()
    if (!clean) return
    const canonical = options?.find((o) => o.toLowerCase() === clean.toLowerCase()) ?? clean
    if (!values.some((x) => x.toLowerCase() === canonical.toLowerCase())) onChange([...values, canonical])
    setDraft('')
  }
  const chip = tone === 'brand' ? 'bg-brand-50 text-brand-800 ring-brand-200' : 'bg-slate-100 text-slate-700 ring-slate-200'
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((v) => (
        <span key={v} className={`inline-flex items-center gap-0.5 rounded-full py-0.5 pl-2.5 pr-1 text-xs font-semibold ring-1 ring-inset ${chip}`}>
          {v}
          <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="rounded-full px-1 opacity-50 hover:bg-rose-50 hover:text-rose-600 hover:opacity-100" aria-label={`Remove ${v}`}>
            ✕
          </button>
        </span>
      ))}
      <input
        value={draft}
        list={options ? listId : undefined}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            add(draft)
          }
        }}
        onBlur={() => add(draft)}
        placeholder={placeholder}
        className="w-36 rounded-full border border-dashed border-slate-300 bg-white px-2.5 py-0.5 text-xs outline-none focus:w-48 focus:border-brand-500"
      />
      {options && (
        <datalist id={listId}>
          {options.map((o) => <option key={o} value={o} />)}
        </datalist>
      )}
    </div>
  )
}

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4 rounded-3xl bg-white p-6 shadow-sm ring-1 ring-slate-200/70">
      <div>
        <h3 className="font-display text-lg font-bold text-slate-900">{title}</h3>
        {hint && <p className="mt-0.5 text-sm text-slate-500">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5 sm:grid-cols-[11rem_1fr] sm:items-start">
      <span className="pt-1 text-sm font-semibold text-slate-700">{label}</span>
      <div>{children}</div>
    </div>
  )
}

function Upload({ onFile, busy, error, compact = false }: { onFile: (f: File) => void; busy: boolean; error?: string; compact?: boolean }) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const f = e.dataTransfer.files[0]
        if (f) onFile(f)
      }}
      className={`rounded-3xl border-2 border-dashed text-center transition ${compact ? 'p-4' : 'p-10'} ${
        over ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-white/70'
      }`}
    >
      <input
        ref={input}
        type="file"
        accept=".pdf,.docx,.txt"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
      {!compact && <p className="text-4xl" aria-hidden>📄</p>}
      <p className={`${compact ? '' : 'mt-3'} font-display font-bold text-slate-800`}>
        {busy ? 'Reading your resume…' : compact ? 'Drop a new resume here' : 'Drop your resume here'}
      </p>
      {!compact && <p className="mt-1 text-sm text-slate-500">PDF, Word (.docx) or plain text, up to 5 MB. It stays on this server.</p>}
      <button
        type="button"
        disabled={busy}
        onClick={() => input.current?.click()}
        className="mt-3 rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-5 py-2 text-sm font-bold text-white shadow-md shadow-brand-600/25 hover:brightness-110 disabled:opacity-60"
      >
        {busy ? (
          <span className="inline-flex items-center gap-2">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/40 border-t-white" /> Reading…
          </span>
        ) : compact ? 'Replace resume' : 'Choose a file'}
      </button>
      {error && <p className="mt-3 text-sm text-rose-700">{error}</p>}
    </div>
  )
}

export default function Profile({ onSeeMatches }: { onSeeMatches: () => void }) {
  const queryClient = useQueryClient()
  const profile = useQuery({
    queryKey: ['profile'],
    queryFn: api.profile,
    // While the feed is being scored, refresh the progress.
    refetchInterval: (q) => (q.state.data?.scoring.running ? 3000 : false),
  })
  const p = profile.data
  const [edit, setEdit] = useState<ProfileEdit | null>(null)
  const view: ProfileEdit = { ...p, ...edit }
  const changed = edit !== null
  const set = (patch: ProfileEdit) => setEdit((e) => ({ ...(e ?? {}), ...patch }))

  const refreshAll = (data: ProfileData) => {
    queryClient.setQueryData(['profile'], data)
    ;['jobs', 'fresh-counts', 'job', 'top-matches'].forEach((key) => queryClient.invalidateQueries({ queryKey: [key] }))
  }
  const upload = useMutation({
    mutationFn: async (file: File) => api.uploadResume(file.name, await toBase64(file)),
    onSuccess: (data) => {
      setEdit(null)
      refreshAll(data)
    },
  })
  const save = useMutation({ mutationFn: (e: ProfileEdit) => api.updateProfile(e), onSuccess: (data) => (setEdit(null), refreshAll(data)) })
  const remove = useMutation({ mutationFn: api.deleteResume, onSuccess: (data) => (setEdit(null), refreshAll(data)) })
  const addRole = useMutation({
    mutationFn: async (role: string) => {
      const prefs = await api.prefs()
      return api.savePrefs({ ...prefs, include_keywords: [...new Set([...prefs.include_keywords, role])] })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profile'] })
      queryClient.invalidateQueries({ queryKey: ['prefs'] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })

  if (!p) return <div className="skeleton h-64 rounded-3xl" />
  const scoring = p.scoring
  const scoredPct = scoring.total ? Math.round((100 * scoring.scored) / scoring.total) : 100

  return (
    <div className="space-y-5">
      <PageHeader icon="🎯" title="My profile">
        Upload your resume and every job gets a match score: your skills, experience, education and target roles against what it asks for,
        plus your visa situation. Nothing leaves this server.
      </PageHeader>

      {!p.has_resume ? (
        <Upload onFile={(f) => upload.mutate(f)} busy={upload.isPending} error={(upload.error as Error | null)?.message} />
      ) : (
        <>
          <section className="flex flex-wrap items-center gap-4 rounded-3xl bg-gradient-to-r from-brand-50 to-emerald-50 p-5 ring-1 ring-brand-100">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-2xl shadow-sm" aria-hidden>📄</span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-display font-bold text-slate-900">{p.filename}</p>
              <p className="text-sm text-slate-600">
                {scoring.running ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" />
                    Scoring your jobs… {scoring.scored.toLocaleString()} of {scoring.total.toLocaleString()} ({scoredPct}%)
                  </span>
                ) : (
                  <>✓ {(p.scored ?? 0).toLocaleString()} jobs scored against it</>
                )}
              </p>
              {scoring.running && (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white">
                  <div className="h-full rounded-full bg-gradient-to-r from-brand-500 to-emerald-500 transition-all" style={{ width: `${scoredPct}%` }} />
                </div>
              )}
            </div>
            <button onClick={onSeeMatches} className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-4 py-2 text-sm font-bold text-white shadow-md shadow-brand-600/25 hover:brightness-110">
              See my best matches →
            </button>
          </section>

          <Card title="What we read from your resume" hint="Correct anything that's off: scores update as soon as you save.">
            <Field label="Skills">
              <ChipEditor values={view.skills ?? []} onChange={(skills) => set({ skills })} placeholder="+ Add a skill" options={p.vocabulary} />
            </Field>
            <Field label="Years of experience">
              <input
                type="number"
                min={0}
                max={50}
                step={0.5}
                value={view.years ?? ''}
                onChange={(e) => set({ years: e.target.value === '' ? null : Number(e.target.value) })}
                className="w-28 rounded-xl border border-slate-300 px-3 py-1.5 text-sm"
              />
              <span className="ml-2 text-xs text-slate-500">Estimated from the dates of your roles; internships count.</span>
            </Field>
            <Field label="Highest degree">
              <select value={view.degree ?? ''} onChange={(e) => set({ degree: e.target.value as ProfileEdit['degree'] })} className="rounded-xl border border-slate-300 px-3 py-1.5 text-sm">
                {DEGREES.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </Field>
            <Field label="Roles in your resume">
              <ChipEditor values={view.target_roles ?? []} onChange={(target_roles) => set({ target_roles })} placeholder="+ Add a role" tone="slate" />
              {(p.suggested_roles?.length ?? 0) > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-slate-500">Not in your feed's roles yet:</span>
                  {p.suggested_roles!.map((r) => (
                    <button key={r} onClick={() => addRole.mutate(r)} disabled={addRole.isPending} className="rounded-full bg-white px-2.5 py-0.5 font-semibold text-brand-700 ring-1 ring-brand-200 hover:ring-brand-400 disabled:opacity-50">
                      + {r}
                    </button>
                  ))}
                </div>
              )}
            </Field>
          </Card>

          <Card title="Your situation" hint="Jobs that rule out sponsorship rank lower when you need it; jobs outside your places rank a little lower.">
            <Field label="Visa status">
              <select value={view.visa_status ?? ''} onChange={(e) => set({ visa_status: e.target.value })} className="rounded-xl border border-slate-300 px-3 py-1.5 text-sm">
                {VISAS.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
              </select>
            </Field>
            <Field label="Sponsorship">
              <Toggle label="I'll need visa sponsorship now or later" checked={view.needs_sponsorship ?? true} onChange={(v) => set({ needs_sponsorship: v })} />
            </Field>
            {(view.visa_status === 'f1_opt' || view.visa_status === 'stem_opt') && (
              <Field label="OPT ends on">
                <input type="date" value={view.opt_end ?? ''} onChange={(e) => set({ opt_end: e.target.value || null })} className="rounded-xl border border-slate-300 px-3 py-1.5 text-sm" />
                {view.visa_status === 'f1_opt' && <p className="mt-1 text-xs text-slate-500">For a STEM OPT extension, your employer must use E-Verify: look for the E-Verify badge.</p>}
              </Field>
            )}
            <Field label="Preferred places">
              <ChipEditor values={view.locations ?? []} onChange={(locations) => set({ locations })} placeholder="+ State or city (e.g. CA, Boston)" tone="slate" />
              <p className="mt-1 text-xs text-slate-500">Leave empty for anywhere in the US. Remote jobs always count as a fit.</p>
            </Field>
          </Card>

          <div className="flex flex-wrap items-center gap-3">
            <button
              disabled={!changed || save.isPending}
              onClick={() => edit && save.mutate(edit)}
              className="rounded-full bg-gradient-to-r from-brand-600 to-violet-600 px-6 py-2.5 text-sm font-bold text-white shadow-lg shadow-brand-600/25 hover:brightness-110 disabled:from-slate-300 disabled:to-slate-300 disabled:shadow-none"
            >
              {save.isPending ? 'Saving…' : 'Save and rescore'}
            </button>
            {changed && <button onClick={() => setEdit(null)} className="text-sm font-medium text-slate-500 hover:text-slate-800">Discard changes</button>}
            <div className="ml-auto w-full sm:w-80">
              <Upload compact onFile={(f) => upload.mutate(f)} busy={upload.isPending} error={(upload.error as Error | null)?.message} />
            </div>
          </div>
          <button
            onClick={() => window.confirm('Remove your resume and all match scores?') && remove.mutate()}
            className="text-sm font-medium text-rose-600 hover:underline"
          >
            Remove my resume
          </button>
        </>
      )}
    </div>
  )
}
