"use client";

import { useEffect, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { blobUrl } from "@/lib/client";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

/** Renders a backend PDF (fetched through the authenticated proxy) with react-pdf. */
export default function PdfViewer({ path, width = 560 }: { path: string; width?: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [pages, setPages] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let live = true;
    setUrl(null);
    setError(null);
    blobUrl(path)
      .then((u) => {
        revoke = u;
        if (live) setUrl(u);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : "failed"));
    return () => {
      live = false;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [path]);

  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!url) return <p className="text-sm text-zinc-500">Loading PDF…</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-zinc-500">
        {pages} page{pages === 1 ? "" : "s"} ·{" "}
        <a href={url} download="resume.pdf" className="underline">download</a>
      </p>
      <Document file={url} onLoadSuccess={(d) => setPages(d.numPages)} loading={<p className="text-sm text-zinc-500">Rendering…</p>}>
        {Array.from({ length: pages }, (_, i) => (
          <Page
            key={i}
            pageNumber={i + 1}
            width={width}
            className="mb-3 overflow-hidden rounded border border-zinc-200 shadow-sm dark:border-zinc-800"
          />
        ))}
      </Document>
    </div>
  );
}
