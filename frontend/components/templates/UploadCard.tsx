"use client";

import { useState } from "react";
import { Button, Card, Input, Label, Notice } from "@/components/ui";
import { upload } from "@/lib/client";
import type { SuggestResult, Template } from "./types";

export function UploadCard({ onUploaded }: { onUploaded: (t: Template) => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [pdf, setPdf] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);

  async function submit(autoMarkers = false) {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    if (pdf) form.append("reference_pdf", pdf);
    if (label.trim()) form.append("label", label.trim());
    if (autoMarkers) form.append("auto_markers", "true");
    try {
      const t = await upload<Template>("/templates", form);
      setFile(null);
      setPdf(null);
      setLabel("");
      setInputKey((k) => k + 1);
      await onUploaded(t);
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <p className="mb-1 text-sm font-medium">Upload base template</p>
      <p className="mb-3 text-xs text-zinc-500">
        Your Overleaf <code>.tex</code> (or the project <code>.zip</code> from Overleaf → Menu → Download Source), with
        editable sections wrapped in <code>%%BEGIN:NAME%%</code> / <code>%%END:NAME%%</code> lines. Add the PDF
        Overleaf produced to check that the output here matches it.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="tpl-file">Template (.tex or .zip)</Label>
          <Input key={`f${inputKey}`} id="tpl-file" type="file" accept=".tex,.zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div>
          <Label htmlFor="tpl-pdf">Overleaf PDF (optional)</Label>
          <Input key={`p${inputKey}`} id="tpl-pdf" type="file" accept=".pdf" onChange={(e) => setPdf(e.target.files?.[0] ?? null)} />
        </div>
        <div>
          <Label htmlFor="tpl-label">Label</Label>
          <Input id="tpl-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. SDE resume" />
        </div>
      </div>
      {error && (
        <div className="mt-3 space-y-2">
          <Notice tone="bad">{error}</Notice>
          {error.includes("no editable regions") && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => submit(true)} disabled={busy}>
                Add markers automatically &amp; upload
              </Button>
              <span className="text-xs text-zinc-500">
                Each <code>\section</code> body becomes an editable region. Your header, preamble and layout stay locked.
              </span>
            </div>
          )}
        </div>
      )}
      <Button className="mt-3" onClick={() => submit()} disabled={!file || busy}>
        {busy ? "Compiling…" : "Upload & compile"}
      </Button>
    </Card>
  );
}

export function SuggestMarkersCard() {
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function pick(file: File | undefined) {
    if (!file) return;
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    try {
      setResult(await upload<SuggestResult>("/templates/suggest-markers", form));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.tex], { type: "text/x-tex" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "resume-marked.tex";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Card>
      <p className="mb-1 text-sm font-medium">No markers yet?</p>
      <p className="mb-3 text-xs text-zinc-500">
        Upload your unmarked <code>.tex</code> and every <code>\section</code> body gets wrapped in markers. The header
        (name and contact details) stays locked. Review the result, then upload it above.
      </p>
      <Input type="file" accept=".tex" onChange={(e) => pick(e.target.files?.[0])} aria-label="Unmarked template" />
      {error && <div className="mt-3"><Notice tone="bad">{error}</Notice></div>}
      {result && (
        <div className="mt-3 space-y-2">
          <p className="text-sm">
            Regions: {result.regions.map((r) => <code key={r} className="mr-1.5">{r}</code>)}
          </p>
          <textarea
            readOnly
            value={result.tex}
            className="h-56 w-full rounded-md border border-zinc-300 bg-zinc-50 p-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
          />
          <Button size="sm" variant="outline" onClick={download}>Download marked .tex</Button>
        </div>
      )}
    </Card>
  );
}
