"use client";

import { useState } from "react";
import { Button, Card, Input, Label, Notice } from "@/components/ui";
import { call } from "@/lib/client";
import { isRisky, type Site, type SiteMode } from "./types";

const lines = (s: string) => s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export function SiteForm({ site, onDone, onCancel }: { site?: Site; onDone: () => Promise<void>; onCancel?: () => void }) {
  const [name, setName] = useState(site?.name ?? "");
  const [urls, setUrls] = useState((site?.search_urls ?? []).join("\n"));
  const [cats, setCats] = useState((site?.categories ?? []).join(", "));
  const [excl, setExcl] = useState((site?.exclude_keywords ?? []).join(", "));
  const [mode, setMode] = useState<SiteMode>(site?.mode ?? "search_only");
  const [every, setEvery] = useState(String(site?.check_every_minutes ?? 120));
  const [delay, setDelay] = useState(String(site?.apply_delay_minutes ?? 0));
  const [maxNew, setMaxNew] = useState(String(site?.max_new_per_check ?? 5));
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const risky = isRisky(lines(urls));

  async function save() {
    setError(null);
    const body = {
      name, search_urls: lines(urls), categories: lines(cats), exclude_keywords: lines(excl), mode,
      check_every_minutes: Number(every), apply_delay_minutes: Number(delay), max_new_per_check: Number(maxNew),
      enabled: site?.enabled ?? true, acknowledge_risk: ack,
    };
    try {
      await call(site ? `/sites/${site.id}` : "/sites", { method: site ? "PUT" : "POST", json: body });
      if (!site) { setName(""); setUrls(""); setCats(""); setExcl(""); setAck(false); }
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  return (
    <Card className={site ? "space-y-3" : "space-y-3 border-dashed"}>
      {!site && <p className="text-sm font-medium">Add a job website</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div><Label htmlFor={`sn${site?.id ?? "new"}`}>Name</Label><Input id={`sn${site?.id ?? "new"}`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Havlock, LinkedIn, Acme careers…" /></div>
        <div>
          <Label htmlFor={`sc${site?.id ?? "new"}`}>Job categories (comma-separated)</Label>
          <Input id={`sc${site?.id ?? "new"}`} value={cats} onChange={(e) => setCats(e.target.value)} placeholder="Data Engineer, Machine Learning Intern" />
          <p className="mt-0.5 text-[11px] text-zinc-500">Only these roles are applied to. Close variants count (e.g. “ML Engineer Intern”). Empty = every job.</p>
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor={`su${site?.id ?? "new"}`}>Search result links (one per line)</Label>
          <textarea id={`su${site?.id ?? "new"}`} value={urls} onChange={(e) => setUrls(e.target.value)} rows={2}
            placeholder="https://placements.haveloc.com/jobs?jobview=eligible"
            className="w-full rounded-md border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
          <p className="mt-0.5 text-[11px] text-zinc-500">Open the site, search/filter the way you like, then paste the results page&apos;s address.</p>
        </div>
        <div><Label htmlFor={`se${site?.id ?? "new"}`}>Skip jobs containing (comma-separated)</Label><Input id={`se${site?.id ?? "new"}`} value={excl} onChange={(e) => setExcl(e.target.value)} placeholder="senior, unpaid, 5+ years" /></div>
        <div className="grid grid-cols-3 gap-2">
          <div><Label htmlFor={`sv${site?.id ?? "new"}`}>Check every (min)</Label><Input id={`sv${site?.id ?? "new"}`} type="number" min={15} value={every} onChange={(e) => setEvery(e.target.value)} /></div>
          <div><Label htmlFor={`sd${site?.id ?? "new"}`}>Review window (min)</Label><Input id={`sd${site?.id ?? "new"}`} type="number" min={0} value={delay} onChange={(e) => setDelay(e.target.value)} title="Time you get to review before it applies. 0 = apply as soon as ready" /></div>
          <div><Label htmlFor={`sm${site?.id ?? "new"}`}>Max new / check</Label><Input id={`sm${site?.id ?? "new"}`} type="number" min={1} max={25} value={maxNew} onChange={(e) => setMaxNew(e.target.value)} /></div>
        </div>
      </div>
      <div>
        <Label>What should the agent do?</Label>
        <div className="inline-flex overflow-hidden rounded-md border border-zinc-300 text-xs dark:border-zinc-700" role="group">
          {(["search_only", "search_and_apply"] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`px-3 py-1.5 ${mode === m ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : ""}`}>
              {m === "search_only" ? "Search + prepare resume (I apply)" : "Search + apply"}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-zinc-500">
          {mode === "search_only"
            ? "Finds matching jobs, reads each JD (and PDFs) and prepares a tailored resume; you apply yourself."
            : `Prepares the resume right away, then applies automatically ${Number(delay) > 0 ? `after the ${delay}-minute review window` : "as soon as it's ready"}, unless you click “Don't apply”. “Apply now” skips the wait. Resumes that fail a quality check wait for your approval instead.`}
        </p>
      </div>
      {risky && (
        <Notice tone="warn">
          This site&apos;s terms prohibit automated access, and using the agent there can get your account restricted. Keep it on “Search + prepare
          resume” and apply yourself.
          {mode === "search_and_apply" && (
            <label className="mt-2 flex items-center gap-2 text-xs">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I understand the risk to my account and want the agent to apply anyway.
            </label>
          )}
        </Notice>
      )}
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="flex gap-2">
        <Button onClick={save} disabled={!name || lines(urls).length === 0}>{site ? "Save" : "Add website"}</Button>
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
    </Card>
  );
}
