"use client";

import Link from "next/link";
import { IconBell } from "@/components/icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { call } from "@/lib/client";
import type { Notification } from "@/lib/types";
import { cn } from "@/lib/utils";

export function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 16, left: 16 });
  const buttonRef = useRef<HTMLButtonElement>(null);

  // The sidebar scrolls (overflow), which would clip an absolutely-positioned panel, so the
  // panel is fixed to the viewport and anchored next to the button, kept on screen.
  function toggle() {
    const r = buttonRef.current?.getBoundingClientRect();
    if (r) {
      const width = Math.min(320, window.innerWidth - 16);
      const wide = window.innerWidth >= 768;
      setPos({
        top: wide ? Math.max(8, Math.min(r.top, window.innerHeight - 440)) : r.bottom + 6,
        left: wide ? r.right + 8 : Math.max(8, Math.min(r.left, window.innerWidth - width - 8)),
      });
    }
    setOpen((v) => !v);
  }

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const load = useCallback(async () => {
    try {
      setItems(await call<Notification[]>("/notifications?limit=20"));
    } catch {
      /* backend down: keep the last list */
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, [load]);

  const unread = items.filter((n) => !n.read_at).length;

  async function markRead(id: number) {
    await call(`/notifications/${id}/read`, { method: "POST" });
  }

  async function readAll() {
    await call("/notifications/read-all", { method: "POST" });
    await load();
  }

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={toggle}
        aria-expanded={open}
        className="relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-100"
        aria-label={`Notifications (${unread} unread)`}
      >
        <IconBell size={17} className="shrink-0 opacity-80" />
        <span className="flex-1 text-left">Notifications</span>
        {unread > 0 && <span className="min-w-5 rounded-full bg-red-500 px-1.5 text-center text-[10px] font-semibold leading-5 text-white">{unread}</span>}
      </button>
      {open && <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden />}
      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          style={{ top: pos.top, left: pos.left }}
          className="fixed z-50 w-96 max-w-[calc(100vw-16px)] rounded-xl border border-zinc-200 bg-white p-2 shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
        >
          <div className="mb-1 flex items-center justify-between px-1">
            <span className="text-xs font-medium">Notifications</span>
            {unread > 0 && <button className="text-xs underline" onClick={readAll}>mark all read</button>}
          </div>
          {items.length === 0 && <p className="px-1 py-2 text-xs text-zinc-500">Nothing yet.</p>}
          <ul className="max-h-96 space-y-1 overflow-y-auto">
            {items.map((n) => (
              <li key={n.id} className={cn("rounded-lg p-2.5 text-xs", !n.read_at && "bg-brand-50/60 dark:bg-brand-500/10")}>
                <p className={cn("font-medium", n.level === "error" && "text-red-700 dark:text-red-400", n.level === "warning" && "text-amber-700 dark:text-amber-400")}>{n.title}</p>
                {n.body && <p className="mt-0.5 break-words text-zinc-500">{n.body}</p>}
                {n.kind === "job.ready" && n.job_id && <ApproveActions jobId={n.job_id} onDone={async () => { await markRead(n.id); await load(); }} onClose={() => setOpen(false)} />}
                {n.kind === "job.ready_auto" && n.job_id && <ApproveActions auto jobId={n.job_id} onDone={async () => { await markRead(n.id); await load(); }} onClose={() => setOpen(false)} />}
                <p className="mt-0.5 text-[10px] text-zinc-400">
                  {new Date(n.created_at).toLocaleString()}
                  {n.job_id && <> · <Link href={`/jobs/${n.job_id}`} className="underline" onClick={() => setOpen(false)}>job #{n.job_id}</Link></>}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ApproveActions({ jobId, onDone, onClose, auto = false }: { jobId: number; onDone: () => Promise<void>; onClose: () => void; auto?: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);
  async function cancel() {
    if (!window.confirm("Don't apply to this job? The resume stays saved.")) return;
    setState("busy");
    try {
      await call(`/jobs/${jobId}/approval`, { method: "DELETE" });
      setState("done");
      setMessage("Cancelled. It won't be applied.");
      await onDone();
    } catch (e) {
      setState("error");
      setMessage(e instanceof Error ? e.message : "failed");
    }
  }

  async function approve() {
    if (!auto && !window.confirm("Approve this resume? If the job has a portal link, the agent will submit this exact version.")) return;
    setState("busy");
    try {
      const job = await call<{ apply_url: string | null }>(`/jobs/${jobId}/approve`, { method: "POST", json: {} });
      setState("done");
      setMessage(job.apply_url ? "Approved. Submitting through the portal." : "Approved. No portal link, so apply yourself.");
      await onDone();
    } catch (e) {
      setState("error");
      setMessage(e instanceof Error ? e.message : "failed");
    }
  }
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2">
      <Link href={`/jobs/${jobId}`} onClick={onClose} className="rounded border border-zinc-300 px-2 py-0.5 text-[11px] dark:border-zinc-700">Review</Link>
      {state !== "done" && (
        <button onClick={approve} disabled={state === "busy"} className="rounded bg-zinc-900 px-2 py-0.5 text-[11px] text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900">
          {state === "busy" ? "Working…" : auto ? "Apply now" : "Approve"}
        </button>
      )}
      {auto && state !== "done" && (
        <button onClick={cancel} disabled={state === "busy"} className="rounded border border-red-300 px-2 py-0.5 text-[11px] text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-400">
          Don&apos;t apply
        </button>
      )}
      {message && <span className={state === "error" ? "text-red-600" : "text-emerald-700 dark:text-emerald-400"}>{message}</span>}
    </div>
  );
}
