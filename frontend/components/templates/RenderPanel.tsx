"use client";

import { useState } from "react";
import { Button, Card, Label, Notice, SectionTitle, Select } from "@/components/ui";
import { call } from "@/lib/client";
import { BuildReportView } from "./BuildReportView";
import { PdfPreview } from "./PdfPreview";
import type { RenderResult, Template } from "./types";

/** Edit region contents of the active template and compile. The full Monaco-based
 * Resume Studio with version diffs arrives in Phase 6. */
export function RenderPanel({ template }: { template: Template }) {
  const [regions, setRegions] = useState<Record<string, string>>(
    Object.fromEntries(template.regions.map((r) => [r.name, r.content])),
  );
  const [pageLimit, setPageLimit] = useState(String(template.page_limit ?? 1));
  const [fit, setFit] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RenderResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function renderNow() {
    setBusy(true);
    setError(null);
    const changed = Object.fromEntries(
      Object.entries(regions).filter(([k, v]) => v !== template.regions.find((r) => r.name === k)?.content),
    );
    try {
      setResult(
        await call<RenderResult>("/resumes/render", {
          method: "POST",
          json: { template_id: template.id, regions: changed, page_limit: Number(pageLimit), fit, label: "template page" },
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <SectionTitle
        title="Try it: edit regions & compile"
        subtitle="Only the marked regions can change, and only with commands your template already uses. Annotate a bullet line with %%prio:0.2 (lower is dropped first) or %%pin (never dropped)."
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="space-y-3">
          {template.regions.map((r) => (
            <div key={r.name}>
              <Label htmlFor={`reg-${r.name}`}>{r.name}</Label>
              <textarea
                id={`reg-${r.name}`}
                spellCheck={false}
                value={regions[r.name] ?? ""}
                onChange={(e) => setRegions((prev) => ({ ...prev, [r.name]: e.target.value }))}
                className="h-32 w-full rounded-md border border-zinc-300 bg-white p-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
              />
            </div>
          ))}
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-28">
              <Label htmlFor="page-limit">Page limit</Label>
              <Select id="page-limit" value={pageLimit} onChange={(e) => setPageLimit(e.target.value)}>
                <option value="1">1</option>
                <option value="2">2</option>
                <option value="3">3</option>
              </Select>
            </div>
            <label className="flex items-center gap-2 pb-2 text-sm">
              <input type="checkbox" checked={fit} onChange={(e) => setFit(e.target.checked)} />
              Drop lowest-priority bullets if over the limit
            </label>
            <Button onClick={renderNow} disabled={busy}>{busy ? "Compiling…" : "Compile"}</Button>
          </div>
          {error && <Notice tone="bad">{error}</Notice>}
        </Card>
        <div className="space-y-3">
          {result && (
            <Card>
              {result.resume && (
                <p className="mb-2 text-xs text-zinc-500">Saved as version {result.resume.version}</p>
              )}
              <BuildReportView report={result.report} />
            </Card>
          )}
          {result?.resume && <PdfPreview path={`/resumes/${result.resume.id}/pdf`} title="Rendered resume" />}
        </div>
      </div>
    </section>
  );
}
