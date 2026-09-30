"use client";

import { useState } from "react";
import { Badge, Button, Card, Input, Label, Notice, Select, Switch } from "@/components/ui";
import { call } from "@/lib/client";
import type { Portal } from "@/app/settings/portals/page";
import { PROVIDER_LABEL, type Account, type Rule } from "./types";

export function AccountCard({ account: a, portals, onChange }: { account: Account; portals: Portal[]; onChange: () => Promise<void> }) {
  const [msg, setMsg] = useState<{ tone: "good" | "bad" | "neutral"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<{ tone: "good" | "bad" | "neutral"; text: string } | void>) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      if (r) setMsg(r);
      await onChange();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(false);
    }
  }

  const test = () => run(async () => {
    const r = await call<{ ok: boolean; message: string }>(`/email/accounts/${a.id}/test`, { method: "POST" });
    return { tone: r.ok ? "good" : "bad", text: r.message };
  });
  const connect = () => run(async () => {
    const r = await call<{ auth_url: string }>(`/email/accounts/${a.id}/oauth/start`, { method: "POST" });
    window.location.href = r.auth_url;
  });
  const sync = () => run(async () => {
    await call(`/email/accounts/${a.id}/sync`, { method: "POST" });
    return { tone: "neutral", text: "Sync queued: the worker checks this inbox within a minute." };
  });
  const remove = () => run(async () => {
    if (!window.confirm(`Remove ${a.address}? Its rules are deleted; stored emails and jobs stay.`)) return;
    await call(`/email/accounts/${a.id}`, { method: "DELETE" });
  });

  const tone = a.status === "connected" ? "good" : a.status === "error" ? "bad" : "warn";
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{a.address}</span>
        <Badge>{PROVIDER_LABEL[a.provider]}</Badge>
        <Badge tone={tone}>{a.status}</Badge>
        {a.provider !== "imap" && !a.oauth_connected && <Badge tone="warn">not connected</Badge>}
        <span className="text-xs text-zinc-500">{a.last_sync_at ? `last checked ${new Date(a.last_sync_at).toLocaleString()}` : "never checked"}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          {a.provider !== "imap" && <Button size="sm" onClick={connect} disabled={busy}>{a.oauth_connected ? "Reconnect" : "Connect"}</Button>}
          <Button size="sm" variant="outline" onClick={test} disabled={busy}>Test connection</Button>
          <Button size="sm" variant="outline" onClick={sync} disabled={busy}>Check now</Button>
          <Button size="sm" variant="ghost" className="text-red-600" onClick={remove} disabled={busy}>Remove</Button>
        </div>
      </div>
      {a.last_error && <p className="break-words text-xs text-red-700 dark:text-red-400">{a.last_error}</p>}
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}

      <div>
        <p className="mb-1 text-xs font-medium">Sender rules: only mail from these senders is ever read</p>
        <div className="space-y-2">
          {a.rules.map((r) => <RuleRow key={r.id} rule={r} portals={portals} onChange={onChange} />)}
          <NewRule accountId={a.id} portals={portals} onChange={onChange} />
        </div>
      </div>
    </Card>
  );
}

function RuleRow({ rule: r, portals, onChange }: { rule: Rule; portals: Portal[]; onChange: () => Promise<void> }) {
  const patch = async (body: Partial<Rule>) => {
    await call(`/email/rules/${r.id}`, { method: "PATCH", json: body });
    await onChange();
  };
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 p-2 text-sm dark:border-zinc-800">
      <Switch label={`Enable rule ${r.sender_match}`} checked={r.enabled} onChange={(v) => patch({ enabled: v })} />
      <code className="min-w-[140px]">{r.sender_match}</code>
      <Select className="h-8 w-36 text-xs" value={r.portal_id ?? ""} onChange={(e) => patch({ portal_id: e.target.value ? Number(e.target.value) : null })} aria-label="Portal">
        <option value="">No portal</option>
        {portals.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <div className="inline-flex overflow-hidden rounded-md border border-zinc-300 text-xs dark:border-zinc-700" role="group" aria-label="Apply mode">
        {(["direct_link", "read_email_then_apply"] as const).map((m) => (
          <button key={m} onClick={() => patch({ apply_mode: m })}
            className={`px-2 py-1 ${r.apply_mode === m ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : ""}`}>
            {m === "direct_link" ? "Direct link" : "Read email then apply"}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-1 text-xs">
        delay
        <Input className="h-8 w-20 text-xs" type="number" min={0} defaultValue={r.delay_minutes ?? ""} placeholder="global"
          onBlur={(e) => patch({ delay_minutes: e.target.value === "" ? null : Number(e.target.value) })} aria-label="Delay minutes" />
        min
      </label>
      <label className="flex items-center gap-1 text-xs">
        <Switch label="Accept forwarded" checked={r.match_forwarded} onChange={(v) => patch({ match_forwarded: v })} /> accept forwards
      </label>
      <Button size="sm" variant="ghost" className="ml-auto text-red-600" onClick={async () => { await call(`/email/rules/${r.id}`, { method: "DELETE" }); await onChange(); }}>✕</Button>
    </div>
  );
}

function NewRule({ accountId, portals, onChange }: { accountId: number; portals: Portal[]; onChange: () => Promise<void> }) {
  const [sender, setSender] = useState("");
  const [portal, setPortal] = useState<string>(portals[0] ? String(portals[0].id) : "");
  const [error, setError] = useState<string | null>(null);
  async function add() {
    setError(null);
    try {
      await call(`/email/accounts/${accountId}/rules`, { method: "POST", json: { sender_match: sender, portal_id: portal ? Number(portal) : null } });
      setSender("");
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div>
        <Label htmlFor={`ns${accountId}`}>Sender address or @domain</Label>
        <Input id={`ns${accountId}`} className="h-8 w-56 text-xs" value={sender} onChange={(e) => setSender(e.target.value)} placeholder="noreply@haveloc.com or @haveloc.com" />
      </div>
      <Select className="h-8 w-36 text-xs" value={portal} onChange={(e) => setPortal(e.target.value)} aria-label="Portal for new rule">
        <option value="">No portal</option>
        {portals.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Select>
      <Button size="sm" variant="outline" onClick={add} disabled={sender.trim().length < 3}>Add rule</Button>
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
