"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Label, Notice, SectionTitle, Switch } from "@/components/ui";
import { call } from "@/lib/client";
import type { AppSettings } from "@/lib/api";
import type { KnowledgeStatus, Repo, SyncStatus } from "@/lib/types";

export function GitHubSection({ status, onChange }: { status: KnowledgeStatus; onChange: () => Promise<void> }) {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [username, setUsername] = useState("");
  const [exclude, setExclude] = useState("");
  const [token, setToken] = useState("");
  const [tokenMasked, setTokenMasked] = useState<string | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const sync = status.github_sync;
  const busy = sync?.state === "queued" || sync?.state === "running";

  async function load() {
    const [s, secrets, r] = await Promise.all([
      call<AppSettings>("/settings"),
      call<{ name: string; masked: string | null }[]>("/settings/secrets"),
      call<Repo[]>("/knowledge/github/repos"),
    ]);
    setSettings(s);
    setUsername(s.github_username ?? "");
    setExclude(s.github_exclude_repos.join(", "));
    setTokenMasked(secrets.find((x) => x.name === "github_token")?.masked ?? null);
    setRepos(r);
  }

  useEffect(() => {
    void load();
  }, []);

  // Refresh the repo list when a sync finishes.
  useEffect(() => {
    if (sync?.state === "done" || sync?.state === "failed") void load();
  }, [sync?.state, sync?.finished_at]);

  async function save(patch: Partial<AppSettings>) {
    setMsg(null);
    try {
      setSettings(await call<AppSettings>("/settings", { method: "PATCH", json: patch }));
      setMsg({ tone: "good", text: "Saved." });
      await onChange();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  async function saveToken() {
    setMsg(null);
    try {
      const r = await call<{ masked: string | null }>("/settings/secrets/github_token", {
        method: "PUT",
        json: { value: token.trim() },
      });
      setTokenMasked(r.masked);
      setToken("");
      setMsg({ tone: "good", text: "Token saved (encrypted)." });
      await onChange();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  async function removeToken() {
    await call("/settings/secrets/github_token", { method: "DELETE" });
    setTokenMasked(null);
    await onChange();
  }

  async function startSync() {
    setMsg(null);
    try {
      await call("/knowledge/github/sync", { method: "POST" });
      await onChange();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  return (
    <section>
      <SectionTitle
        title="GitHub"
        subtitle="Repos are summarized by the repo_summarizer model. Tech and numbers that don't appear in the repo's own data are removed automatically."
      />
      <Card className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor="gh-user">Username</Label>
            <div className="flex gap-2">
              <Input id="gh-user" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="your-github-login" />
              <Button size="sm" variant="outline" className="h-9" onClick={() => save({ github_username: username.trim() || null })}>Save</Button>
            </div>
          </div>
          <div>
            <Label htmlFor="gh-token">Token (optional)</Label>
            <div className="flex gap-2">
              <Input id="gh-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={tokenMasked ?? "read-only token"} />
              <Button size="sm" variant="outline" className="h-9" onClick={saveToken} disabled={!token.trim()}>Save</Button>
            </div>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              {tokenMasked ? <>Saved: <code>{tokenMasked}</code> · <button className="underline" onClick={removeToken}>remove</button></> : "Raises GitHub's limit from 60 to 5000 requests/hour. A fine-grained token with read-only Contents + Metadata is enough."}
            </p>
          </div>
          <div>
            <Label htmlFor="gh-exclude">Exclude repos (comma-separated)</Label>
            <div className="flex gap-2">
              <Input id="gh-exclude" value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="dotfiles, homework" />
              <Button size="sm" variant="outline" className="h-9" onClick={() => save({ github_exclude_repos: exclude.split(",").map((x) => x.trim()).filter(Boolean) })}>Save</Button>
            </div>
          </div>
        </div>
        {settings && (
          <div className="flex flex-wrap gap-6 text-sm">
            <label className="flex items-center gap-2">
              <Switch label="Include private repos" checked={settings.github_include_private} onChange={(v) => save({ github_include_private: v })} />
              Include private repos (needs a token)
            </label>
            <label className="flex items-center gap-2">
              <Switch label="Include forks" checked={settings.github_include_forks} onChange={(v) => save({ github_include_forks: v })} />
              Include forks you committed to
            </label>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={startSync} disabled={busy || !status.github_username}>
            {busy ? "Syncing…" : "Sync now"}
          </Button>
          <SyncBadge sync={sync} />
          <span className="text-xs text-zinc-500">Also runs automatically every Sunday.</span>
        </div>
        {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      </Card>

      <div className="mt-3 space-y-3">
        {repos.map((r) => <RepoCard key={r.id} repo={r} />)}
      </div>
    </section>
  );
}

function SyncBadge({ sync }: { sync: SyncStatus | null }) {
  if (!sync) return <Badge>never synced</Badge>;
  if (sync.state === "queued") return <Badge tone="warn">queued, the worker starts within a minute</Badge>;
  if (sync.state === "running") return <Badge tone="warn">running since {new Date(sync.started_at ?? "").toLocaleTimeString()}</Badge>;
  if (sync.state === "failed") return <Badge tone="bad">failed: {sync.error}</Badge>;
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs">
      <Badge tone={sync.errors?.length ? "warn" : "good"}>done</Badge>
      {sync.repos_kept} repos · {sync.summarized} summarized · {sync.unchanged} unchanged · {sync.removed} removed · {sync.bullets} bullets
      {sync.errors && sync.errors.length > 0 && <span className="text-amber-700 dark:text-amber-400">{sync.errors.length} problem(s): {sync.errors[0]}</span>}
    </span>
  );
}

function RepoCard({ repo: r }: { repo: Repo }) {
  const s = r.summary;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <a href={r.url} target="_blank" rel="noreferrer" className="font-medium underline-offset-2 hover:underline">{r.full_name}</a>
        {r.is_private && <Badge>private</Badge>}
        {r.is_fork && <Badge>fork</Badge>}
        {r.archived && <Badge>archived</Badge>}
        {s?.role && <Badge tone="good">{s.role}</Badge>}
        <span className="text-xs text-zinc-500">
          ★ {r.stars} · {r.commit_count ?? 0} commits by you · {Object.keys(r.languages).slice(0, 4).join(", ")}
        </span>
      </div>
      {s?.problem && <p className="mt-1 text-sm">{s.problem}</p>}
      {s?.tech_stack && s.tech_stack.length > 0 && <p className="mt-1 text-xs text-zinc-500">Tech: {s.tech_stack.join(", ")}</p>}
      {s?.outcomes && s.outcomes.length > 0 && <p className="mt-1 text-xs text-zinc-500">Outcomes: {s.outcomes.join("; ")}</p>}
      {r.bullets.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm">
          {r.bullets.map((b) => <li key={b.id}>{b.text}</li>)}
        </ul>
      )}
      {s?.dropped && s.dropped.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-amber-700 dark:text-amber-400">
            Truthfulness check removed {s.dropped.length} unsupported claim(s)
          </summary>
          <ul className="mt-1 list-disc pl-5 text-zinc-500">{s.dropped.map((d, i) => <li key={i}>{d}</li>)}</ul>
        </details>
      )}
      {!s && !r.sync_error && <p className="mt-1 text-xs text-zinc-500">Not summarized yet.</p>}
      {r.sync_error && <p className="mt-1 break-words text-xs text-red-700 dark:text-red-400">{r.sync_error}</p>}
    </Card>
  );
}
