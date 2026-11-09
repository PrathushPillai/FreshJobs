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
  const [showSuggestions, setShowSuggestions] = useState(false)
  const save = useMutation({
    mutationFn: (include_keywords: string[]) => api.savePrefs({ ...prefs.data!, include_keywords }),
    // Show the change at once; the server rechecks just the jobs that mention the role (a second or two).
    onMutate: (include_keywords) => {
      const before = prefs.data
      if (before) queryClient.setQueryData(['prefs'], { ...before, include_keywords })
      return { before }
    },
    onError: (_err, _roles, ctx) => ctx?.before && queryClient.setQueryData(['prefs'], ctx.before),
    onSuccess: (saved) => {
      queryClient.setQueryData(['prefs'], saved)
      queryClient.invalidateQueries({ queryKey: ['role-suggestions'] })
      // The feed is up to date as soon as the save returns: reload it now.
      ;['jobs', 'fresh-counts'].forEach((key) => queryClient.invalidateQueries({ queryKey: [key] }))
    },
  })
  const setRoles = (next: string[]) => save.mutate([...new Set(next.map((r) => r.trim().toLowerCase()).filter(Boolean))])
  const add = (role: string) => !roles.includes(role.toLowerCase()) && setRoles([...roles, role])
  const shown = showAll ? roles : roles.slice(0, 10)
  const extra = (suggestions.data ?? []).length

  if (!prefs.data) return null
  return (
    <div className="rounded-2xl bg-gradient-to-r from-brand-50/80 to-fuchsia-50/40 p-3 ring-1 ring-brand-100">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 inline-flex items-center gap-1.5 font-display text-xs font-bold uppercase tracking-wider text-brand-700">
          ✦ Your roles <span className="rounded-full bg-brand-100 px-1.5 text-[10px] text-brand-700">{roles.length}</span>
        </span>
        {shown.map((r) => (
          <span key={r} className="group inline-flex items-center gap-0.5 rounded-full bg-white py-0.5 pl-2.5 pr-1 text-xs font-semibold text-brand-800 shadow-sm ring-1 ring-brand-200">
            {r}
            <button
              onClick={() => setRoles(roles.filter((x) => x !== r))}
              className="rounded-full px-1 text-brand-300 hover:bg-rose-50 hover:text-rose-600"
              aria-label={`Remove ${r}`}
              title="Remove"
            >
              ✕
            </button>
          </span>
        ))}
        {roles.length > 10 && (
          <button onClick={() => setShowAll(!showAll)} className="rounded-full px-2 py-0.5 text-xs font-semibold text-brand-700 hover:bg-white">
            {showAll ? 'Show fewer' : `+${roles.length - 10} more`}
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
            className="w-28 rounded-full border border-dashed border-brand-300 bg-white/70 px-2.5 py-0.5 text-xs outline-none transition-all focus:w-44 focus:border-brand-500 focus:bg-white"
          />
        </form>
        {extra > 0 && (
          <button
            onClick={() => setShowSuggestions(!showSuggestions)}
            className={`ml-auto rounded-full px-3 py-1 text-xs font-semibold transition ${
              showSuggestions ? 'bg-brand-600 text-white' : 'bg-white text-brand-700 ring-1 ring-brand-200 hover:ring-brand-400'
            }`}
          >
            {showSuggestions ? 'Hide suggestions' : `✦ ${extra} related roles you could add`}
          </button>
        )}
        {save.isPending && (
          <span className="inline-flex items-center gap-1.5 text-xs text-brand-600">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" /> Updating your feed…
          </span>
        )}
      </div>
      {showSuggestions && extra > 0 && (
        <div className="mt-2.5 flex animate-rise flex-wrap items-center gap-1.5 border-t border-brand-100 pt-2.5">
          {suggestions.data!.slice(0, 30).map((s) => (
            <button
              key={s.role}
              onClick={() => add(s.role)}
              disabled={save.isPending}
              className="rounded-full bg-white px-2.5 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-slate-200 transition hover:text-brand-800 hover:ring-brand-400 disabled:opacity-50"
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
