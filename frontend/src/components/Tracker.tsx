import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { APP_STATUSES, type Application, type AppStatus, STATUS_STYLES, api, formatDateTime } from '../api'

function NotesCell({ app }: { app: Application }) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState(app.notes)
  const save = useMutation({
    mutationFn: (notes: string) => api.updateApplication(app.id, { notes }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['applications'] }),
  })
  return (
    <textarea
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => value !== app.notes && save.mutate(value)}
      placeholder="Notes: referral, interview dates…"
      rows={1}
      className="w-full min-w-40 resize-y rounded border border-transparent bg-transparent px-1 py-0.5 text-sm hover:border-slate-200 focus:border-slate-300 focus:bg-white"
    />
  )
}

function ContactCell({ app }: { app: Application }) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState(app.contact)
  const save = useMutation({
    mutationFn: (contact: string) => api.updateApplication(app.id, { contact }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['applications'] }),
  })
  return (
    <input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => value !== app.contact && save.mutate(value)}
      placeholder="Recruiter / referral"
      className="w-full min-w-32 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm hover:border-slate-200 focus:border-slate-300 focus:bg-white"
    />
  )
}

export default function Tracker() {
  const queryClient = useQueryClient()
  const apps = useQuery({ queryKey: ['applications'], queryFn: api.applications })
  const [statusFilter, setStatusFilter] = useState<AppStatus | 'all'>('all')
  const [form, setForm] = useState({ company: '', title: '', url: '' })
  const [adding, setAdding] = useState(false)

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['applications'] })
    queryClient.invalidateQueries({ queryKey: ['jobs'] })
    queryClient.invalidateQueries({ queryKey: ['status'] })
  }
  const update = useMutation({ mutationFn: ({ id, status }: { id: number; status: AppStatus }) => api.updateApplication(id, { status }), onSuccess: refresh })
  const remove = useMutation({ mutationFn: api.deleteApplication, onSuccess: refresh })
  const add = useMutation({
    mutationFn: () => api.track({ ...form, status: 'applied' }),
    onSuccess: () => {
      setForm({ company: '', title: '', url: '' })
      setAdding(false)
      refresh()
    },
  })

  const all = apps.data ?? []
  const counts = Object.fromEntries(APP_STATUSES.map((s) => [s, all.filter((a) => a.status === s).length]))
  const shown = statusFilter === 'all' ? all : all.filter((a) => a.status === statusFilter)
  const active = all.filter((a) => ['applied', 'assessment', 'interviewing', 'offer'].includes(a.status)).length

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setStatusFilter('all')}
          className={`rounded-full px-3 py-1 text-xs font-medium ${statusFilter === 'all' ? 'bg-slate-900 text-white' : 'bg-white text-slate-600 ring-1 ring-slate-200'}`}
        >
          All {all.length}
        </button>
        {APP_STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${statusFilter === s ? 'ring-2 ring-slate-900' : ''} ${STATUS_STYLES[s]}`}
          >
            {s} {counts[s]}
          </button>
        ))}
        <span className="ml-auto text-sm text-slate-500">{active} active</span>
        <button onClick={() => setAdding(!adding)} className="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white">
          + Add a job from elsewhere
        </button>
      </div>

      {adding && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate()
          }}
          className="flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 bg-white p-3"
        >
          {(['company', 'title', 'url'] as const).map((field) => (
            <label key={field} className="text-sm">
              <span className="mb-1 block capitalize text-slate-600">{field === 'url' ? 'Link (optional)' : field}</span>
              <input
                required={field !== 'url'}
                value={form[field]}
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                className="rounded-md border border-slate-300 px-2 py-1.5"
              />
            </label>
          ))}
          <button disabled={add.isPending} className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            Add as applied
          </button>
          {add.isError && <span className="text-sm text-red-600">{(add.error as Error).message}</span>}
        </form>
      )}

      {apps.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
      {apps.data && all.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-10 text-center text-sm text-slate-500">
          Nothing tracked yet. On the Feed, click <b>Applied</b> on a job to add it here.
        </p>
      )}

      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Job</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Applied</th>
                <th className="px-3 py-2">Contact</th>
                <th className="px-3 py-2">Notes</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 align-top">
              {shown.map((a) => (
                <tr key={a.id}>
                  <td className="px-3 py-2">
                    <div className="font-medium">
                      {a.url ? (
                        <a href={a.url} target="_blank" rel="noreferrer" className="hover:underline">
                          {a.title}
                        </a>
                      ) : (
                        a.title
                      )}
                    </div>
                    <div className="text-xs text-slate-500">
                      {a.company}
                      {a.location && ` · ${a.location}`}
                      {a.job_closed && <span className="ml-1 text-amber-700">· posting closed</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <select
                      value={a.status}
                      onChange={(e) => update.mutate({ id: a.id, status: e.target.value as AppStatus })}
                      className={`rounded-md border-0 py-1 pl-2 pr-6 text-xs font-medium capitalize ${STATUS_STYLES[a.status]}`}
                    >
                      {APP_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-600">{a.applied_at ? formatDateTime(a.applied_at) : '—'}</td>
                  <td className="px-3 py-2">
                    <ContactCell app={a} />
                  </td>
                  <td className="w-full px-3 py-2">
                    <NotesCell app={a} />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => confirm(`Remove "${a.title}" at ${a.company} from your tracker?`) && remove.mutate(a.id)}
                      className="text-xs text-slate-400 hover:text-red-600"
                      title="Remove from tracker"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
