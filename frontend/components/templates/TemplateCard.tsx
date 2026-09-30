"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, Notice } from "@/components/ui";
import { call } from "@/lib/client";
import { BuildReportView } from "./BuildReportView";
import { PdfPreview } from "./PdfPreview";
import type { Template } from "./types";

export function TemplateCard({ t, onChange }: { t: Template; onChange: () => Promise<void> }) {
  const [showPdf, setShowPdf] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function activate() {
    try {
      await call(`/templates/${t.id}/activate`, { method: "POST" });
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  const check = t.overleaf_check;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{t.label ?? `Template #${t.id}`}</span>
        {t.is_active ? <Badge tone="good">active</Badge> : <Badge>inactive</Badge>}
        <span className="text-xs text-zinc-500">
          uploaded {new Date(t.created_at).toLocaleString()} · bullets use <code>\{t.bullet_command}</code>
        </span>
        <div className="ml-auto flex gap-2">
          {!t.is_active && <Button size="sm" variant="outline" onClick={activate}>Make active</Button>}
          <Link href={`/studio/${t.id}`}><Button size="sm" variant="ghost">Open in studio</Button></Link>
          <Button size="sm" variant="ghost" onClick={() => setShowPdf((v) => !v)}>
            {showPdf ? "Hide PDF" : "View PDF"}
          </Button>
        </div>
      </div>
      <p className="mt-2 text-sm">
        Editable regions:{" "}
        {t.regions.map((r) => (
          <code key={r.name} className="mr-1.5 rounded bg-zinc-100 px-1 dark:bg-zinc-800">{r.name}</code>
        ))}
      </p>
      {t.auto_marked.length > 0 && (
        <p className="text-xs text-zinc-500">Markers were added automatically around each section; your original file is unchanged.</p>
      )}
            {t.assets.length > 0 && <p className="text-xs text-zinc-500">Assets: {t.assets.join(", ")}</p>}
      {t.build_report && <div className="mt-2"><BuildReportView report={t.build_report} /></div>}
      {check && (
        <div className="mt-2">
          <Notice tone={check.ok ? "good" : "warn"}>
            Overleaf check: {check.reference_pages} page(s) in your PDF vs {check.compiled_pages} here, text
            similarity {(check.text_similarity * 100).toFixed(0)}%. {check.message ?? "Output matches."}
          </Notice>
        </div>
      )}
      {error && <div className="mt-2"><Notice tone="bad">{error}</Notice></div>}
      {showPdf && <div className="mt-3"><PdfPreview path={`/resumes/${t.id}/pdf`} title={`Template ${t.id}`} /></div>}
    </Card>
  );
}
