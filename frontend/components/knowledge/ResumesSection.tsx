"use client";

import { useEffect, useState } from "react";
import { Button, Card, Input, Label, Notice, SectionTitle } from "@/components/ui";
import { call, upload } from "@/lib/client";
import type { IngestResult, PastResume } from "@/lib/types";

export function ResumesSection({ onChange }: { onChange: () => Promise<void> }) {
  const [files, setFiles] = useState<FileList | null>(null);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<IngestResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [list, setList] = useState<PastResume[]>([]);
  const [inputKey, setInputKey] = useState(0);

  const load = async () => setList(await call<PastResume[]>("/knowledge/resumes"));
  useEffect(() => {
    void load();
  }, []);

  async function submit() {
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    if (date) form.append("source_date", date);
    try {
      setResults(await upload<IngestResult[]>("/knowledge/resumes", form));
      setFiles(null);
      setInputKey((k) => k + 1);
      await Promise.all([load(), onChange()]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(r: PastResume) {
    if (!window.confirm(`Remove ${r.filename} and its ${r.bullet_count} bullet(s) from the bank?`)) return;
    await call(`/knowledge/resumes/${r.id}`, { method: "DELETE" });
    await Promise.all([load(), onChange()]);
  }

  return (
    <section>
      <SectionTitle title="Past resumes" subtitle="Bullets are extracted as written, with no rewriting and no LLM. Exact and near-duplicates are skipped." />
      <Card className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Label htmlFor="past-files">.tex or .pdf files (up to 10)</Label>
            <Input key={inputKey} id="past-files" type="file" multiple accept=".tex,.pdf" onChange={(e) => setFiles(e.target.files)} />
          </div>
          <div>
            <Label htmlFor="past-date">When was it written? (optional)</Label>
            <Input id="past-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
        </div>
        <Button onClick={submit} disabled={!files?.length || busy}>{busy ? "Parsing…" : "Add to bullet bank"}</Button>
        {error && <Notice tone="bad">{error}</Notice>}
        {results?.map((r) => (
          <Notice key={r.resume_id} tone={r.added > 0 ? "good" : "warn"}>
            <b>{r.filename}</b>: found {r.found} bullet(s), added {r.added}, skipped {r.exact_duplicates} exact and{" "}
            {r.near_duplicates} near-duplicate(s).{r.warnings.length > 0 && <> {r.warnings.join(" ")}</>}
          </Notice>
        ))}
        {list.length > 0 && (
          <ul className="divide-y divide-zinc-100 text-sm dark:divide-zinc-800">
            {list.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <span className="font-medium">{r.filename}</span>
                <span className="text-xs text-zinc-500">
                  {r.format?.toUpperCase()} · {r.bullet_count} bullet(s){r.source_date ? ` · from ${r.source_date}` : ""}
                </span>
                <Button size="sm" variant="ghost" className="ml-auto text-red-600" onClick={() => remove(r)}>Remove</Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}
