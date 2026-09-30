"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { EmailAndFiles, PromptBox } from "@/components/jobs/EmailAndFiles";
import { LevelBadge } from "@/components/jobs/LevelBadge";
import { ScoreReportView } from "@/components/jobs/ScoreReportView";
import { ALL_STATUSES, STATUS_LABEL, STATUS_TONE, STEP_LABEL } from "@/components/jobs/status";
import { IconArrowLeft, IconCheck, IconDownload, IconExternal, IconFile, IconSparkles } from "@/components/icons";
import { Avatar, Badge, Button, Card, Input, Notice, SectionTitle, Select } from "@/components/ui";
import { call } from "@/lib/client";
import { timeAgo } from "@/lib/utils";
import type { JobDetail, JobStatus } from "@/lib/types";

const PdfViewer = dynamic(() => import("@/components/PdfViewer"), { ssr: false });

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const j = await call<JobDetail>(`/jobs/${id}`);
      setJob(j);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // poll while a run is active or the agent is waiting for an OTP/CAPTCHA
  useEffect(() => {
    if (!job?.active_run && !job?.prompt) return;
    const t = setTimeout(() => void load(), 3000);
    return () => clearTimeout(t);
  }, [job, load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "good", text: ok });
      await load();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!job) return <p className="text-sm text-zinc-500">Loading…</p>;

  const resume = job.resumes.find((r) => r.id === selected) ?? job.resumes[0];
  const report = job.resumes.find((r) => r.score_report)?.score_report;
  const approved = job.applications.find((a) => a.status === "approved" || a.status === "submitted");
  const pendingAuto = job.applications.find((a) => a.status === "prepared" && a.auto_submit_at);
  const run = job.active_run;
  const lastRun = job.runs[0];

  return (
    <div className="space-y-6">
      <Link href="/jobs" className="inline-flex items-center gap-1 text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">
        <IconArrowLeft size={14} /> All jobs
      </Link>
      <Card className="p-6">
        <div className="flex flex-wrap items-start gap-4">
          <Avatar name={job.company ?? job.role} size={48} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{job.company ?? "Unknown company"}</h1>
              <Badge tone={STATUS_TONE[job.status] === "neutral" ? "accent" : STATUS_TONE[job.status]} dot>{STATUS_LABEL[job.status]}</Badge>
            </div>
            <p className="mt-0.5 text-sm text-zinc-600 dark:text-zinc-300">{job.role ?? "Role pending analysis"}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
              <LevelBadge level={job.level} reason={job.level_reason} />
              <Badge>{job.source === "site" ? "Job site" : job.source === "email" ? "Email" : "Manual"}</Badge>
              {job.location && <Badge>{job.location}</Badge>}
              {job.ctc && <Badge>{job.ctc}</Badge>}
              <span className="ml-1" title={new Date(job.detected_at).toLocaleString()}>found {timeAgo(job.detected_at)}</span>
            </div>
            {job.level_reason && <p className="mt-1.5 text-xs text-zinc-500">{job.level_reason}</p>}
          </div>
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4 dark:border-zinc-800">
          {approved ? (
            <Button variant="outline" onClick={() => act(() => call(`/jobs/${job.id}/approval`, { method: "DELETE" }), "Approval withdrawn.")} disabled={approved.status !== "approved"}>
              {approved.status === "approved" ? "Withdraw approval" : "Submitted"}
            </Button>
          ) : (
            <Button onClick={() => act(() => call(`/jobs/${job.id}/approve`, { method: "POST", json: { resume_id: resume?.id ?? null } }), "Approved. The portal agent will submit this exact version.")} disabled={!resume || !!run}>
              <IconCheck size={16} /> Approve
            </Button>
          )}
          <Button variant="outline" onClick={() => act(() => call(`/jobs/${job.id}/regenerate`, { method: "POST", json: {} }), "Regeneration queued.")} disabled={!!run}>
            <IconSparkles size={15} /> Regenerate
          </Button>
          {resume && <Link href={`/studio/${resume.id}`}><Button variant="outline"><IconFile size={15} /> Edit in studio</Button></Link>}
          {job.apply_url && (
            <a href={job.apply_url} target="_blank" rel="noreferrer noopener">
              <Button variant="ghost"><IconExternal size={15} /> Open posting</Button>
            </a>
          )}
          <span className="flex-1" />
          <Select
            className="w-44"
            value={job.status}
            onChange={(e) => act(() => call(`/jobs/${job.id}`, { method: "PATCH", json: { status: e.target.value as JobStatus } }), "Status updated.")}
            aria-label="Job status"
          >
            {ALL_STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
          </Select>
          <Button variant="ghost" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => window.confirm("Delete this job and its resumes?") && act(async () => { await call(`/jobs/${job.id}`, { method: "DELETE" }); router.push("/jobs"); }, "Deleted.")}>
            Delete
          </Button>
        </div>
      </Card>

      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <PromptBox job={job} onDone={load} />
      {job.status_reason && <Notice tone="warn">{job.status_reason}</Notice>}
      {run && (
        <Notice tone="neutral">
          <span className="inline-flex items-center gap-2">
            <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
            {run.state === "queued" ? "Queued: the worker picks it up within 15 seconds." : run.kind === "scrape" ? "Portal agent: reading the job page" : run.kind === "apply" ? "Portal agent: submitting your application" : STEP_LABEL[run.step ?? ""] ?? "Working…"}
          </span>
        </Notice>
      )}
      {!run && lastRun?.state === "failed" && (
        <Notice tone="bad">
          <span className="block">Last run failed: {lastRun.error}</span>
          {lastRun.kind === "generate" && (
            <Button size="sm" className="mt-2" onClick={() => act(() => call(`/jobs/${job.id}/retry`, { method: "POST" }), "Retrying from where it stopped.")}>
              Retry from where it stopped
            </Button>
          )}
        </Notice>
      )}
      {pendingAuto && (
        <Notice tone="warn">
          <span className="block">
            Applies automatically at {new Date(pendingAuto.auto_submit_at ?? "").toLocaleTimeString()} (
            {Math.max(0, Math.round((new Date(pendingAuto.auto_submit_at ?? "").getTime() - Date.now()) / 60000))} min) unless you stop it.
          </span>
          <span className="mt-2 flex gap-2">
            <Button size="sm" onClick={() => act(() => call(`/jobs/${job.id}/approve`, { method: "POST", json: { resume_id: resume?.id ?? null } }), "Applying now.")}>Apply now</Button>
            <Button size="sm" variant="outline" onClick={() => window.confirm("Don't apply to this job?") && act(() => call(`/jobs/${job.id}/approval`, { method: "DELETE" }), "Cancelled. It won't be applied.")}>Don&apos;t apply</Button>
          </span>
        </Notice>
      )}
      {approved && <Notice tone="good">Approved {new Date(approved.approved_at ?? "").toLocaleString()} (resume #{approved.resume_id}). {approved.status === "approved" ? (job.apply_url && job.portal_id ? "The portal agent is submitting it." : "The agent has no site it's allowed to apply on for this job; see below.") : `Submitted ${approved.submitted_at ? new Date(approved.submitted_at).toLocaleString() : ""}.`}</Notice>}
      {!job.portal_id && job.status !== "applied" && <ApplyWhere job={job} resumeId={resume?.id ?? null} act={act} />}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_600px]">
        <div className="space-y-6">
          {report && (
            <section>
              <SectionTitle title="Score report" />
              <ScoreReportView report={report} onRegenerate={run ? undefined : () => void act(() => call(`/jobs/${job.id}/regenerate`, { method: "POST", json: {} }), "Regeneration queued with your new skills.")} />
            </section>
          )}
          {job.eligibility && (
            <section>
              <SectionTitle title="Eligibility" />
              <Card className="text-sm">
                {job.eligibility.eligible === true && <Badge tone="good">eligible</Badge>}
                {job.eligibility.eligible === false && <Badge tone="bad">not eligible</Badge>}
                {job.eligibility.eligible === null && <Badge tone="warn">can&apos;t tell, complete your profile</Badge>}
                <ul className="mt-2 list-disc pl-5 text-xs text-zinc-600 dark:text-zinc-400">
                  {[...(job.eligibility.reasons ?? []), ...(job.eligibility.unknown ?? [])].map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </Card>
            </section>
          )}
          {job.jd_structured && (
            <section>
              <SectionTitle title="What the job asks for" />
              <Card className="space-y-2 text-sm">
                <p><span className="text-xs text-zinc-500">Required:</span> {(job.jd_structured.required_skills ?? []).join(", ") || "–"}</p>
                <p><span className="text-xs text-zinc-500">Preferred:</span> {(job.jd_structured.preferred_skills ?? []).join(", ") || "–"}</p>
                <p><span className="text-xs text-zinc-500">Keywords:</span> {(job.jd_structured.keywords ?? []).join(", ") || "–"}</p>
              </Card>
            </section>
          )}
          <EmailAndFiles job={job} />
          <section>
            <details>
              <summary className="cursor-pointer text-sm font-medium">Job description</summary>
              <Card className="mt-2 whitespace-pre-wrap text-sm">{job.jd_text}</Card>
            </details>
          </section>
        </div>

        <div className="space-y-3">
          <SectionTitle title="Resume" subtitle={resume ? `Version ${resume.version} · ${resume.kind.replace("_", " ")}` : undefined} />
          {job.resumes.length > 1 && (
            <Select value={resume?.id ?? ""} onChange={(e) => setSelected(Number(e.target.value))} aria-label="Resume version">
              {job.resumes.map((r) => (
                <option key={r.id} value={r.id}>
                  v{r.version} · {r.kind.replace("_", " ")} · {new Date(r.created_at).toLocaleString()}{r.ats_score ? ` · ATS ${r.ats_score.toFixed(0)}` : ""}
                </option>
              ))}
            </Select>
          )}
          {resume ? <PdfViewer path={`/resumes/${resume.id}/pdf`} width={560} /> : <p className="text-sm text-zinc-500">No resume yet.</p>}
        </div>
      </div>
    </div>
  );
}

function ApplyWhere({ job, resumeId, act }: { job: JobDetail; resumeId: number | null; act: (fn: () => Promise<unknown>, ok: string) => Promise<void> }) {
  const [link, setLink] = useState("");
  const host = (() => { try { return job.apply_url ? new URL(job.apply_url).hostname : ""; } catch { return ""; } })();
  const onLinkedIn = host.endsWith("linkedin.com");
  const companySite = !!job.apply_url && !onLinkedIn;
  return (
    <Notice tone={companySite ? "warn" : "neutral"}>
      <span className="block font-medium">
        {companySite ? `This opening is on ${host}.` : onLinkedIn ? "LinkedIn job: the agent never applies on LinkedIn (it's against LinkedIn's rules)." : "No application link for this job."}
      </span>
      {job.notes && <span className="mt-1 block text-xs">{job.notes}</span>}
      <span className="mt-2 flex flex-wrap items-center gap-2">
        {companySite && (
          <Button size="sm" onClick={() => window.confirm(`Allow the agent to open ${host} and apply there (after your approval)?`) && act(() => call(`/jobs/${job.id}/allow-apply-site`, { method: "POST" }), `${host} allowed.`)}>
            Allow {host} and let the agent apply
          </Button>
        )}
        {job.apply_url && (
          <a className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200" href={job.apply_url} target="_blank" rel="noreferrer noopener">
            <IconExternal size={13} />
            {onLinkedIn ? "Open on LinkedIn" : "Open it"}
          </a>
        )}
        {resumeId && (
          <a className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-3 text-xs font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200" href={`/api/backend/resumes/${resumeId}/pdf`} download>
            <IconDownload size={13} />
            Download resume
          </a>
        )}
        {job.company && (
          <Button size="sm" variant="outline" onClick={() => act(() => call(`/jobs/${job.id}/find-apply-page`, { method: "POST" }), "Search finished.")}>
            Search for {job.company}&apos;s application page
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => act(() => call(`/jobs/${job.id}`, { method: "PATCH", json: { status: "applied" } }), "Marked as applied.")}>
          I applied myself
        </Button>
      </span>
      <span className="mt-2 flex gap-2">
        <Input className="h-8 text-xs" placeholder="Know the company's own application page? Paste the link and the agent applies there" value={link} onChange={(e) => setLink(e.target.value)} />
        <Button size="sm" disabled={!link} onClick={() => act(async () => { await call(`/jobs/${job.id}/apply-link`, { method: "POST", json: { url: link } }); setLink(""); }, "Link saved. The agent applies there once you approve (or now, if already approved).")}>
          Use link
        </Button>
      </span>
    </Notice>
  );
}
