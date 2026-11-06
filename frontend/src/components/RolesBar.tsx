import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'

/** The roles "For you" shows: the user's own list, plus related roles they can add with one click. */
export default function RolesBar() {
  const queryClient = useQueryClient()
  const prefs = useQuery({ queryKey: ['prefs'], queryFn: api.prefs })
  const roles = prefs.data?.include_keywords ?? []
  const suggestions = useQuery({
    queryKey: ['role-suggestions', roles],
    queryFn: () => api.roleSuggestions(roles),
    enabled: roles.length > 0,
    staleTime: 10 * 60_000,
  })
  const [draft, setDraft] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const save = useMutation({
    mutationFn: (include_keywords: string[]) => api.savePrefs({ ...prefs.data!, include_keywords }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['prefs'], saved)
      queryClient.invalidateQueries({ queryKey: ['role-suggestions'] })
      // The server rechecks every job's title in the background (about a minute): reload as it goes.
      setRefreshing(true)
      const reload = () => ['jobs', 'fresh-counts'].forEach((key) => queryClient.invalidateQueries({ queryKey: [key] }))
      setTimeout(reload, 15_000)
      setTimeout(() => {
        reload()
        setRefreshing(false)
      }, 60_000)
    },
  })
  const setRoles = (next: string[]) => save.mutate([...new Set(next.map((r) => r.trim().toLowerCase()).filter(Boolean))])
  const add = (role: string) => !roles.includes(role.toLowerCase()) && setRoles([...roles, role])
  const shown = showAll ? roles : roles.slice(0, 14)

  if (!prefs.data) return null
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Your roles</span>
        {shown.map((r) => (
          <span key={r} className="inline-flex items-center gap-1 rounded-full bg-emerald-600 py-0.5 pl-2.5 pr-1 text-xs font-medium text-white">
            {r}
            <button onClick={() => setRoles(roles.filter((x) => x !== r))} className="rounded-full px-1 hover:bg-emerald-700" aria-label={`Remove ${r}`} title="Remove">
              ✕
            </button>
          </span>
        ))}
        {roles.length > 14 && (
          <button onClick={() => setShowAll(!showAll)} className="text-xs font-medium text-emerald-700 hover:underline">
            {showAll ? 'Show fewer' : `+${roles.length - 14} more`}
          </button>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (draft.trim()) add(draft)
            setDraft('')
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="+ Add a role"
            className="w-32 rounded-full border border-dashed border-slate-300 bg-white px-2.5 py-0.5 text-xs outline-none focus:w-44 focus:border-emerald-500"
          />
        </form>
        {(save.isPending || refreshing) && <span className="text-xs text-slate-500">Updating your feed… (about a minute)</span>}
      </div>
      {(suggestions.data?.length ?? 0) > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-slate-500">Related roles you could add</span>
          {suggestions.data!.slice(0, 16).map((s) => (
            <button
              key={s.role}
              onClick={() => add(s.role)}
              disabled={save.isPending}
              className="rounded-full bg-white px-2.5 py-0.5 text-xs text-slate-700 ring-1 ring-slate-200 hover:text-emerald-800 hover:ring-emerald-400 disabled:opacity-50"
              title={`Add "${s.role}" to your roles`}
            >
              + {s.role} <span className="text-slate-400">{s.open_jobs > 2_000 ? '2k+' : s.open_jobs.toLocaleString()}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
