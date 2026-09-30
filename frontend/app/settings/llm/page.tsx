"use client";

import { useCallback, useEffect, useState } from "react";
import { Notice } from "@/components/ui";
import { ProvidersSection } from "@/components/llm/ProvidersSection";
import { TasksSection } from "@/components/llm/TasksSection";
import { UsageSection } from "@/components/llm/UsageSection";
import { call } from "@/lib/client";
import type { Provider, ProviderSpec, TaskConfig, UsageSummary } from "@/lib/types";

type Data = { catalog: ProviderSpec[]; providers: Provider[]; tasks: TaskConfig[]; usage: UsageSummary };

export default function LLMSettingsPage() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [catalog, providers, tasks, usage] = await Promise.all([
        call<ProviderSpec[]>("/llm/catalog"),
        call<Provider[]>("/llm/providers"),
        call<TaskConfig[]>("/llm/tasks"),
        call<UsageSummary>("/llm/usage"),
      ]);
      setData({ catalog, providers, tasks, usage });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">LLM settings</h1>
        <p className="text-sm text-zinc-500">
          Providers, per-task models, fallbacks, embeddings and budget. Changes apply immediately, with no code changes or restarts.
        </p>
      </header>
      {error && <Notice tone="bad">Could not load settings: {error}</Notice>}
      {!data && !error && <p className="text-sm text-zinc-500">Loading…</p>}
      {data && (
        <>
          <ProvidersSection catalog={data.catalog} providers={data.providers} onChange={reload} />
          {/* key forces rows to re-init after provider changes */}
          <TasksSection
            key={data.providers.map((p) => `${p.id}:${p.enabled}`).join(",")}
            tasks={data.tasks}
            providers={data.providers}
            onChange={reload}
          />
          <UsageSection usage={data.usage} onChange={reload} />
        </>
      )}
    </div>
  );
}
