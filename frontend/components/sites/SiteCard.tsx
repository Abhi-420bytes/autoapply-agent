"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Notice } from "@/components/ui";
import { blobUrl, call } from "@/lib/client";
import { SiteForm } from "./SiteForm";
import type { Site } from "./types";

function ago(iso: string | null) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

export function SiteCard({ site: s, onChange }: { site: Site; onChange: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (editing) return <SiteForm site={s} onDone={async () => { setEditing(false); await onChange(); }} onCancel={() => setEditing(false)} />;

  async function checkNow() {
    await call(`/sites/${s.id}/check`, { method: "POST" });
    setMsg("Queued: the agent searches this site within a minute.");
    await onChange();
  }
  async function toggle() {
    await call(`/sites/${s.id}`, { method: "PUT", json: {
      name: s.name, search_urls: s.search_urls, categories: s.categories, exclude_keywords: s.exclude_keywords,
      mode: s.mode, check_every_minutes: s.check_every_minutes, apply_delay_minutes: s.apply_delay_minutes,
      max_new_per_check: s.max_new_per_check, enabled: !s.enabled, portal_id: s.portal_id, acknowledge_risk: true,
    } });
    await onChange();
  }
  async function remove() {
    if (!window.confirm(`Remove ${s.name}? Jobs already found stay in your Jobs list.`)) return;
    await call(`/sites/${s.id}`, { method: "DELETE" });
    await onChange();
  }

  return (
    <Card className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{s.name}</span>
        <Badge tone={s.mode === "search_and_apply" ? "warn" : "neutral"}>{s.mode === "search_and_apply" ? "search + apply" : "search + prepare resume"}</Badge>
        {!s.enabled && <Badge>paused</Badge>}
        {s.tos_warning && <Badge tone="warn">bot-restricted site</Badge>}
        <span className="text-xs text-zinc-500">checked {ago(s.last_checked_at)} · every {s.check_every_minutes} min · {s.mode === "search_and_apply" ? `review window ${s.apply_delay_minutes ? `${s.apply_delay_minutes} min` : "none (applies when ready)"}` : "you apply"}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <Button size="sm" onClick={checkNow}>Search now</Button>
          <Button size="sm" variant="outline" onClick={toggle}>{s.enabled ? "Pause" : "Resume"}</Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
          <Button size="sm" variant="ghost" className="text-red-600" onClick={remove}>Remove</Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1 text-xs">
        {s.categories.length ? s.categories.map((c) => <Badge key={c} tone="good">{c}</Badge>) : <span className="text-zinc-500">all roles</span>}
        {s.exclude_keywords.map((c) => <Badge key={c} tone="bad">not “{c}”</Badge>)}
      </div>
      <p className="text-xs text-zinc-500">
        {s.found} postings seen · {s.matched} matched · {s.jobs} jobs · max {s.max_new_per_check} new per check ·{" "}
        {s.has_login ? "login saved" : <>no login saved (<Link className="underline" href="/settings/portals">add it in Portals</Link> if the site needs one)</>}
      </p>
      {s.last_error && <p className="break-words text-xs text-red-700 dark:text-red-400">{s.last_error}</p>}
      {msg && <Notice>{msg}</Notice>}
      {s.prompt && <SitePrompt site={s} onDone={onChange} />}
    </Card>
  );
}

function SitePrompt({ site, onDone }: { site: Site; onDone: () => Promise<void> }) {
  const [url, setUrl] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  useEffect(() => {
    let revoke: string | null = null;
    void blobUrl(`/sites/${site.id}/prompt/screenshot`).then((u) => { revoke = u; setUrl(u); }).catch(() => undefined);
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [site.id]);
  async function send() {
    await call(`/sites/${site.id}/prompt`, { method: "POST", json: { answer } });
    setAnswer("");
    await onDone();
  }
  return (
    <div className="space-y-2 rounded-md border border-amber-400 p-3 dark:border-amber-600">
      <p className="text-sm font-medium">Action needed: {site.prompt?.question}</p>
      <p className="text-xs text-zinc-500">The agent is paused and waits up to 10 minutes. It never bypasses OTP/CAPTCHA; it only types what you enter.</p>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {url && <img src={url} alt="What the agent sees" className="max-h-80 rounded border border-zinc-200 dark:border-zinc-800" />}
      <div className="flex gap-2">
        <Input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={site.prompt?.kind === "otp" ? "OTP code" : "CAPTCHA text"} />
        <Button onClick={send} disabled={!answer.trim()}>Send to agent</Button>
      </div>
    </div>
  );
}
