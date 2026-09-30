"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BulletBank } from "@/components/knowledge/BulletBank";
import { GitHubSection } from "@/components/knowledge/GitHubSection";
import { ResumesSection } from "@/components/knowledge/ResumesSection";
import { SearchSection } from "@/components/knowledge/SearchSection";
import { Badge, Notice } from "@/components/ui";
import { call } from "@/lib/client";
import type { KnowledgeStatus } from "@/lib/types";

export default function KnowledgePage() {
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reload = useCallback(async () => {
    try {
      setStatus(await call<KnowledgeStatus>("/knowledge/status"));
      setVersion((v) => v + 1);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Poll while a sync is queued/running or items are waiting for embeddings.
  const syncing = status?.github_sync?.state === "queued" || status?.github_sync?.state === "running";
  const pending = syncing || (status?.unembedded_items ?? 0) > 0 || status?.reindex_pending;
  useEffect(() => {
    if (!pending) return;
    timer.current = setTimeout(() => void reload(), 4000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [pending, status, reload]);

  const total = status ? Object.values(status.bullets_by_source).reduce((a, b) => a + b, 0) : 0;

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Knowledge base</h1>
        <p className="text-sm text-zinc-500">
          The only sources your resume content may come from: GitHub, your past resumes and your profile.
        </p>
        {status && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <Badge>{status.repos} repos</Badge>
            <Badge>{total} bullets</Badge>
            <Badge>{status.past_resumes} past resumes</Badge>
            {status.embedding_model ? (
              <Badge tone={status.unembedded_items ? "warn" : "good"}>
                {status.embedding_model}: {status.embedded_chunks} chunks
                {status.unembedded_items ? `, ${status.unembedded_items} item(s) waiting` : ""}
              </Badge>
            ) : (
              <Badge tone="bad">no embedding model; set one in LLM settings</Badge>
            )}
            {status.reindex_pending && <Badge tone="warn">re-index in progress</Badge>}
          </div>
        )}
      </header>
      {error && <Notice tone="bad">{error}</Notice>}
      {status && <GitHubSection status={status} onChange={reload} />}
      <ResumesSection onChange={reload} />
      <SearchSection />
      <BulletBank version={version} />
    </div>
  );
}
