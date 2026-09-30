"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Avatar, Badge, Button, Card, EmptyState, Input, Label, Notice, PageHeader, SectionTitle, Select, StatCard, Switch, Tabs } from "@/components/ui";
import { IconBuilding, IconEye, IconMail, IconSearch, IconSend } from "@/components/icons";
import { call } from "@/lib/client";

type OutreachEmail = {
  id: number;
  company_id: number;
  company: string;
  to_address: string;
  subject: string | null;
  body: string | null;
  status: string;
  warnings: string[];
  resume_id: number | null;
  job_id: number | null;
  error: string | null;
  sent_at: string | null;
  auto_send_at: string | null;
};

type Company = {
  id: number;
  name: string;
  domain: string;
  website: string;
  location: string | null;
  size: string | null;
  careers_url: string | null;
  source: string;
  status: string;
  summary: { what_they_do?: string; tech?: string[]; hook?: string } | null;
  emails: { address: string; source_url: string; kind: string; source?: "site" | "web" | "ai" | "user" }[];
  error: string | null;
  email: OutreachEmail | null;
};

type Overview = { companies: Company[]; sent_last_24h: number; daily_cap: number; search_ready: boolean };

type Settings = {
  outreach_enabled: boolean;
  outreach_locations: string[];
  outreach_roles: string[];
  outreach_company_kinds: ("startups" | "companies")[];
  outreach_daily_cap: number;
  outreach_new_per_search: number;
  outreach_account_id: number | null;
  outreach_company_sizes: ("startup" | "mid-size" | "large" | "unknown")[];
  outreach_auto_send: boolean;
  outreach_send_delay_minutes: number;
  outreach_closing_note: string;
};

type Account = { id: number; address: string; provider: string; status: string };

const STATUS: Record<string, { label: string; tone: "good" | "bad" | "warn" | "neutral" }> = {
  new: { label: "researching soon", tone: "neutral" },
  researched: { label: "email found", tone: "neutral" },
  queued: { label: "tailoring resume", tone: "neutral" },
  no_email: { label: "no email or careers page", tone: "warn" },
  watching: { label: "watching careers page", tone: "neutral" },
  skipped: { label: "skipped", tone: "neutral" },
  failed: { label: "failed", tone: "bad" },
  resume_pending: { label: "tailoring resume", tone: "neutral" },
  ready: { label: "draft ready", tone: "warn" },
  approved: { label: "sending", tone: "neutral" },
  sent: { label: "sent", tone: "good" },
};

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function OutreachPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [site, setSite] = useState("");
  const [siteEmail, setSiteEmail] = useState("");
  const [siteName, setSiteName] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"drafts" | "companies" | "directories" | "setup">("drafts");

  const load = useCallback(async () => {
    setData(await call<Overview>("/outreach"));
  }, []);
  useEffect(() => {
    void load();
    void call<Settings>("/settings").then(setSettings);
    void call<Account[]>("/email/accounts").then(setAccounts);
    const t = setInterval(() => void load(), 15000);
    return () => clearInterval(t);
  }, [load]);

  async function act<T>(fn: () => Promise<T>, ok: string) {
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "companies" in (r as object)) setData(r as unknown as Overview);
      setMsg({ tone: "good", text: ok });
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings(patch: Partial<Settings>) {
    await act(async () => setSettings(await call<Settings>("/settings", { method: "PATCH", json: patch })), "Saved.");
  }

  const drafts = (data?.companies ?? []).filter((c) => c.email && ["ready", "failed", "approved"].includes(c.email.status));
  const watching = (data?.companies ?? []).filter((c) => c.status === "watching").length;
  const inProgress = (data?.companies ?? []).filter((c) => ["new", "researched", "queued"].includes(c.status) || c.email?.status === "resume_pending").length;

  return (
    <div className="space-y-6">
      <PageHeader
        icon={<IconSend size={20} />}
        title="Outreach"
        subtitle="Startups and mid-size companies, found for you. The agent reads each company's site, tailors your resume and writes a short email about your most relevant projects. Clean drafts go out automatically; anything with a warning waits for you."
        actions={
          <Button disabled={busy || !data?.search_ready} onClick={() => act(() => call<Overview>("/outreach/search", { method: "POST" }), "Search done. New companies are researched over the next few minutes.")}>
            <IconSearch size={15} /> Find companies now
          </Button>
        }
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Sent today" value={`${data?.sent_last_24h ?? 0} / ${data?.daily_cap ?? 0}`} hint="last 24 hours" tone="good" icon={<IconSend size={16} />} />
        <StatCard label="Drafts" value={drafts.length} hint="ready, scheduled or failed" tone="warn" icon={<IconMail size={16} />} active={tab === "drafts"} onClick={() => setTab("drafts")} />
        <StatCard label="Companies" value={data?.companies.length ?? 0} hint={`${inProgress} in progress`} icon={<IconBuilding size={16} />} active={tab === "companies"} onClick={() => setTab("companies")} />
        <StatCard label="Watching careers pages" value={watching} hint="no email published" tone="accent" icon={<IconEye size={16} />} active={tab === "companies"} onClick={() => setTab("companies")} />
      </div>
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "drafts", label: "Drafts", count: drafts.length },
          { value: "companies", label: "Companies", count: data?.companies.length ?? 0 },
          { value: "directories", label: "Startup directories" },
          { value: "setup", label: "Setup" },
        ]}
      />
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {data && !data.search_ready && (
        <Notice tone="warn">
          Add a free Tavily key in <Link className="underline" href="/settings/outreach">Settings → Outreach</Link> so the agent can find
          companies (no card needed). You can still add company websites yourself below.
        </Notice>
      )}

      {tab === "setup" && settings && (
        <Card className="space-y-4 text-sm">
          <div className="flex items-center justify-between">
            <p className="font-medium">Automatic company search</p>
            <Switch label="Automatic company search" checked={settings.outreach_enabled} onChange={(v) => void saveSettings({ outreach_enabled: v })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="loc">Locations (comma separated)</Label>
              <Input id="loc" defaultValue={settings.outreach_locations.join(", ")} placeholder="Bengaluru, Hyderabad, Remote"
                onBlur={(e) => void saveSettings({ outreach_locations: list(e.target.value) })} />
            </div>
            <div>
              <Label htmlFor="roles">Roles (the first is used in emails)</Label>
              <Input id="roles" defaultValue={settings.outreach_roles.join(", ")} placeholder="Software Engineer, Backend Developer, AI Engineer"
                onBlur={(e) => void saveSettings({ outreach_roles: list(e.target.value) })} />
            </div>
            <div>
              <Label htmlFor="kinds">Look for</Label>
              <Select id="kinds" value={settings.outreach_company_kinds.join(",")}
                onChange={(e) => void saveSettings({ outreach_company_kinds: e.target.value.split(",") as Settings["outreach_company_kinds"] })}>
                <option value="startups,companies">Startups and mid-size companies</option>
                <option value="startups">Startups only</option>
                <option value="companies">Mid-size companies only</option>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="cap">Emails per day (max)</Label>
                <Input id="cap" type="number" min={1} max={50} defaultValue={settings.outreach_daily_cap}
                  onBlur={(e) => void saveSettings({ outreach_daily_cap: Number(e.target.value) })} />
              </div>
              <div>
                <Label htmlFor="per">New companies per search</Label>
                <Input id="per" type="number" min={1} max={20} defaultValue={settings.outreach_new_per_search}
                  onBlur={(e) => void saveSettings({ outreach_new_per_search: Number(e.target.value) })} />
              </div>
            </div>
            <fieldset className="sm:col-span-2 text-sm">
              <legend className="text-xs font-medium">Company sizes to email</legend>
              <div className="mt-1 flex flex-wrap gap-4">
                {([["startup", "Startups"], ["mid-size", "Mid-size"], ["unknown", "Size not stated"], ["large", "Large enterprises"]] as const).map(([v, label]) => (
                  <label key={v} className="flex items-center gap-2">
                    <input type="checkbox" checked={settings.outreach_company_sizes.includes(v)} onChange={(e) => {
                      const next = e.target.checked ? [...settings.outreach_company_sizes, v] : settings.outreach_company_sizes.filter((x) => x !== v);
                      if (next.length) void saveSettings({ outreach_company_sizes: next });
                    }} />
                    {label}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-xs text-zinc-500">The AI judges size from the company&apos;s own site (team size, funding stage, &quot;Fortune 500&quot;...) and must quote it.</p>
            </fieldset>
            <div className="sm:col-span-2 flex flex-wrap items-center gap-3 text-sm">
              <Switch label="Send automatically" checked={settings.outreach_auto_send} onChange={(v) => void saveSettings({ outreach_auto_send: v })} />
              <span><b>Send automatically</b> {settings.outreach_auto_send ? "ON" : "OFF"}: drafts go out after</span>
              <Input className="h-8 w-20" type="number" min={0} max={1440} defaultValue={settings.outreach_send_delay_minutes} aria-label="Minutes before sending"
                onBlur={(e) => void saveSettings({ outreach_send_delay_minutes: Number(e.target.value) })} />
              <span className="text-xs text-zinc-500">minutes (time to skip or edit). Drafts with a warning always wait for you.</span>
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="closing">Closing note (added to the end of every email)</Label>
              <textarea id="closing" className="mt-1 h-24 w-full rounded-md border border-zinc-300 bg-transparent p-2 text-sm dark:border-zinc-700"
                defaultValue={settings.outreach_closing_note}
                onBlur={(e) => e.target.value !== settings.outreach_closing_note && void saveSettings({ outreach_closing_note: e.target.value })} />
              <p className="text-xs text-zinc-500">It must mention AutoApply Agent (your rule: every email says your agent sent it). Use {"{name}"} for your name.</p>
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="acct">Send from</Label>
              <Select id="acct" value={settings.outreach_account_id ?? ""} onChange={(e) => void saveSettings({ outreach_account_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">First connected mailbox</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.address} ({a.provider})</option>)}
              </Select>
              <p className="mt-1 text-xs text-zinc-500">
                Gmail/Outlook mailboxes connected before this feature need one <b>Reconnect</b> in <Link className="underline" href="/settings/email">Settings → Email</Link> to allow sending.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy || !data?.search_ready} onClick={() => act(() => call<Overview>("/outreach/search", { method: "POST" }), "Search done. New companies are researched over the next few minutes.")}>
              Find companies now
            </Button>
            <span className="text-xs text-zinc-500 self-center">{data ? `${data.sent_last_24h} of ${data.daily_cap} sent in the last 24 hours` : ""}</span>
          </div>
        </Card>
      )}

      {tab === "directories" && <Directories />}

      {tab === "companies" && (<>
      <Card className="space-y-2 text-sm">
        <p className="font-medium">Add a company or an email yourself</p>
        <p className="text-xs text-zinc-500">
          The agent finds the email itself: a hiring address on their site or the web, else their Contact Us address, else it watches their careers
          page and applies when a matching job is posted. You can also give <b>just an email address</b>: the agent tailors the resume, writes the email and
          sends it straight away (a work address like name@company.com also lets it read the company&apos;s website; for a Gmail/personal address,
          add the company name).
        </p>
        <div className="flex flex-wrap gap-2">
          <Input className="w-64" type="email" placeholder="email address" value={siteEmail} onChange={(e) => setSiteEmail(e.target.value)} />
          <Input className="min-w-48 flex-1" placeholder="website (optional)" value={site} onChange={(e) => setSite(e.target.value)} />
          <Input className="w-48" placeholder="company name (optional)" value={siteName} onChange={(e) => setSiteName(e.target.value)} />
          <Button size="sm" className="h-9" disabled={(!site && !siteEmail) || busy} onClick={() => act(async () => {
            const r = await call<Overview>("/outreach/companies", { method: "POST", json: { website: site || null, email: siteEmail || null, name: siteName || null } });
            setSite(""); setSiteEmail(""); setSiteName(""); return r;
          }, siteEmail ? "Added. The agent prepares and sends it within a few minutes." : "Added. It's researched within a few minutes.")}>Add</Button>
        </div>
      </Card>

      <Card className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-zinc-100 text-xs text-zinc-500 dark:border-zinc-800">
              <tr><th className="px-4 py-3 font-medium">Company</th><th className="px-4 py-3 font-medium">What they do</th><th className="px-4 py-3 font-medium">How to reach them</th><th className="px-4 py-3 font-medium">Status</th><th className="px-4 py-3" /></tr>
            </thead>
            <tbody>
              {(data?.companies ?? []).map((c) => {
                const st = STATUS[c.email?.status ?? c.status] ?? { label: c.status, tone: "neutral" as const };
                return (
                  <tr key={c.id} className="border-t border-zinc-100 align-top transition-colors hover:bg-zinc-50/70 dark:border-zinc-800 dark:hover:bg-zinc-800/30">
                    <td className="px-4 py-3"><div className="flex items-start gap-3"><Avatar name={c.name} size={32} /><div className="min-w-0">{c.website ? <a className="font-medium hover:text-brand-700" href={c.website} target="_blank" rel="noreferrer noopener">{c.name}</a> : <span className="font-medium">{c.name}</span>}<div className="text-xs text-zinc-500">{c.website ? c.domain : "personal email"}{c.size ? ` · ${c.size}` : ""}{c.source === "manual" ? " · added by you" : ""}</div></div></div></td>
                    <td className="max-w-xs px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400"><span className="line-clamp-3">{c.summary?.what_they_do ?? "–"}</span></td>
                    <td className="px-4 py-3 text-xs">{c.status === "watching" && c.careers_url && !c.emails[0] ? (
                      <a className="underline" href={c.careers_url} target="_blank" rel="noreferrer noopener">careers page</a>
                    ) : c.emails[0] ? (
                      <>
                        {c.emails[0].source_url ? <a className="underline" href={c.emails[0].source_url} target="_blank" rel="noreferrer noopener" title="where it's published">{c.emails[0].address}</a> : c.emails[0].address}
                        <div className="text-zinc-500">{c.emails[0].source === "user" ? "added by you" : c.emails[0].source === "ai" ? "AI guess: waits for your OK" : c.emails[0].source === "web" ? "found on the web" : c.emails[0].kind === "general" ? "their contact-us address" : "on their site"}</div>
                      </>
                    ) : "–"}</td>
                    <td className="px-4 py-3"><Badge tone={st.tone} dot>{st.label}</Badge>{(c.email?.error ?? c.error) && <div title={c.email?.error ?? c.error ?? ""} className="mt-1 line-clamp-2 max-w-xs text-xs text-zinc-500">{c.email?.error ?? c.error}</div>}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      {c.email?.status !== "sent" && (
                        <Button size="sm" variant="ghost" onClick={() => {
                          const v = window.prompt(`Email address for ${c.name} (used first):`, c.emails[0]?.source === "user" ? c.emails[0].address : "");
                          if (v) void act(() => call<Overview>(`/outreach/companies/${c.id}/email`, { method: "PUT", json: { email: v } }), "Email saved.");
                        }}>Set email</Button>
                      )}
                      {["failed", "no_email", "skipped"].includes(c.status) && <Button size="sm" variant="ghost" onClick={() => act(() => call<Overview>(`/outreach/companies/${c.id}/retry`, { method: "POST" }), "Will research again.")}>Retry</Button>}
                      {c.email?.status !== "sent" && c.status !== "skipped" && <Button size="sm" variant="ghost" onClick={() => act(() => call<Overview>(`/outreach/companies/${c.id}/skip`, { method: "POST" }), "Skipped.")}>Skip</Button>}
                    </td>
                  </tr>
                );
              })}
              {data && data.companies.length === 0 && <tr><td colSpan={5} className="p-6 text-center text-sm text-zinc-500">No companies yet. Click Find companies now, add a startup directory, or add a company above.</td></tr>}
            </tbody>
          </table>
      </Card>
      </>)}

      {tab === "drafts" && (
        drafts.length > 0 ? (
          <section className="space-y-4">
            {drafts.map((c) => <Draft key={c.id} c={c} busy={busy} act={act} />)}
          </section>
        ) : (
          <EmptyState icon={<IconMail size={18} />} title="No drafts right now">
            Drafts appear here once a company is researched and its tailored resume is ready. Clean drafts send automatically after the review
            window; anything with a warning waits here for you.
          </EmptyState>
        )
      )}
    </div>
  );
}

function Draft({ c, busy, act }: { c: Company; busy: boolean; act: <T>(fn: () => Promise<T>, ok: string) => Promise<void> }) {
  const e = c.email as OutreachEmail;
  const [to, setTo] = useState(e.to_address);
  const [subject, setSubject] = useState(e.subject ?? "");
  const [body, setBody] = useState(e.body ?? "");
  const dirty = to !== e.to_address || subject !== (e.subject ?? "") || body !== (e.body ?? "");
  return (
    <Card className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{c.name} <span className="text-xs font-normal text-zinc-500">{c.summary?.hook ? `· ${c.summary.hook}` : ""}</span></p>
        <Badge tone={STATUS[e.status]?.tone ?? "neutral"}>{STATUS[e.status]?.label ?? e.status}</Badge>
      </div>
      {e.error && <Notice tone="bad">{e.error}</Notice>}
      {e.status === "ready" && e.auto_send_at && (
        <Notice tone="neutral">Sends automatically at {new Date(e.auto_send_at).toLocaleTimeString()} unless you skip it. Edits you save are what gets sent.</Notice>
      )}
      {e.warnings.map((w, i) => <Notice key={i} tone="warn">{w}</Notice>)}
      <div className="grid gap-2 sm:grid-cols-[80px_1fr] sm:items-center">
        <Label htmlFor={`to${e.id}`}>To</Label>
        <Input id={`to${e.id}`} value={to} onChange={(x) => setTo(x.target.value)} />
        <Label htmlFor={`sub${e.id}`}>Subject</Label>
        <Input id={`sub${e.id}`} value={subject} onChange={(x) => setSubject(x.target.value)} />
      </div>
      <textarea aria-label="Email body" className="h-72 w-full rounded-md border border-zinc-300 bg-transparent p-3 font-sans text-sm leading-relaxed dark:border-zinc-700" value={body} onChange={(x) => setBody(x.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        {e.resume_id && <a className="text-xs underline" href={`/api/backend/resumes/${e.resume_id}/pdf`} target="_blank" rel="noreferrer noopener">Attached resume (PDF)</a>}
        {e.job_id && <Link className="text-xs underline" href={`/jobs/${e.job_id}`}>Resume details</Link>}
        <span className="flex-1" />
        {dirty && <Button size="sm" variant="outline" disabled={busy} onClick={() => act(() => call(`/outreach/emails/${e.id}`, { method: "PATCH", json: { to_address: to, subject, body } }), "Draft saved.")}>Save changes</Button>}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(() => call(`/outreach/emails/${e.id}/skip`, { method: "POST" }), "Skipped. It won't be sent.")}>Skip</Button>
        <Button size="sm" disabled={busy || dirty || e.status === "approved"} title={dirty ? "Save your changes first" : undefined}
          onClick={() => window.confirm(`Send this email to ${e.to_address} from your mailbox?`) && act(() => call(`/outreach/emails/${e.id}/send`, { method: "POST" }), "Sending (or queued if today's limit is reached).")}>
          {e.auto_send_at ? "Send now" : "Send"}
        </Button>
      </div>
    </Card>
  );
}

type Source = {
  id: number;
  url: string;
  label: string | null;
  location: string | null;
  enabled: boolean;
  last_read_at: string | null;
  entries_found: number;
  added_total: number;
  error: string | null;
};

function Directories() {
  const [sources, setSources] = useState<Source[]>([]);
  const [url, setUrl] = useState("");
  const [loc, setLoc] = useState("");
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void call<Source[]>("/outreach/sources").then(setSources);
  }, []);
  async function add() {
    setBusy(true);
    try {
      setSources(await call<Source[]>("/outreach/sources", { method: "POST", json: { url, location: loc || null } }));
      setUrl(""); setLoc("");
      setMsg({ tone: "good", text: "Directory added and read." });
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="space-y-3 text-sm">
      <div>
        <p className="font-medium">Startup directories</p>
        <p className="text-xs text-zinc-500">
          Websites that list startups (like bangalorestartupmap.com). Each search, the agent takes the startups that best fit your roles
          (seed to Series C first; VCs, accelerators and big companies are skipped), then reads their sites and emails them. Directories are read
          once a day, respecting their robots.txt.
        </p>
      </div>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      {sources.map((s) => (
        <div key={s.id} className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
          <a className="font-medium underline-offset-2 hover:underline" href={s.url} target="_blank" rel="noreferrer noopener">{s.label ?? s.url}</a>
          {s.location && <Badge>{s.location}</Badge>}
          <span className="text-xs text-zinc-500">
            {s.last_read_at ? `${s.entries_found} startups listed · ${s.added_total} picked so far` : "not read yet (read on the next search)"}
          </span>
          {s.error && <span className="text-xs text-red-600">{s.error}</span>}
          <span className="flex-1" />
          <Button size="sm" variant="ghost" onClick={() => void call<Source[]>(`/outreach/sources/${s.id}`, { method: "DELETE" }).then(setSources)}>Remove</Button>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Input className="min-w-64 flex-1" placeholder="https://www.bangalorestartupmap.com/" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Input className="w-40" placeholder="city (optional)" value={loc} onChange={(e) => setLoc(e.target.value)} />
        <Button size="sm" className="h-9" disabled={!url || busy} onClick={() => void add()}>Add directory</Button>
      </div>
    </Card>
  );
}
