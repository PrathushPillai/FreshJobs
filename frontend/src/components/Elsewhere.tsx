import { useState } from 'react'
import { Toggle, PageHeader } from './ui'

/** Saved by the Feed, so this tab opens with whatever was searched last. */
export const LAST_QUERY_KEY = 'freshjobs.lastQuery'

function lastQuery() {
  try {
    return localStorage.getItem(LAST_QUERY_KEY) || 'data analyst'
  } catch {
    return 'data analyst'
  }
}

const enc = encodeURIComponent
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

interface LinkGroup {
  title: string
  note: string
  links: { label: string; hint: string; url: string }[]
}

function groups(role: string, location: string, visa: boolean): LinkGroup[] {
  const r = role.trim() || 'data analyst'
  const loc = location.trim() || 'United States'
  const visaWords = visa ? ' "visa sponsorship" OR "H1B" OR "H-1B"' : ''
  const li = (params: string) => `https://www.linkedin.com/jobs/search/?keywords=${enc(r)}&location=${enc(loc)}&sortBy=DD&${params}`
  return [
    {
      title: 'LinkedIn jobs',
      note: 'Newest first, including Easy Apply jobs that never reach company boards.',
      links: [
        { label: 'Posted in the past hour', hint: 'Be one of the first applicants', url: li('f_TPR=r3600') },
        { label: 'Past hour · Easy Apply only', hint: 'Jobs posted straight to LinkedIn', url: li('f_TPR=r3600&f_AL=true') },
        { label: 'Past 24 hours · entry level', hint: 'Internship, entry and associate', url: li('f_TPR=r86400&f_E=1%2C2%2C3') },
        { label: 'Past 24 hours · remote', hint: 'Remote roles only', url: li('f_TPR=r86400&f_WT=2') },
        { label: 'Past week · under 10 applicants', hint: 'LinkedIn\'s "Under 10 applicants" filter', url: li('f_TPR=r604800&f_JIYN=true') },
      ],
    },
    {
      title: 'Hiring posts by recruiters and employees',
      note: 'Many roles are announced in a post ("We\'re hiring a…") before, or instead of, a job listing. Message the poster directly.',
      links: [
        {
          label: 'LinkedIn posts · past 24 hours',
          hint: '"hiring" + your role, newest first',
          url: `https://www.linkedin.com/search/results/content/?keywords=${enc(`"hiring" "${r}"${visaWords}`)}&datePosted=${enc('"past-24h"')}&sortBy=${enc('"date_posted"')}`,
        },
        {
          label: 'LinkedIn posts · past week',
          hint: 'Wider net',
          url: `https://www.linkedin.com/search/results/content/?keywords=${enc(`"hiring" "${r}"${visaWords}`)}&datePosted=${enc('"past-week"')}&sortBy=${enc('"date_posted"')}`,
        },
        {
          label: 'Google: LinkedIn hiring posts · past day',
          hint: 'Works without signing in',
          url: `https://www.google.com/search?q=${enc(`site:linkedin.com/posts ("hiring" OR "we're hiring") "${r}"`)}&tbs=qdr:d`,
        },
        {
          label: 'X (Twitter): live "hiring" posts',
          hint: 'Startup founders often post here first',
          url: `https://x.com/search?q=${enc(`("hiring" OR "we're hiring") "${r}"`)}&f=live`,
        },
      ],
    },
    {
      title: 'Other job sites',
      note: 'Each search is already filtered to the newest postings.',
      links: [
        { label: 'Indeed · past 24 hours', hint: 'Sorted by date', url: `https://www.indeed.com/jobs?q=${enc(r)}&l=${enc(loc)}&fromage=1&sort=date` },
        { label: 'Glassdoor · past day', hint: '', url: `https://www.glassdoor.com/Job/jobs.htm?sc.keyword=${enc(r)}&locKeyword=${enc(loc)}&fromAge=1` },
        // Google Jobs (udm=8) dropped the old date chip (htichips); it reads "since yesterday" in the query instead.
        { label: 'Google Jobs · past day', hint: 'Indexes most job boards', url: `https://www.google.com/search?q=${enc(`${r} jobs ${loc} since yesterday`)}&udm=8` },
        // Wellfound ignores ?query=; its role pages (/role/data-analyst) are the search.
        { label: 'Wellfound (startups)', hint: 'Startup roles, many with salary and equity', url: `https://wellfound.com/role/${slug(r)}` },
        // hnhiring.com searches only this month's "Who is hiring?" thread; Algolia matched every comment.
        { label: 'Hacker News: who is hiring', hint: 'This month\'s "Who is hiring?" thread', url: `https://hnhiring.com/search?q=${enc(r)}` },
      ],
    },
  ]
}

export default function Elsewhere() {
  const [role, setRole] = useState(lastQuery)
  const [location, setLocation] = useState('United States')
  const [visa, setVisa] = useState(false)

  return (
    <div className="space-y-4">
      <PageHeader icon="🔗" title="LinkedIn & more">
        One click to LinkedIn, Indeed, Glassdoor, Google Jobs and X, already filtered to the newest posts for your role.
      </PageHeader>
      <div className="rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70 p-4 shadow-sm">
        <h2 className="font-display text-lg font-bold">LinkedIn, hiring posts and other sites</h2>
        <p className="mt-1 text-sm text-slate-600">
          FreshJobs pulls jobs from company boards, but LinkedIn doesn't allow other sites to collect its listings or posts. These buttons
          open LinkedIn's own search (and other sites') already filtered to the newest results for your role, in your signed-in browser.
          LinkedIn-only jobs found through Google also appear in your Feed (source "Google Jobs").
        </p>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Role, e.g. data analyst" className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500" />
          <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Location" className="min-w-0 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500 sm:w-56" />
          <Toggle label="Posts mentioning visa sponsorship" checked={visa} onChange={setVisa} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {groups(role, location, visa).map((g) => (
          <section key={g.title} className="rounded-3xl bg-white shadow-sm ring-1 ring-slate-200/70 p-4">
            <h3 className="font-semibold">{g.title}</h3>
            <p className="mb-3 mt-0.5 text-xs text-slate-500">{g.note}</p>
            <ul className="space-y-2">
              {g.links.map((l) => (
                <li key={l.label}>
                  <a href={l.url} target="_blank" rel="noreferrer" className="block rounded-lg border border-slate-200 px-3 py-2 hover:border-brand-400 hover:bg-brand-50">
                    <span className="text-sm font-medium text-slate-900">{l.label} ↗</span>
                    {l.hint && <span className="block text-xs text-slate-500">{l.hint}</span>}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  )
}
