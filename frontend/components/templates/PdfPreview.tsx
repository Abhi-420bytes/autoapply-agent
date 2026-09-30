"use client";

import { useEffect, useState } from "react";
import { blobUrl } from "@/lib/client";

/** Loads a PDF through the authenticated proxy and shows it with the browser's viewer.
 * (The Resume Studio in Phase 6 replaces this with react-pdf.) */
export function PdfPreview({ path, title }: { path: string; title: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let live = true;
    setUrl(null);
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
    <iframe
      src={url}
      title={title}
      className="h-[70vh] w-full rounded-md border border-zinc-200 bg-white dark:border-zinc-800"
    />
  );
}
