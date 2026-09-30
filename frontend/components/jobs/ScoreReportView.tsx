"use client";

import { useState } from "react";
import { IconCheck, IconPlus } from "@/components/icons";
import { Badge, Button, Card, Notice } from "@/components/ui";
import { call } from "@/lib/client";
import type { ScoreReport } from "@/lib/types";
import { usd } from "@/lib/utils";

function Bar({ label, value, weight }: { label: string; value: number | null; weight: string }) {
  const v = value ?? 0;
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span>
          {label} <span className="text-zinc-500">({weight})</span>
        </span>
        <span className="tabular-nums">{value === null ? "n/a" : v.toFixed(0)}</span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800">
        <div className={v >= 80 ? "h-full bg-emerald-600" : v >= 60 ? "h-full bg-amber-500" : "h-full bg-red-600"} style={{ width: `${Math.min(100, v)}%` }} />
      </div>
    </div>
  );
}

function Terms({ items, tone }: { items: string[]; tone: "good" | "bad" | "warn" | "neutral" }) {
  if (!items.length) return <span className="text-xs text-zinc-500">none</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((t) => <Badge key={t} tone={tone}>{t}</Badge>)}
    </span>
  );
}

type AddState = { added: string[]; busy: string | null; onAdd: (term: string) => void };

/** Missing skills: click one you really have to add it to your profile skills. */
function AddableTerms({ items, tone, s }: { items: string[]; tone: "bad" | "warn"; s: AddState }) {
  if (!items.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((t) => {
        const done = s.added.some((a) => a.toLowerCase() === t.toLowerCase());
        return done ? (
          <Badge key={t} tone="good"><IconCheck size={11} />{t}</Badge>
        ) : (
          <button
            key={t}
            type="button"
            disabled={s.busy !== null}
            onClick={() => s.onAdd(t)}
            title="I have this skill: add it to my profile"
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset transition-colors disabled:opacity-60 ${
              tone === "bad"
                ? "bg-red-50 text-red-700 ring-red-200 hover:bg-red-100 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-500/30"
                : "bg-amber-50 text-amber-800 ring-amber-200 hover:bg-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30"
            }`}
          >
            <IconPlus size={11} />
            {s.busy === t ? "adding…" : t}
          </button>
        );
      })}
    </span>
  );
}

export function ScoreReportView({ report, onRegenerate }: { report: ScoreReport; onRegenerate?: () => void }) {
  const ats = report.ats;
  const [added, setAdded] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function onAdd(term: string) {
    if (!window.confirm(`Do you really have "${term}"?\n\nIt's added to your profile skills, and the agent may put it on your resumes.`)) return;
    setBusy(term);
    setError(null);
    try {
      await call("/settings/profile/skills", { method: "POST", json: { skill: term } });
      setAdded((a) => [...a, term]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "couldn't add the skill");
    } finally {
      setBusy(null);
    }
  }
  const s: AddState = { added, busy, onAdd: (t) => void onAdd(t) };
  return (
    <Card className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-4">
        <span className={`grid h-16 w-16 place-items-center rounded-full text-2xl font-semibold tabular-nums ring-4 ring-inset ${!ats ? "ring-zinc-200" : ats.score >= 80 ? "text-emerald-700 ring-emerald-200" : ats.score >= 60 ? "text-amber-700 ring-amber-200" : "text-red-700 ring-red-200"}`}>
          {ats ? ats.score.toFixed(0) : "–"}
        </span>
        <div>
          <p className="text-xs text-zinc-500">ATS score · target {report.threshold}</p>
          {report.passed ? <Badge tone="good" dot>passed all checks</Badge> : <Badge tone="warn" dot>below target</Badge>}
        </div>
        <div className="w-full text-xs text-zinc-500">
          <p>
            {report.build.page_count} / {report.build.page_limit} page(s)
            {report.build.last_page_fill != null ? ` · last page ${Math.round(report.build.last_page_fill * 100)}% full` : ""} ·{" "}
            {report.build.alignment_ok ? "no overfull lines" : "overfull lines"}
          </p>
          <p>cost {usd(report.cost_usd)} · {report.models_used.join(", ")}</p>
        </div>
      </div>

      {ats && (
        <div className="grid gap-3">
          <Bar label="Keywords" value={ats.keyword_score} weight="50%" />
          <Bar label="Semantic match" value={ats.semantic_score} weight="30%" />
          <Bar label="Format" value={ats.format_score} weight="20%" />
        </div>
      )}

      {ats && (
        <div className="space-y-1.5">
          <p className="text-xs text-zinc-500">
            Missing skills have a <b>+</b>: click one you really have to add it to your profile, then regenerate.
          </p>
          {error && <Notice tone="bad">{error}</Notice>}
          {added.length > 0 && (
            <Notice tone="good">
              <span className="block">Added to your skills: {added.join(", ")}. Regenerate the resume so it can use {added.length === 1 ? "it" : "them"}.</span>
              {onRegenerate && <Button size="sm" className="mt-2" onClick={onRegenerate}>Regenerate resume</Button>}
            </Notice>
          )}
          <p className="pt-1 text-xs font-medium">Required skills</p>
          <div className="flex flex-wrap gap-3"><Terms items={ats.matched_required} tone="good" /><AddableTerms items={ats.missing_required} tone="bad" s={s} /></div>
          <p className="pt-1 text-xs font-medium">Preferred skills</p>
          <div className="flex flex-wrap gap-3"><Terms items={ats.matched_preferred} tone="good" /><AddableTerms items={ats.missing_preferred} tone="warn" s={s} /></div>
          {(ats.matched_keywords.length > 0 || (ats.missing_keywords ?? []).length > 0) && (
            <>
              <p className="pt-1 text-xs font-medium">Other job keywords</p>
              <div className="flex flex-wrap gap-3"><Terms items={ats.matched_keywords} tone="good" /><AddableTerms items={ats.missing_keywords ?? []} tone="warn" s={s} /></div>
            </>
          )}
          {Object.keys(ats.synonym_matches).length > 0 && (
            <p className="text-xs text-zinc-500">
              Credited via equivalent wording:{" "}
              {Object.entries(ats.synonym_matches).map(([t, e]) => `${t} (“${e}”)`).join("; ")}
            </p>
          )}
        </div>
      )}

      {!report.passed && report.ceiling != null && report.ceiling < report.threshold && (
        <p className="rounded bg-amber-50 p-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          With the facts in your knowledge base, about {report.ceiling.toFixed(0)} is the most this job can reach
          (every supported keyword added, semantic match unchanged). Add the missing skills to your profile or
          projects if you really have them, or lower the threshold in Settings.
        </p>
      )}

      {report.review && (
        <div>
          <p className="text-xs font-medium">ATS reviewer&apos;s last notes to the writer</p>
          {report.review.summary && <p className="text-xs text-zinc-600 dark:text-zinc-400">{report.review.summary}</p>}
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-zinc-600 dark:text-zinc-400">
            {report.review.suggestions.map((s, i) => (
              <li key={i}>
                <span className="font-mono">{s.region}</span>: {s.change} <span className="text-zinc-500">({s.fact_ids.join(", ")})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.writer_stopped && (
        <p className="text-xs text-amber-700 dark:text-amber-400">Quality loop ended early: {report.writer_stopped}</p>
      )}

      {report.structure && !report.structure.ok && (
        <div>
          <p className="text-xs font-medium text-amber-700 dark:text-amber-400">Layout issues in the best attempt</p>
          <ul className="list-disc pl-5 text-xs text-zinc-600 dark:text-zinc-400">{report.structure.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      )}

      <div>
        <p className="text-xs font-medium">Gaps: the JD asks for these, but they aren&apos;t in your data, so they were left out</p>
        {report.gaps.length ? <AddableTerms items={report.gaps} tone="warn" s={s} /> : <span className="text-xs text-zinc-500">none</span>}
      </div>

      {report.guard_removed.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-amber-700 dark:text-amber-400">
            Truthfulness check removed {report.guard_removed.length} unsupported item(s)
          </summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-zinc-500">{report.guard_removed.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </details>
      )}

      {Object.keys(report.changes).length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs">What changed vs your base resume</summary>
          <div className="mt-2 space-y-2">
            {Object.entries(report.changes).map(([region, c]) => (
              <div key={region} className="text-xs">
                <p className="font-mono font-medium">{region} <span className="font-sans font-normal text-zinc-500">· {c.kept} kept</span></p>
                {c.added.map((a, i) => <p key={`a${i}`} className="text-emerald-700 dark:text-emerald-400">+ {a}</p>)}
                {c.removed.map((a, i) => <p key={`r${i}`} className="text-red-700 line-through dark:text-red-400">− {a}</p>)}
              </div>
            ))}
          </div>
        </details>
      )}

      <details>
        <summary className="cursor-pointer text-xs">Quality loop ({report.iterations.length} attempt{report.iterations.length === 1 ? "" : "s"})</summary>
        <ol className="mt-1 list-decimal space-y-1 pl-5 text-xs text-zinc-600 dark:text-zinc-400">
          {report.iterations.map((it, i) => (
            <li key={i}>
              {it.source}: score {it.score ?? "–"}, {it.pages ?? "?"} page(s) {it.passed ? "✓ passed" : ""}
              {it.issues.length > 0 && <span className="block text-zinc-500">{it.issues.slice(0, 3).join(" · ")}</span>}
            </li>
          ))}
        </ol>
      </details>
    </Card>
  );
}
