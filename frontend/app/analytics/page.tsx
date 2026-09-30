"use client";

import { useEffect, useState } from "react";
import { BarChart, HBarChart } from "@/components/charts/BarChart";
import { STATUS_LABEL } from "@/components/jobs/status";
import { Card, Notice, SectionTitle } from "@/components/ui";
import { call } from "@/lib/client";
import type { JobStatus } from "@/lib/types";
import { usd } from "@/lib/utils";

type Analytics = {
  weekly: { week_start: string; applications: number; generated: number }[];
  funnel: Record<string, number>;
  applications_submitted: number;
  with_outcome: number;
  shortlisted_or_offer: number;
  shortlist_rate: number | null;
  avg_ats_score: number | null;
  ats_scores: number[];
  top_gaps: { skill: string; jobs: number }[];
  monthly_cost: { month: string; cost_usd: number; calls: number }[];
  cost_per_job: { job_id: number; label: string; cost_usd: number }[];
  avg_cost_per_resume: number | null;
  top_bullets: { id: number; text: string; outcome_score: number }[];
};

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-xs text-zinc-500">{sub}</p>}
    </Card>
  );
}

const FUNNEL: JobStatus[] = ["detected", "scraped", "resume_ready", "awaiting_approval", "applied", "shortlisted", "offer", "rejected", "ineligible", "suspicious", "failed", "paused"];

export default function AnalyticsPage() {
  const [a, setA] = useState<Analytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    call<Analytics>("/analytics").then(setA).catch((e) => setError(e instanceof Error ? e.message : "failed"));
  }, []);
  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!a) return <p className="text-sm text-zinc-500">Loading…</p>;

  const thisMonth = a.monthly_cost[a.monthly_cost.length - 1];
  const weekLabel = (d: string) => new Date(d + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Analytics</h1>
        <p className="text-sm text-zinc-500">Outcomes feed back into retrieval: bullets from resumes that got shortlisted rank higher next time.</p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Applications submitted" value={String(a.applications_submitted)} sub={`${a.with_outcome} with an outcome`} />
        <Tile label="Shortlist rate" value={a.shortlist_rate === null ? "–" : `${Math.round(a.shortlist_rate * 100)}%`} sub={`${a.shortlisted_or_offer} shortlisted or offer`} />
        <Tile label="Average ATS score" value={a.avg_ats_score?.toFixed(0) ?? "–"} sub={`${a.ats_scores.length} generated resume(s)`} />
        <Tile label="LLM cost this month" value={usd(thisMonth?.cost_usd ?? 0)} sub={a.avg_cost_per_resume === null ? undefined : `${usd(a.avg_cost_per_resume)} per resume`} />
      </div>

      <section>
        <SectionTitle title="Per week" subtitle="Last 12 weeks" />
        <Card>
          <BarChart
            title="Week starting"
            data={a.weekly.map((w) => ({ label: weekLabel(w.week_start), values: [w.applications, w.generated] }))}
            series={[{ name: "Applications", color: "var(--series-1)" }, { name: "Resumes generated", color: "var(--series-2)" }]}
          />
        </Card>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section>
          <SectionTitle title="LLM cost per month" />
          <Card>
            <BarChart title="Month" data={a.monthly_cost.map((m) => ({ label: m.month, values: [m.cost_usd] }))}
              series={[{ name: "Cost", color: "var(--series-1)" }]} format={(v) => usd(v)} height={180} />
          </Card>
        </section>
        <section>
          <SectionTitle title="Top skill gaps" subtitle="Asked for by JDs but not in your data: learn these or build a project" />
          <Card>
            {a.top_gaps.length ? <HBarChart title="Skill gaps" data={a.top_gaps.map((g) => ({ label: g.skill, value: g.jobs }))} format={(v) => `${v} job${v === 1 ? "" : "s"}`} /> : <p className="text-sm text-zinc-500">No gaps yet.</p>}
          </Card>
        </section>
        <section>
          <SectionTitle title="Jobs by status" />
          <Card>
            <HBarChart title="Jobs by status" data={FUNNEL.filter((s) => a.funnel[s]).map((s) => ({ label: STATUS_LABEL[s], value: a.funnel[s] }))} />
            {Object.keys(a.funnel).length === 0 && <p className="text-sm text-zinc-500">No jobs yet.</p>}
          </Card>
        </section>
        <section>
          <SectionTitle title="Cost per job" subtitle="Top 10" />
          <Card>
            {a.cost_per_job.length ? <HBarChart title="Cost per job" data={a.cost_per_job.map((c) => ({ label: c.label, value: c.cost_usd }))} format={(v) => usd(v)} /> : <p className="text-sm text-zinc-500">Nothing yet.</p>}
          </Card>
        </section>
      </div>

      <section>
        <SectionTitle title="What's working" subtitle="Bullets with the best outcome scores (from resumes that were shortlisted or got offers)" />
        <Card>
          {a.top_bullets.length === 0 ? <p className="text-sm text-zinc-500">Mark jobs as Shortlisted, Rejected or Offer on the Jobs page and this starts learning.</p> : (
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              {a.top_bullets.map((b) => <li key={b.id}>{b.text} <span className="text-xs tabular-nums text-zinc-500">({b.outcome_score >= 0 ? "+" : ""}{b.outcome_score.toFixed(2)})</span></li>)}
            </ol>
          )}
        </Card>
      </section>
    </div>
  );
}
