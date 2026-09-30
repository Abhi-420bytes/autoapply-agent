"use client";

import Editor, { DiffEditor, loader, type Monaco } from "@monaco-editor/react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { registerLatex } from "@/components/studio/latex";
import { BuildReportView } from "@/components/templates/BuildReportView";
import { Badge, Button, Card, Label, Notice, Select } from "@/components/ui";
import { call, text } from "@/lib/client";
import type { RenderResult, ResumeVersion } from "@/lib/types";

// Monaco is served from this app (see scripts/copy-static-libs.mjs), not a CDN.
loader.config({ paths: { vs: "/monaco/vs" } });
const PdfViewer = dynamic(() => import("@/components/PdfViewer"), { ssr: false });

function useDark() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(m.matches);
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return dark;
}

export default function StudioPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const dark = useDark();
  const [resume, setResume] = useState<ResumeVersion | null>(null);
  const [source, setSource] = useState("");
  const [draft, setDraft] = useState("");
  const [versions, setVersions] = useState<ResumeVersion[]>([]);
  const [compare, setCompare] = useState<number | null>(null);
  const [compareTex, setCompareTex] = useState("");
  const [pageLimit, setPageLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RenderResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const saveRef = useRef<() => void>(() => undefined);

  const load = useCallback(async () => {
    try {
      const r = await call<ResumeVersion>(`/resumes/${id}`);
      const tex = await text(`/resumes/${id}/tex`);
      setResume(r);
      setSource(tex);
      setDraft(tex);
      setPageLimit(String(r.page_limit ?? 1));
      setVersions(await call<ResumeVersion[]>(r.job_id ? `/resumes?job_id=${r.job_id}` : `/resumes?lineage_id=${r.lineage_id}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (compare == null) return;
    void text(`/resumes/${compare}/tex`).then(setCompareTex);
  }, [compare]);

  async function save() {
    if (busy || draft === source) return;
    setBusy(true);
    setError(null);
    try {
      const r = await call<RenderResult>(`/resumes/${id}/edit`, { method: "POST", json: { tex: draft, page_limit: Number(pageLimit) } });
      setResult(r);
      if (r.resume) router.replace(`/studio/${r.resume.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }
  saveRef.current = save;

  function onMount(_editor: unknown, monaco: Monaco) {
    registerLatex(monaco);
    // eslint-disable-next-line no-bitwise
    (_editor as { addCommand: (k: number, f: () => void) => void }).addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => saveRef.current());
  }

  if (error && !resume) return <Notice tone="bad">{error}</Notice>;
  if (!resume) return <p className="text-sm text-zinc-500">Loading…</p>;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          {resume.job_id ? <Link href={`/jobs/${resume.job_id}`} className="text-xs text-zinc-500 hover:underline">← Job #{resume.job_id}</Link> : <Link href="/templates" className="text-xs text-zinc-500 hover:underline">← Templates</Link>}
          <h1 className="text-2xl font-semibold">Resume studio</h1>
          <p className="text-sm text-zinc-500">
            Version {resume.version} · {resume.kind.replace("_", " ")}. Only text between <code>%%BEGIN</code>/<code>%%END</code> markers can change; edits
            outside them are rejected. <kbd className="rounded border px-1 text-xs">⌘/Ctrl S</kbd> saves and recompiles.
          </p>
        </div>
        <div className="ml-auto flex items-end gap-2">
          <div className="w-28">
            <Label htmlFor="pl">Page limit</Label>
            <Select id="pl" value={pageLimit} onChange={(e) => setPageLimit(e.target.value)}>
              <option value="1">1</option><option value="2">2</option><option value="3">3</option>
            </Select>
          </div>
          <div className="w-56">
            <Label htmlFor="cmp">Compare with</Label>
            <Select id="cmp" value={compare ?? ""} onChange={(e) => setCompare(e.target.value ? Number(e.target.value) : null)}>
              <option value="">(no diff)</option>
              {versions.filter((v) => v.id !== resume.id).map((v) => (
                <option key={v.id} value={v.id}>v{v.version} · {v.kind.replace("_", " ")} · {new Date(v.created_at).toLocaleString()}</option>
              ))}
            </Select>
          </div>
          <Button onClick={save} disabled={busy || draft === source}>{busy ? "Compiling…" : draft === source ? "Saved" : "Save & compile"}</Button>
        </div>
      </header>

      {error && <Notice tone="bad">{error}</Notice>}
      {result && <Card><BuildReportView report={result.report} /></Card>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,600px)]">
        <Card className="h-[78vh] overflow-hidden p-0">
          {compare == null ? (
            <Editor language="latex" theme={dark ? "latex-dark" : "latex-light"} value={draft} onChange={(v) => setDraft(v ?? "")}
              onMount={onMount} beforeMount={registerLatex}
              options={{ minimap: { enabled: false }, wordWrap: "on", fontSize: 13, scrollBeyondLastLine: false }} />
          ) : (
            <DiffEditor language="latex" theme={dark ? "latex-dark" : "latex-light"} original={compareTex} modified={draft} beforeMount={registerLatex}
              onMount={(ed) => ed.getModifiedEditor().onDidChangeModelContent(() => setDraft(ed.getModifiedEditor().getValue()))}
              options={{ renderSideBySide: true, wordWrap: "on", fontSize: 12, minimap: { enabled: false } }} />
          )}
        </Card>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {resume.build_report && (
              <>
                <Badge tone={resume.build_report.within_limit ? "good" : "bad"}>{resume.page_count}/{resume.page_limit} page(s)</Badge>
                <Badge tone={resume.build_report.alignment_ok ? "good" : "warn"}>{resume.build_report.alignment_ok ? "no overfull lines" : "overfull lines"}</Badge>
              </>
            )}
            {resume.ats_score != null && <Badge>ATS {resume.ats_score.toFixed(0)}</Badge>}
          </div>
          <PdfViewer path={`/resumes/${resume.id}/pdf`} width={580} />
        </div>
      </div>
    </div>
  );
}
