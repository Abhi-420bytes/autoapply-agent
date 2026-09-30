"use client";

import { useState } from "react";
import { Badge, Button, Card, Notice, SectionTitle } from "@/components/ui";
import { call } from "@/lib/client";
import type { SearchHit } from "@/lib/types";

export function SearchSection() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setHits(await call<SearchHit[]>("/knowledge/search", { method: "POST", json: { query, k: 10 } }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <SectionTitle title="Try retrieval" subtitle="Paste a job description to see which bullets and projects the resume writer would draw on." />
      <Card className="space-y-3">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Backend intern: Python, FastAPI, PostgreSQL, Docker…"
          className="h-28 w-full rounded-md border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <Button onClick={run} disabled={busy || query.trim().length < 3}>{busy ? "Searching…" : "Search"}</Button>
        {error && <Notice tone="bad">{error}</Notice>}
        {hits && hits.length === 0 && <p className="text-sm text-zinc-500">No matches. Is anything embedded yet?</p>}
        {hits && hits.length > 0 && (
          <ol className="space-y-2">
            {hits.map((h, i) => (
              <li key={i} className="rounded-md border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                  <Badge tone="good">score {h.score.toFixed(3)}</Badge>
                  <span>similarity {h.similarity.toFixed(3)}</span>
                  <Badge>{h.kind === "bullet" ? "bullet" : `project ${h.repo_name}`}</Badge>
                  {h.matched_keywords.length > 0 && <span>keywords: {h.matched_keywords.join(", ")}</span>}
                </div>
                <p className="line-clamp-3 break-words">{h.bullet?.text ?? h.content}</p>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </section>
  );
}
