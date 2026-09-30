"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Notice, SectionTitle } from "@/components/ui";
import { blobUrl, call } from "@/lib/client";
import type { JobDetail } from "@/lib/types";

/** Shows a backend image (screenshot) through the authenticated proxy. */
function AuthedImage({ path, alt }: { path: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let revoke: string | null = null;
    void blobUrl(path).then((u) => { revoke = u; setUrl(u); }).catch(() => undefined);
    return () => { if (revoke) URL.revokeObjectURL(revoke); };
  }, [path]);
  // eslint-disable-next-line @next/next/no-img-element
  return url ? <img src={url} alt={alt} className="max-h-[480px] w-full rounded border border-zinc-200 object-contain dark:border-zinc-800" /> : null;
}

async function download(path: string, name: string) {
  const url = await blobUrl(path);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function PromptBox({ job, onDone }: { job: JobDetail; onDone: () => Promise<void> }) {
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!job.prompt) return null;
  const p = job.prompt;
  async function send() {
    setError(null);
    try {
      await call(`/jobs/${job.id}/prompt`, { method: "POST", json: { answer } });
      setAnswer("");
      await onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }
  return (
    <Card className="space-y-3 border-amber-400 dark:border-amber-600">
      <p className="font-medium">Action needed: {p.question}</p>
      <p className="text-xs text-zinc-500">
        The portal agent is paused and waiting for you (up to 10 minutes from {new Date(p.asked_at).toLocaleTimeString()}). It never tries to bypass
        {p.kind === "otp" ? " OTPs" : " CAPTCHAs"}; it only types what you enter here.
      </p>
      {p.screenshot_file_id && <AuthedImage path={`/jobs/${job.id}/files/${p.screenshot_file_id}`} alt="What the agent sees" />}
      <div className="flex gap-2">
        <Input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={p.kind === "otp" ? "OTP code" : "CAPTCHA text"} autoComplete="one-time-code" />
        <Button onClick={send} disabled={!answer.trim()}>Send to agent</Button>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
    </Card>
  );
}

export function EmailAndFiles({ job }: { job: JobDetail }) {
  const shots = job.files.filter((f) => f.kind === "screenshot" || f.kind === "confirmation_screenshot");
  const docs = job.files.filter((f) => f.kind === "jd_attachment" || f.kind === "email_attachment");
  const [shown, setShown] = useState<number | null>(null);
  return (
    <>
      {job.source_emails.length > 0 && (
        <section>
          <SectionTitle title="Source email" subtitle={job.apply_mode ? `Apply mode: ${job.apply_mode === "direct_link" ? "Direct link" : "Read email then apply"}` : undefined} />
          {job.source_emails.map((e) => (
            <Card key={e.id} className="mb-3 space-y-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{e.subject}</span>
                <Badge>{e.classification.replace("_", " ")}</Badge>
              </div>
              <p className="text-xs text-zinc-500">
                From {e.original_sender ?? e.sender}{e.original_sender ? ` (forwarded by ${e.sender})` : ""} · {new Date(e.received_at).toLocaleString()}
              </p>
              {(e.resolved_link || e.extracted_link) && (
                <p className="break-all text-xs">
                  {e.link_safe === true && <Badge tone="good">link verified</Badge>}
                  {e.link_safe === false && <Badge tone="bad">link blocked</Badge>}{" "}
                  {e.resolved_link ?? e.extracted_link}
                  {e.link_safe === false && <span className="block text-red-700 dark:text-red-400">{e.link_check_reason}</span>}
                </p>
              )}
              <details>
                <summary className="cursor-pointer text-xs">Show email</summary>
                {e.body_html ? (
                  // sandbox="" : no scripts, no same-origin access, no forms, no navigation of this page
                  <iframe sandbox="" srcDoc={e.body_html} title={`Email ${e.id}`} className="mt-2 h-96 w-full rounded border border-zinc-200 bg-white dark:border-zinc-800" />
                ) : (
                  <pre className="mt-2 whitespace-pre-wrap text-xs">{e.body_text}</pre>
                )}
              </details>
            </Card>
          ))}
        </section>
      )}
      {(docs.length > 0 || shots.length > 0) && (
        <section>
          <SectionTitle title="Attachments & screenshots" subtitle="Every portal step is screenshotted for your records." />
          <Card className="space-y-2 text-sm">
            {docs.map((f) => (
              <button key={f.id} className="block text-left underline" onClick={() => download(`/jobs/${job.id}/files/${f.id}`, f.original_name ?? "attachment")}>
                📎 {f.original_name} <span className="text-xs text-zinc-500">({f.kind.replace("_", " ")})</span>
              </button>
            ))}
            <div className="flex flex-wrap gap-2">
              {shots.map((f) => (
                <Button key={f.id} size="sm" variant={shown === f.id ? "default" : "outline"} onClick={() => setShown(shown === f.id ? null : f.id)}>
                  {f.original_name?.replace(".png", "").replace(/_/g, " ")} · {new Date(f.created_at).toLocaleTimeString()}
                </Button>
              ))}
            </div>
            {shown && <AuthedImage path={`/jobs/${job.id}/files/${shown}`} alt="Portal screenshot" />}
          </Card>
        </section>
      )}
    </>
  );
}
