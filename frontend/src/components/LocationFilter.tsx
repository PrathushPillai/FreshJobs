import { useState } from 'react'
import type { Facet } from '../api'

const SHOWN = 200

/** A searchable checklist of places, the ones picked first. */
export function FacetList({ options, selected, onChange, loading, placeholder }: {
  options: Facet[] | undefined
  selected: string[]
  onChange: (values: string[]) => void
  loading?: boolean
  placeholder: string
}) {
  const [search, setSearch] = useState('')
  const needle = search.trim().toLowerCase()
  const counts = new Map((options ?? []).map((o) => [o.name, o.count]))
  const rest = (options ?? []).filter((o) => !selected.includes(o.name) && (!needle || o.name.toLowerCase().includes(needle)))
  const toggle = (name: string) => onChange(selected.includes(name) ? selected.filter((v) => v !== name) : [...selected, name])
  const row = (name: string, checked: boolean) => (
    <label key={name} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm text-slate-700 hover:bg-slate-100">
      <input type="checkbox" className="accent-brand-600" checked={checked} onChange={() => toggle(name)} />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {counts.has(name) && <span className="text-xs text-slate-400">{counts.get(name)!.toLocaleString()}</span>}
    </label>
  )
  return (
    <div className="w-72 max-w-[calc(100vw-3rem)]">
      <input
        autoFocus
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={placeholder}
        className="mb-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm outline-none focus:border-brand-500"
      />
      <div className="max-h-72 overflow-y-auto">
        {selected.map((name) => row(name, true))}
        {selected.length > 0 && rest.length > 0 && <hr className="my-1 border-slate-100" />}
        {rest.slice(0, SHOWN).map((o) => row(o.name, false))}
        {loading && !options && <p className="px-2 py-1 text-sm text-slate-500">Loading places…</p>}
        {!loading && options && rest.length === 0 && selected.length === 0 && <p className="px-2 py-1 text-sm text-slate-500">No matches</p>}
        {rest.length > SHOWN && <p className="px-2 py-1 text-xs text-slate-400">Type to see more ({(rest.length - SHOWN).toLocaleString()} not shown)</p>}
      </div>
    </div>
  )
}
