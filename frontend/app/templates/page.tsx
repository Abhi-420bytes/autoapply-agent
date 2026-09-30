"use client";

import { useCallback, useEffect, useState } from "react";
import { Notice, SectionTitle } from "@/components/ui";
import { RenderPanel } from "@/components/templates/RenderPanel";
import { TemplateCard } from "@/components/templates/TemplateCard";
import { SuggestMarkersCard, UploadCard } from "@/components/templates/UploadCard";
import type { Template } from "@/components/templates/types";
import { call } from "@/lib/client";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setTemplates(await call<Template[]>("/templates"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const active = templates?.find((t) => t.is_active);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Resume template</h1>
        <p className="text-sm text-zinc-500">
          Your Overleaf template is used exactly as-is. Only the marked regions are ever rewritten, and every output is
          checked against your page limit.
        </p>
      </header>
      {error && <Notice tone="bad">{error}</Notice>}
      <div className="grid gap-4 lg:grid-cols-2">
        <UploadCard onUploaded={reload} />
        <SuggestMarkersCard />
      </div>
      <section>
        <SectionTitle title="Templates" />
        <div className="space-y-3">
          {templates?.length === 0 && <Notice>No template uploaded yet.</Notice>}
          {templates?.map((t) => <TemplateCard key={t.id} t={t} onChange={reload} />)}
        </div>
      </section>
      {active && <RenderPanel key={active.id} template={active} />}
    </div>
  );
}
