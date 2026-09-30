"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { STATUS_LABEL } from "@/components/jobs/status";
import { SiteCard } from "@/components/sites/SiteCard";
import { SiteForm } from "@/components/sites/SiteForm";
import type { Posting, Site } from "@/components/sites/types";
import { Badge, Card, Notice, SectionTitle, Select } from "@/components/ui";
import { call } from "@/lib/client";
import type { Job, JobStatus } from "@/lib/types";

const STEPS: { label: string; done: (j: Job) => boolean }[] = [
  { label: "Found", done: () => true },
  { label: "Reading JD", done: (j) => j.status !== "detected" },
  { label: "Resume ready", done: (j) => ["resume_ready", "awaiting_approval", "applied", "shortlisted", "rejected", "offer"].includes(j.status) },
  { label: "Approved", done: (j) => ["applied", "shortlisted", "rejected", "offer"].includes(j.status) },
  { label: "Applied", done: (j) => ["applied", "shortlisted", "rejected", "offer"].includes(j.status) },
];

export default function SitesPage() {
  const [tab, setTab] = useState<"search" | "apply">("search");
  const [sites, setSites] = useState<Site[]>([]);
  const [postings, setPostings] = useState<Posting[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, p, j] = await Promise.all([call<Site[]>("/sites"), call<Posting[]>("/sites/postings?limit=200"), call<Job[]>("/jobs")]);
      setSites(s);
      setPostings(p);
      setJobs(j.filter((x) => x.source === ("site" as Job["source"])));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [load]);

  const shown = postings.filter((p) => filter === "all" || (filter === "matched" ? p.matched : !p.matched));
  const tabBtn = (id: "search" | "apply", label: string, n: number) => (
    <button onClick={() => setTab(id)}
      className={`rounded-md px-3 py-1.5 text-sm ${tab === id ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"}`}>
      {label} <span className="ml-1 text-xs opacity-70">{n}</span>
    </button>
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Job websites</h1>
        <p className="text-sm text-zinc-500">
          <b>1. Searching</b>: the agent checks each website on your schedule and keeps only jobs in your categories.{" "}
          <b>2. Applying</b>: each match is opened, its full JD (and PDFs) read, a tailored resume prepared, and it applies after your approval.
        </p>
      </header>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="flex gap-1">{tabBtn("search", "1. Searching", sites.length)}{tabBtn("apply", "2. Applying", jobs.length)}</div>

      {tab === "search" ? (
        <>
          <section className="space-y-3">
            <SectionTitle title="Websites" />
            {sites.map((s) => <SiteCard key={s.id} site={s} onChange={load} />)}
            <SiteForm onDone={load} />
          </section>
          <section>
            <div className="mb-2 flex items-end gap-2">
              <SectionTitle title="What the search found" subtitle="Every posting seen, with why it was kept or skipped" />
              <Select className="ml-auto w-36" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter postings">
                <option value="all">All</option><option value="matched">Matched</option><option value="skipped">Skipped</option>
              </Select>
            </div>
            <Card className="overflow-x-auto p-0">
              {shown.length === 0 ? <p className="p-3 text-sm text-zinc-500">Nothing yet. Add a website and click “Search now”.</p> : (
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800">
                    <tr><th className="px-3 py-2">Job</th><th className="px-3 py-2">Website</th><th className="px-3 py-2">Result</th><th className="px-3 py-2">Found</th></tr>
                  </thead>
                  <tbody>
                    {shown.map((p) => (
                      <tr key={p.id} className="border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800">
                        <td className="px-3 py-2">
                          <a href={p.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">{p.title ?? p.url}</a>
                          {p.company && <p className="text-xs text-zinc-500">{p.company}</p>}
                        </td>
                        <td className="px-3 py-2 text-xs">{p.site_name}</td>
                        <td className="px-3 py-2 text-xs">
                          {p.matched ? <Badge tone="good">match: {p.matched_category ?? "all roles"}</Badge> : <Badge>skipped</Badge>}
                          {p.skip_reason && <p className="mt-0.5 text-zinc-500">{p.skip_reason}</p>}
                          {p.job_id && <p className="mt-0.5"><Link className="underline" href={`/jobs/${p.job_id}`}>job #{p.job_id}</Link>{p.job_status ? ` · ${STATUS_LABEL[p.job_status as JobStatus] ?? p.job_status}` : ""}</p>}
                        </td>
                        <td className="px-3 py-2 text-xs tabular-nums">{new Date(p.discovered_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </section>
        </>
      ) : (
        <section className="space-y-3">
          <SectionTitle title="Applying" subtitle="Matched jobs moving through: open the posting and read the JD (+PDFs) → tailored resume → your approval → apply" />
          {jobs.length === 0 && <Notice>No matched jobs yet. They appear here as soon as a search finds one.</Notice>}
          {jobs.map((j) => (
            <Card key={j.id} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/jobs/${j.id}`} className="font-medium hover:underline">{j.role ?? "Job"}</Link>
                <span className="text-xs text-zinc-500">{j.company ?? ""}</span>
                <Badge>{STATUS_LABEL[j.status]}</Badge>
                {j.ats_score != null && <Badge tone={j.ats_score >= 80 ? "good" : "warn"}>ATS {j.ats_score.toFixed(0)}</Badge>}
                {j.active_run && <Badge tone="warn">{j.active_run.kind === "scrape" ? "reading the job page" : j.active_run.kind === "apply" ? "applying" : "writing resume"}…</Badge>}
                <Link href={`/jobs/${j.id}`} className="ml-auto text-xs underline">open</Link>
              </div>
              <ol className="flex flex-wrap items-center gap-1 text-xs">
                {STEPS.map((s, i) => (
                  <li key={s.label} className="flex items-center gap-1">
                    <span className={`rounded-full px-2 py-0.5 ${s.done(j) ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800"}`}>{s.label}</span>
                    {i < STEPS.length - 1 && <span className="text-zinc-400">→</span>}
                  </li>
                ))}
              </ol>
              {j.status_reason && <p className="text-xs text-zinc-500">{j.status_reason}</p>}
            </Card>
          ))}
        </section>
      )}
    </div>
  );
}
