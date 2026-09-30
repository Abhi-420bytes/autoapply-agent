"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { IconAlert, IconBriefcase, IconCheck, IconClock, IconPlus, IconSearch, IconSparkles } from "@/components/icons";
import { LevelBadge } from "@/components/jobs/LevelBadge";
import { ALL_STATUSES, STATUS_LABEL, STATUS_TONE } from "@/components/jobs/status";
import { Avatar, Badge, Button, Card, EmptyState, Input, Notice, PageHeader, Select, StatCard } from "@/components/ui";
import { call } from "@/lib/client";
import type { Job, JobStatus } from "@/lib/types";
import { cn, timeAgo } from "@/lib/utils";

type View = "all" | "needs_you" | "ready" | "applied" | "problems";

const VIEWS: Record<View, { label: string; match: (j: Job) => boolean }> = {
  all: { label: "All", match: () => true },
  needs_you: { label: "Needs you", match: (j) => j.status === "awaiting_approval" || j.status === "paused" || j.status === "suspicious" },
  ready: { label: "Resume ready", match: (j) => j.status === "resume_ready" },
  applied: { label: "Applied", match: (j) => ["applied", "shortlisted", "offer"].includes(j.status) },
  problems: { label: "Problems", match: (j) => j.status === "failed" },
};

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [view, setView] = useState<View>("all");
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setJobs(await call<Job[]>("/jobs"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 10_000);
    return () => clearInterval(t);
  }, [load]);

  async function patch(id: number, body: Partial<Pick<Job, "status" | "notes">>) {
    await call(`/jobs/${id}`, { method: "PATCH", json: body });
    await load();
  }

  const all = useMemo(() => jobs ?? [], [jobs]);
  const count = (v: View) => all.filter(VIEWS[v].match).length;
  const shown = all.filter((j) => {
    if (!VIEWS[view].match(j)) return false;
    if (level && (level === "unknown" ? j.level : j.level !== level)) return false;
    const needle = q.trim().toLowerCase();
    return !needle || `${j.company ?? ""} ${j.role ?? ""}`.toLowerCase().includes(needle);
  });
  const working = all.filter((j) => j.active_run).length;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<IconBriefcase size={20} />}
        title="Jobs"
        subtitle="Every job from your email, job websites, careers pages and manual resumes, with its tailored resume and where it stands."
        actions={
          <Link href="/jobs/new">
            <Button><IconPlus size={16} />New resume from JD</Button>
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="All jobs" value={all.length} hint={working ? `${working} being worked on` : "none in progress"} icon={<IconBriefcase size={16} />} active={view === "all"} onClick={() => setView("all")} />
        <StatCard label="Needs you" value={count("needs_you")} hint="approve or answer" tone="warn" icon={<IconClock size={16} />} active={view === "needs_you"} onClick={() => setView("needs_you")} />
        <StatCard label="Resume ready" value={count("ready")} hint="apply or approve" tone="accent" icon={<IconSparkles size={16} />} active={view === "ready"} onClick={() => setView("ready")} />
        <StatCard label="Applied" value={count("applied")} hint={count("problems") ? `${count("problems")} with problems` : "keep going"} tone="good" icon={<IconCheck size={16} />} active={view === "applied"} onClick={() => setView("applied")} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1.5">
          {(Object.keys(VIEWS) as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition-colors",
                view === v
                  ? "bg-brand-600 text-white ring-brand-600"
                  : "bg-white text-zinc-600 ring-zinc-200 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700",
              )}
            >
              {VIEWS[v].label} <span className="opacity-70">{count(v)}</span>
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <div className="relative">
            <IconSearch size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400" />
            <Input className="w-56 pl-8" placeholder="Search company or role" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search jobs" />
          </div>
          <Select className="w-44" value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Filter by level">
            <option value="">All levels</option>
            <option value="fresher">Fresher</option>
            <option value="experienced">Experienced</option>
            <option value="unknown">Level not stated</option>
          </Select>
        </div>
      </div>

      {error && <Notice tone="bad">{error}</Notice>}
      {jobs && all.length === 0 && (
        <EmptyState icon={<IconBriefcase size={18} />} title="No jobs yet">
          Connect your email or add a job website, or paste a job description to make your first tailored resume.
        </EmptyState>
      )}
      {jobs && all.length > 0 && shown.length === 0 && <EmptyState title="Nothing matches these filters" />}
      {shown.length > 0 && (
        <Card className="divide-y divide-zinc-100 p-0 dark:divide-zinc-800">
          {shown.map((j) => <JobRow key={j.id} job={j} onPatch={patch} />)}
        </Card>
      )}
    </div>
  );
}

function AtsScore({ score }: { score: number | null }) {
  if (score === null) return <span className="text-xs text-zinc-400">–</span>;
  const tone = score >= 80 ? "text-emerald-700 bg-emerald-50 ring-emerald-200" : score >= 60 ? "text-amber-800 bg-amber-50 ring-amber-200" : "text-red-700 bg-red-50 ring-red-200";
  return (
    <span title="ATS score" className={cn("inline-grid h-9 w-9 place-items-center rounded-full text-xs font-semibold tabular-nums ring-1 ring-inset dark:bg-transparent", tone)}>
      {score.toFixed(0)}
    </span>
  );
}

const SOURCE_LABEL: Record<string, string> = { email: "Email", manual: "Manual", site: "Job site" };

function JobRow({ job: j, onPatch }: { job: Job; onPatch: (id: number, b: Partial<Job>) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [notes, setNotes] = useState(j.notes ?? "");
  const tone = STATUS_TONE[j.status];
  return (
    <div className="group flex flex-wrap items-center gap-4 px-5 py-4 transition-colors hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 md:flex-nowrap">
      <Avatar name={j.company ?? j.role} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/jobs/${j.id}`} className="truncate font-medium hover:text-brand-700 dark:hover:text-brand-300">
            {j.company ?? "Unknown company"}
          </Link>
          <LevelBadge level={j.level} reason={j.level_reason} />
          <Badge>{SOURCE_LABEL[j.source] ?? j.source}</Badge>
          {j.active_run && (
            <Badge tone="accent" dot className="animate-pulse">
              {j.active_run.state === "queued" ? "queued" : j.active_run.step ?? "working"}
            </Badge>
          )}
        </div>
        <p className="truncate text-sm text-zinc-500">{j.role ?? "role pending analysis"}{j.location ? ` · ${j.location}` : ""}</p>
        {j.status_reason && (
          <p title={j.status_reason} className={cn("mt-0.5 flex items-center gap-1 truncate text-xs", tone === "bad" ? "text-red-600" : "text-zinc-500")}>
            {tone === "bad" && <IconAlert size={12} className="shrink-0" />}
            <span className="truncate">{j.status_reason}</span>
          </p>
        )}
        {editing ? (
          <Input
            autoFocus
            className="mt-1.5 h-8 max-w-md text-xs"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => {
              setEditing(false);
              if (notes !== (j.notes ?? "")) void onPatch(j.id, { notes });
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            aria-label={`Notes for job ${j.id}`}
          />
        ) : (
          <button onClick={() => setEditing(true)} className="mt-0.5 max-w-full truncate text-left text-xs text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200" title={j.notes ?? "Add a note"}>
            {j.notes ? `Note: ${j.notes}` : <span className="opacity-0 group-hover:opacity-100">+ add a note</span>}
          </button>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-4">
        <span className="hidden w-20 text-right text-xs text-zinc-500 lg:block" title={new Date(j.detected_at).toLocaleString()}>
          {timeAgo(j.detected_at)}
        </span>
        <AtsScore score={j.ats_score} />
        <Select
          className={cn(
            "h-8 w-40 rounded-full text-xs font-medium",
            tone === "good" && "border-emerald-200 bg-emerald-50 text-emerald-800",
            tone === "warn" && "border-amber-200 bg-amber-50 text-amber-900",
            tone === "bad" && "border-red-200 bg-red-50 text-red-800",
          )}
          value={j.status}
          onChange={(e) => onPatch(j.id, { status: e.target.value as JobStatus })}
          aria-label={`Status of job ${j.id}`}
        >
          {ALL_STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </Select>
      </div>
    </div>
  );
}
