"use client";

import { useEffect, useState } from "react";
import { Button, Card, Input, Label, Notice, SectionTitle, Select } from "@/components/ui";
import { call } from "@/lib/client";

export type Portal = {
  id: number;
  name: string;
  base_url: string;
  allowed_domains: string[];
  login_method: "password" | "sso" | "otp" | "manual";
  enabled: boolean;
  has_credentials: boolean;
  session_saved_at: string | null;
};

export default function PortalsPage() {
  const [portals, setPortals] = useState<Portal[]>([]);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const load = async () => setPortals(await call<Portal[]>("/portals"));
  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Portals</h1>
        <p className="text-sm text-zinc-500">
          Placement portals the agent may open. A job link from email is opened only if its final address (after unwrapping SafeLinks and
          redirects) is on one of these allowed domains.
        </p>
      </header>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {portals.map((p) => <PortalForm key={p.id} portal={p} onDone={async (t) => { setMsg(t); await load(); }} />)}
      <SectionTitle title="Add portal" />
      <PortalForm onDone={async (t) => { setMsg(t); await load(); }} />
    </div>
  );
}

function PortalForm({ portal, onDone }: { portal?: Portal; onDone: (m: { tone: "good" | "bad"; text: string }) => Promise<void> }) {
  const [name, setName] = useState(portal?.name ?? "");
  const [url, setUrl] = useState(portal?.base_url ?? "https://");
  const [domains, setDomains] = useState((portal?.allowed_domains ?? []).join(", "));
  const [login, setLogin] = useState(portal?.login_method ?? "manual");

  async function save() {
    const body = { name, base_url: url, allowed_domains: domains.split(",").map((d) => d.trim()).filter(Boolean), login_method: login, enabled: true };
    try {
      await call(portal ? `/portals/${portal.id}` : "/portals", { method: portal ? "PUT" : "POST", json: body });
      await onDone({ tone: "good", text: `Saved ${name}.` });
      if (!portal) { setName(""); setUrl("https://"); setDomains(""); }
    } catch (e) {
      await onDone({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  async function remove() {
    if (!portal || !window.confirm(`Delete portal ${portal.name}? Sender rules linked to it will lose their portal.`)) return;
    await call(`/portals/${portal.id}`, { method: "DELETE" });
    await onDone({ tone: "good", text: "Deleted." });
  }

  return (
    <Card className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div><Label htmlFor={`pn${portal?.id ?? "new"}`}>Name</Label><Input id={`pn${portal?.id ?? "new"}`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Havlock" /></div>
        <div><Label htmlFor={`pu${portal?.id ?? "new"}`}>Base URL</Label><Input id={`pu${portal?.id ?? "new"}`} value={url} onChange={(e) => setUrl(e.target.value)} /></div>
        <div>
          <Label htmlFor={`pd${portal?.id ?? "new"}`}>Allowed domains (comma-separated)</Label>
          <Input id={`pd${portal?.id ?? "new"}`} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="app.havlock.in, *.havlock.in" />
          <p className="mt-0.5 text-[11px] text-zinc-500">Leave empty to allow only the base URL&apos;s host. <code>*.x.com</code> allows x.com and its subdomains.</p>
        </div>
        <div>
          <Label htmlFor={`pl${portal?.id ?? "new"}`}>Login method</Label>
          <Select id={`pl${portal?.id ?? "new"}`} value={login} onChange={(e) => setLogin(e.target.value as Portal["login_method"])}>
            <option value="manual">Manual (log in once in the agent&apos;s browser)</option>
            <option value="password">Username + password</option>
            <option value="sso">College SSO</option>
            <option value="otp">OTP</option>
          </Select>
        </div>
      </div>
      {portal && <Credentials portal={portal} onDone={onDone} />}
      {portal && <SignedInSession portal={portal} onDone={onDone} />}
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={!name || !url}>{portal ? "Save" : "Add portal"}</Button>
        {portal && <Button size="sm" variant="ghost" className="text-red-600" onClick={remove}>Delete</Button>}
      </div>
    </Card>
  );
}

function Credentials({ portal, onDone }: { portal: Portal; onDone: (m: { tone: "good" | "bad"; text: string }) => Promise<void> }) {
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  async function save() {
    try {
      await call(`/portals/${portal.id}/credentials`, { method: "PUT", json: { username: user, password: pw } });
      setUser(""); setPw("");
      await onDone({ tone: "good", text: "Portal login saved (encrypted)." });
    } catch (e) {
      await onDone({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }
  async function clear() {
    await call(`/portals/${portal.id}/credentials`, { method: "DELETE" });
    await onDone({ tone: "good", text: "Portal login removed." });
  }
  return (
    <div className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <p className="mb-2 text-xs font-medium">
        Portal login {portal.has_credentials ? "(saved; enter new values to replace)" : "(not set; the agent can't log in without it)"}
      </p>
      <div className="flex flex-wrap gap-2">
        <Input className="w-56" placeholder="username / email" value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" aria-label="Portal username" />
        <Input className="w-56" type="password" placeholder="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" aria-label="Portal password" />
        <Button size="sm" variant="outline" className="h-9" onClick={save} disabled={!user || !pw}>Save login</Button>
        {portal.has_credentials && <Button size="sm" variant="ghost" className="h-9 text-red-600" onClick={clear}>Remove</Button>}
      </div>
    </div>
  );
}

function SignedInSession({ portal, onDone }: { portal: Portal; onDone: (m: { tone: "good" | "bad"; text: string }) => Promise<void> }) {
  const cmd = `backend/.venv/bin/python backend/scripts/portal_login.py ${portal.id}`;
  async function clear() {
    await call(`/portals/${portal.id}/session`, { method: "DELETE" });
    await onDone({ tone: "good", text: "Saved session removed." });
  }
  return (
    <div className="rounded-md border border-zinc-200 p-3 text-xs dark:border-zinc-800">
      <p className="mb-1 font-medium">
        Signed-in session{" "}
        {portal.session_saved_at ? `(saved ${new Date(portal.session_saved_at).toLocaleString()})` : "(none yet)"}
      </p>
      <p className="text-zinc-500">
        If the portal shows a CAPTCHA (&quot;I&apos;m not a robot&quot;) before login, the agent stops. It never solves CAPTCHAs. Sign in once
        yourself: in a terminal in the project folder run the command below, solve the CAPTCHA and log in in the window that opens,
        then press Enter. The agent reuses that session until it expires; you&apos;ll get a notification to do it again.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <code className="break-all rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-900">{cmd}</code>
        <Button size="sm" variant="outline" onClick={() => void navigator.clipboard?.writeText(cmd)}>Copy</Button>
        {portal.session_saved_at && <Button size="sm" variant="ghost" className="text-red-600" onClick={clear}>Remove session</Button>}
      </div>
    </div>
  );
}
