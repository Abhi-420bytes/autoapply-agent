"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { AccountCard } from "@/components/email/AccountCard";
import { PROVIDER_LABEL, type Account, type Message } from "@/components/email/types";
import { Badge, Button, Card, Input, Label, Notice, SectionTitle, Select } from "@/components/ui";
import { call } from "@/lib/client";
import type { Portal } from "@/app/settings/portals/page";

export default function EmailSettingsPage() {
  return <Suspense><EmailSettings /></Suspense>;
}

function EmailSettings() {
  const params = useSearchParams();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [portals, setPortals] = useState<Portal[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);

  const load = useCallback(async () => {
    const [a, p, m] = await Promise.all([call<Account[]>("/email/accounts"), call<Portal[]>("/portals"), call<Message[]>("/email/messages?limit=30")]);
    setAccounts(a);
    setPortals(p);
    setMessages(m);
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Email</h1>
        <p className="text-sm text-zinc-500">
          Inboxes the agent watches for job notices. Only messages from your sender rules are downloaded; everything else is never read.
        </p>
      </header>
      {params.get("connected") && <Notice tone="good">Connected {params.get("connected")}.</Notice>}
      {params.get("error") && <Notice tone="bad">Sign-in failed: {params.get("error")}</Notice>}
      {portals.length === 0 && <Notice tone="warn">Add your placement portal first (Settings → Portals), so rules can link to it and links can be safety-checked.</Notice>}

      <section className="space-y-3">
        <SectionTitle title="Accounts" />
        {accounts.map((a) => <AccountCard key={a.id} account={a} portals={portals} onChange={load} />)}
        <AddAccount onAdded={load} />
      </section>

      <OAuthApps />

      <section>
        <SectionTitle title="Recent allowlisted emails" subtitle="What the agent did with each message it was allowed to read." />
        <Card className="overflow-x-auto p-0">
          {messages.length === 0 ? <p className="p-3 text-sm text-zinc-500">Nothing yet.</p> : (
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="border-b border-zinc-200 text-zinc-500 dark:border-zinc-800">
                <tr><th className="px-3 py-2">Received</th><th className="px-3 py-2">From</th><th className="px-3 py-2">Subject</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Link</th><th className="px-3 py-2">Job</th></tr>
              </thead>
              <tbody>
                {messages.map((m) => (
                  <tr key={m.id} className="border-b border-zinc-100 align-top last:border-0 dark:border-zinc-800">
                    <td className="px-3 py-2 tabular-nums">{new Date(m.received_at).toLocaleString()}</td>
                    <td className="px-3 py-2">{m.original_sender ? <>{m.original_sender}<br /><span className="text-zinc-500">via {m.sender}</span></> : m.sender}</td>
                    <td className="max-w-[260px] px-3 py-2">{m.subject}</td>
                    <td className="px-3 py-2"><Badge>{m.classification.replace("_", " ")}</Badge></td>
                    <td className="max-w-[260px] break-all px-3 py-2">
                      {m.link_safe === true && <Badge tone="good">safe</Badge>}
                      {m.link_safe === false && <Badge tone="bad">blocked</Badge>}
                      <span className="ml-1">{m.resolved_link ?? m.extracted_link ?? "–"}</span>
                      {m.link_safe === false && <p className="text-red-700 dark:text-red-400">{m.link_check_reason}</p>}
                    </td>
                    <td className="px-3 py-2">{m.job_id ? <a className="underline" href={`/jobs/${m.job_id}`}>#{m.job_id}</a> : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </section>
    </div>
  );
}

function AddAccount({ onAdded }: { onAdded: () => Promise<void> }) {
  const [provider, setProvider] = useState<Account["provider"]>("outlook_graph");
  const [address, setAddress] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("993");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function add() {
    setError(null);
    try {
      await call("/email/accounts", { method: "POST", json: {
        provider, address, ...(provider === "imap" ? { imap_host: host, imap_port: Number(port), imap_password: password } : {}),
      } });
      setAddress(""); setPassword("");
      await onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  return (
    <Card className="space-y-3 border-dashed">
      <p className="text-sm font-medium">Add email account</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <Label htmlFor="prov">Provider</Label>
          <Select id="prov" value={provider} onChange={(e) => setProvider(e.target.value as Account["provider"])}>
            {Object.entries(PROVIDER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </div>
        <div className="sm:col-span-2"><Label htmlFor="addr">Address</Label><Input id="addr" type="email" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="you@college.edu" /></div>
        {provider === "imap" && (
          <>
            <div><Label htmlFor="host">IMAP host</Label><Input id="host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="imap.gmail.com" /></div>
            <div><Label htmlFor="port">Port</Label><Input id="port" value={port} onChange={(e) => setPort(e.target.value)} /></div>
            <div><Label htmlFor="pw">App password</Label><Input id="pw" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
          </>
        )}
      </div>
      {provider !== "imap" && <p className="text-xs text-zinc-500">After adding, click <b>Connect</b> to sign in with {PROVIDER_LABEL[provider]} (read-only mail access).</p>}
      {error && <Notice tone="bad">{error}</Notice>}
      <Button onClick={add} disabled={!address}>Add account</Button>
    </Card>
  );
}

function OAuthApps() {
  const [status, setStatus] = useState<Record<string, string | null>>({});
  const [values, setValues] = useState<Record<string, string>>({});
  const names = ["google_client_id", "google_client_secret", "microsoft_client_id", "microsoft_client_secret", "microsoft_tenant"];
  const load = async () => {
    const s = await call<{ name: string; masked: string | null }[]>("/settings/secrets");
    setStatus(Object.fromEntries(s.map((x) => [x.name, x.masked])));
  };
  useEffect(() => {
    void load();
  }, []);
  async function save(name: string) {
    await call(`/settings/secrets/${name}`, { method: "PUT", json: { value: values[name] } });
    setValues((v) => ({ ...v, [name]: "" }));
    await load();
  }
  return (
    <section>
      <details className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <summary className="cursor-pointer text-sm font-medium">OAuth app setup (needed once for Outlook/Gmail sign-in)</summary>
        <div className="mt-3 space-y-3 text-sm">
          <p className="text-xs text-zinc-500">
            Create an OAuth app and add <code>http://localhost:3000/oauth/callback</code> as its redirect URI.{" "}
            <b>Google:</b> Cloud Console → APIs &amp; Services → enable Gmail API → OAuth client (Web). <b>Microsoft:</b> Azure portal → App registrations →
            Web redirect URI, API permissions <code>Mail.Read</code>, <code>User.Read</code>, <code>offline_access</code>. Tenant: <code>common</code>, or
            <code>organizations</code> for college accounts. If your college blocks third-party apps, add an Outlook rule that forwards Havlock mail to Gmail,
            and turn on &quot;accept forwards&quot; for the Gmail rule.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {names.map((n) => (
              <div key={n}>
                <Label htmlFor={n}>{n.replace(/_/g, " ")}</Label>
                <div className="flex gap-2">
                  <Input id={n} type={n.endsWith("secret") ? "password" : "text"} autoComplete="off" value={values[n] ?? ""}
                    placeholder={status[n] ?? (n === "microsoft_tenant" ? "common" : "not set")} onChange={(e) => setValues((v) => ({ ...v, [n]: e.target.value }))} />
                  <Button size="sm" variant="outline" className="h-9" onClick={() => save(n)} disabled={!values[n]}>Save</Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </details>
    </section>
  );
}
