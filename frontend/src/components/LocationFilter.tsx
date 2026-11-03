import { useQuery } from '@tanstack/react-query'
import { api, type Facet } from '../api'

interface Props {
  countries: string[]
  states: string[]
  cities: string[]
  onChange: (v: { countries: string[]; states: string[]; cities: string[] }) => void
}

function Picker({ label, options, selected, onAdd, disabledHint }: {
  label: string
  options: Facet[]
  selected: string[]
  onAdd: (v: string) => void
  disabledHint?: string
}) {
  const available = options.filter((o) => !selected.includes(o.name))
  return (
    <select
      value=""
      disabled={!!disabledHint}
      title={disabledHint}
      onChange={(e) => e.target.value && onAdd(e.target.value)}
      className="min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm disabled:bg-slate-50 disabled:text-slate-400"
    >
      <option value="">{disabledHint ?? `+ ${label}`}</option>
      {available.map((o) => (
        <option key={o.name} value={o.name}>
          {o.name} ({o.count.toLocaleString()})
        </option>
      ))}
    </select>
  )
}

function Chips({ values, onRemove }: { values: string[]; onRemove: (v: string) => void }) {
  return values.map((v) => (
    <button
      key={v}
      onClick={() => onRemove(v)}
      className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100"
      title="Remove"
    >
      {v} ✕
    </button>
  ))
}

export default function LocationFilter({ countries, states, cities, onChange }: Props) {
  const oneCountry = countries.length === 1 ? countries[0] : undefined
  const oneState = states.length === 1 ? states[0] : undefined
  const facets = useQuery({
    queryKey: ['facets', oneCountry, oneState],
    queryFn: () => api.locationFacets(oneCountry, oneState),
    staleTime: 5 * 60_000,
  })
  const f = facets.data
  const set = (patch: Partial<Props>) => onChange({ countries, states, cities, ...patch })
  const remove = (list: string[], v: string) => list.filter((x) => x !== v)

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm font-medium text-slate-700">Location</span>
      <Picker label="Country" options={f?.countries ?? []} selected={countries} onAdd={(v) => set({ countries: [...countries, v], states: [], cities: [] })} />
      <Picker
        label={oneCountry === 'United States' ? 'State' : 'State / province'}
        options={f?.states ?? []}
        selected={states}
        onAdd={(v) => set({ states: [...states, v], cities: [] })}
        disabledHint={oneCountry ? undefined : 'Pick one country for states'}
      />
      <Picker
        label="City"
        options={f?.cities ?? []}
        selected={cities}
        onAdd={(v) => set({ cities: [...cities, v] })}
        disabledHint={oneCountry ? undefined : 'Pick one country for cities'}
      />
      <Chips values={countries} onRemove={(v) => set({ countries: remove(countries, v), states: [], cities: [] })} />
      <Chips values={states} onRemove={(v) => set({ states: remove(states, v), cities: [] })} />
      <Chips values={cities} onRemove={(v) => set({ cities: remove(cities, v) })} />
    </div>
  )
}
