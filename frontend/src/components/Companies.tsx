import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, timeAgo } from '../api'
import { PageHeader } from './ui'

const ATS_HINTS: Record<string, string> = {
  jsearch: 'a Google Jobs search, e.g. query=data analyst jobs in Texas&date_posted=today (uses your JSearch quota)',
  greenhouse: 'the name in boards.greenhouse.io/<slug> or job-boards.greenhouse.io/<slug>',
  lever: 'the name in jobs.lever.co/<slug>',
  ashby: 'the name in jobs.ashbyhq.com/<slug>',
  workday: 'tenant|wdN|site from <tenant>.<wdN>.myworkdayjobs.com/<site>, e.g. nvidia|wd5|NVIDIAExternalCareerSite',
  smartrecruiters: 'the company id in jobs.smartrecruiters.com/<slug> (case-sensitive)',
  workable: 'the account in apply.workable.com/<slug>',
  amazon: 'amazon.jobs search filters as a query string, e.g. country=USA (empty = every job)',
  microsoft: 'a location filter, e.g. United States (empty = every job)',
  google: 'results-page filters as a query string, e.g. location=United States',
  apple: 'a location id, e.g. postLocation-USA (empty = every job)',
  oracle_hcm: 'host|siteNumber[|locationId=N] from <host>/hcmUI/CandidateExperience/en/sites/<siteNumber>, e.g. jpmc.fa.oraclecloud.com|CX_1001',
  icims: 'the portal in careers-<slug>.icims.com, or a full host such as uscareers-msci.icims.com',
  eightfold: 'tenant|domain[|location] from <tenant>.eightfold.ai, e.g. qualcomm|qualcomm.com|United States',
  successfactors: 'host[|filters] of the career site, e.g. careers.ey.com|optionsFacetsDD_country=US',
  rippling: 'the board in ats.rippling.com/<slug>/jobs',
  gem: 'the board in jobs.gem.com/<slug>',
  polymer: 'the board in jobs.polymer.co/<slug>',
  bamboohr: 'the subdomain in <slug>.bamboohr.com/careers',
  recruitee: 'the subdomain in <slug>.recruitee.com',
  breezy: 'the subdomain in <slug>.breezy.hr',
  pinpoint: 'the subdomain in <slug>.pinpointhq.com',
  simplify: 'a SimplifyJobs GitHub repo, e.g. SimplifyJobs/New-Grad-Positions',
}

export default function Companies() {
  const queryClient = useQueryClient()
  const companies = useQuery({ queryKey: ['companies'], queryFn: api.companies })
  const [form, setForm] = useState({ name: '', ats: 'greenhouse', slug: '' })
  const add = useMutation({
    mutationFn: api.addCompany,
    onSuccess: () => {
      setForm({ name: '', ats: form.ats, slug: '' })
      queryClient.invalidateQueries({ queryKey: ['companies'] })
    },
  })
  const [url, setUrl] = useState('')
  const fromUrl = useMutation({
    mutationFn: api.addCompanyFromUrl,
    onSuccess: () => {
      setUrl('')
      queryClient.invalidateQueries({ queryKey: ['companies'] })
    },
  })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api.setCompanyEnabled(id, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['companies'] }),
  })

  return (
    <div className="space-y-5">
      <PageHeader icon="🛰️" title="Sources">
        The company job boards and feeds FreshJobs checks every few minutes. Paste any careers page to add a company.
      </PageHeader>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          fromUrl.mutate(url)
        }}
        className="space-y-2 rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70 p-4"
      >
        <label className="block text-sm font-medium" htmlFor="careers-url">
          Add a company from its careers page
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="careers-url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a careers page or job link, e.g. careers.jpmorgan.com or jobs.lever.co/acme/123"
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
          <button disabled={fromUrl.isPending} className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
            {fromUrl.isPending ? 'Detecting…' : 'Detect and add'}
          </button>
        </div>
        <p className="text-xs text-slate-500">
          Works with Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, iCIMS, Oracle, Eightfold and SuccessFactors boards, even when they're embedded in a company's own site.
        </p>
        {fromUrl.isSuccess && (
          <p className="text-sm text-brand-700">
            {fromUrl.data.added ? 'Added' : 'Already tracking'} <b>{fromUrl.data.company.name}</b> ({fromUrl.data.company.ats}/{fromUrl.data.company.slug}).
            {fromUrl.data.added && ' Its jobs appear after the next check.'}
          </p>
        )}
        {fromUrl.isError && <p className="text-sm text-red-600">{(fromUrl.error as Error).message}</p>}
      </form>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          add.mutate(form)
        }}
        className="flex flex-wrap items-end gap-3 rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70 p-4"
      >
        <label className="text-sm">
          <span className="mb-1 block text-slate-600">Company</span>
          <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="rounded-md border border-slate-300 px-2 py-1.5" />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-600">ATS</span>
          <select value={form.ats} onChange={(e) => setForm({ ...form, ats: e.target.value })} className="rounded-md border border-slate-300 px-2 py-1.5">
            {Object.keys(ATS_HINTS).map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-slate-600">Board slug</span>
          <input required value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} className="rounded-md border border-slate-300 px-2 py-1.5" />
        </label>
        <button disabled={add.isPending} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          Add company
        </button>
        <p className="w-full text-xs text-slate-500">
          Slug: {ATS_HINTS[form.ats]}. It's picked up on the next check.
          {add.isError && <span className="ml-2 text-red-600">{(add.error as Error).message}</span>}
        </p>
      </form>

      <div className="overflow-x-auto rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Company</th>
              <th className="px-4 py-2">Source</th>
              <th className="px-4 py-2 text-right">Open jobs</th>
              <th className="px-4 py-2">Last check</th>
              <th className="px-4 py-2">Polling</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {companies.data?.map((c) => (
              <tr key={c.id} className={c.enabled ? '' : 'opacity-50'}>
                <td className="px-4 py-2 font-medium">{c.name}</td>
                <td className="px-4 py-2 text-slate-600">
                  {c.ats}/{c.slug}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{c.open_jobs.toLocaleString()}</td>
                <td className={`px-4 py-2 ${c.last_status?.startsWith('error') ? 'text-red-600' : 'text-slate-600'}`}>
                  {c.last_polled_at ? `${timeAgo(c.last_polled_at)} · ${c.last_status}` : 'not yet'}
                </td>
                <td className="px-4 py-2">
                  <input type="checkbox" className="accent-brand-600" checked={c.enabled} onChange={(e) => toggle.mutate({ id: c.id, enabled: e.target.checked })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
