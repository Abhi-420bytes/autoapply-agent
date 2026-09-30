"use client";

import { useEffect, useState } from "react";
import { Button, Card, Input, Label, Notice, SectionTitle, Select, Switch } from "@/components/ui";
import { call } from "@/lib/client";
import type { AppSettings } from "@/lib/api";

type Education = { institution: string; degree?: string | null; branch?: string | null; cgpa?: number | null; start_year?: number | null; graduation_year?: number | null };
type Profile = {
  full_name: string;
  email: string | null;
  phone: string | null;
  location: string | null;
  links: Record<string, string>;
  education: Education[];
  skills: string[];
  form_fields: Record<string, string>;
};

export default function GeneralSettingsPage() {
  const [s, setS] = useState<AppSettings | null>(null);
  const [p, setP] = useState<Profile | null>(null);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  useEffect(() => {
    void Promise.all([call<AppSettings>("/settings"), call<Profile>("/settings/profile")]).then(([a, b]) => {
      setS(a);
      setP(b);
    });
  }, []);

  async function saveSettings(patch: Partial<AppSettings>) {
    setMsg(null);
    try {
      setS(await call<AppSettings>("/settings", { method: "PATCH", json: patch }));
      setMsg({ tone: "good", text: "Settings saved." });
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  async function saveProfile() {
    if (!p) return;
    setMsg(null);
    try {
      setP(await call<Profile>("/settings/profile", { method: "PUT", json: p }));
      setMsg({ tone: "good", text: "Profile saved." });
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  if (!s || !p) return <p className="text-sm text-zinc-500">Loading…</p>;
  const edu = p.education[0] ?? { institution: "" };
  const setEdu = (patch: Partial<Education>) => setP({ ...p, education: [{ ...edu, ...patch }, ...p.education.slice(1)] });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  return (
    <div className="max-w-3xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-zinc-500">How the agent behaves, and the facts about you it may use.</p>
      </header>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}

      <section>
        <SectionTitle title="Automation" />
        <Card className="space-y-4">
          <label className="flex items-start gap-3">
            <Switch label="Auto-apply" checked={s.auto_apply} onChange={(v) => {
              if (v && !window.confirm("With auto-apply ON, applications are submitted without waiting for your approval. CAPTCHA/OTP still pause for you. Turn it on?")) return;
              void saveSettings({ auto_apply: v });
            }} />
            <span className="text-sm">
              <b>Auto-apply</b> is {s.auto_apply ? "ON" : "OFF"}.{" "}
              {s.auto_apply ? "Ready applications are submitted automatically." : "The agent prepares everything and waits for you to click Approve."}
            </span>
          </label>
          <label className="flex items-start gap-3">
            <Switch label="Add the AutoApply Agent project to my resumes" checked={s.include_signature_project} onChange={(v) => void saveSettings({ include_signature_project: v })} />
            <span className="text-sm">
              <b>Add the AutoApply Agent project to my resumes</b>{" "}
              <span className="text-zinc-500">(one line). Only turn this on if you built or contributed to AutoApply; a resume must never claim work you didn&apos;t do.</span>
            </span>
          </label>
          <fieldset className="space-y-1 text-sm">
            <legend className="font-medium">Job levels to prepare resumes for</legend>
            <p className="text-xs text-zinc-500">
              Every job is labelled Fresher or Experienced from its title and description (e.g. &quot;0–1 years&quot;, &quot;2026 batch&quot; vs
              &quot;3+ years&quot;, &quot;Senior&quot;). Unticked levels are set aside before any resume is made, so they use no AI quota.
            </p>
            <div className="flex flex-wrap gap-4 pt-1">
              {([
                ["fresher", "Fresher / entry level"],
                ["experienced", "Experienced / senior"],
                ["unknown", "Level not stated"],
              ] as const).map(([value, label]) => (
                <label key={value} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={s.target_levels.includes(value)}
                    onChange={(e) => {
                      const next = e.target.checked ? [...s.target_levels, value] : s.target_levels.filter((x) => x !== value);
                      if (next.length === 0) return;
                      void saveSettings({ target_levels: next });
                    }}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <Label htmlFor="delay">Default delay before processing (min)</Label>
              <Input id="delay" type="number" min={0} defaultValue={s.global_delay_minutes} onBlur={(e) => saveSettings({ global_delay_minutes: Number(e.target.value) })} />
            </div>
            <div>
              <Label htmlFor="ats">ATS threshold</Label>
              <Input id="ats" type="number" min={0} max={100} defaultValue={s.ats_threshold} onBlur={(e) => saveSettings({ ats_threshold: Number(e.target.value) })} />
            </div>
            <div>
              <Label htmlFor="pages">Page limit</Label>
              <Select id="pages" value={s.page_limit} onChange={(e) => saveSettings({ page_limit: Number(e.target.value) })}>
                <option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="iters">Quality loop attempts</Label>
              <Select id="iters" value={s.max_quality_iterations} onChange={(e) => saveSettings({ max_quality_iterations: Number(e.target.value) })}>
                {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </div>
            <div>
              <Label htmlFor="tz">Timezone</Label>
              <Input id="tz" defaultValue={s.timezone} onBlur={(e) => saveSettings({ timezone: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="rfn">Resume file name</Label>
              <div className="flex items-center gap-1.5">
                <Input id="rfn" placeholder="Abhiram" defaultValue={s.resume_file_name} onBlur={(e) => e.target.value !== s.resume_file_name && saveSettings({ resume_file_name: e.target.value })} />
                <span className="text-sm text-zinc-500">.pdf</span>
              </div>
              <p className="mt-1 text-xs text-zinc-500">Used for applications, email attachments and downloads.</p>
            </div>
          </div>
        </Card>
      </section>

      <section>
        <SectionTitle title="Profile" subtitle="Used for eligibility checks, the resume header facts, and portal form fields. Keep it accurate." />
        <Card className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label htmlFor="name">Full name</Label><Input id="name" value={p.full_name} onChange={(e) => setP({ ...p, full_name: e.target.value })} /></div>
            <div><Label htmlFor="email">Email</Label><Input id="email" type="email" value={p.email ?? ""} onChange={(e) => setP({ ...p, email: e.target.value || null })} /></div>
            <div><Label htmlFor="phone">Phone</Label><Input id="phone" value={p.phone ?? ""} onChange={(e) => setP({ ...p, phone: e.target.value || null })} /></div>
            <div><Label htmlFor="loc">Location</Label><Input id="loc" value={p.location ?? ""} onChange={(e) => setP({ ...p, location: e.target.value || null })} /></div>
          </div>
          <p className="pt-2 text-xs font-medium">Current degree</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="sm:col-span-2"><Label htmlFor="inst">Institution</Label><Input id="inst" value={edu.institution} onChange={(e) => setEdu({ institution: e.target.value })} /></div>
            <div><Label htmlFor="deg">Degree</Label><Input id="deg" value={edu.degree ?? ""} onChange={(e) => setEdu({ degree: e.target.value || null })} placeholder="B.Tech" /></div>
            <div><Label htmlFor="branch">Branch</Label><Input id="branch" value={edu.branch ?? ""} onChange={(e) => setEdu({ branch: e.target.value || null })} placeholder="Computer Science" /></div>
            <div><Label htmlFor="cgpa">CGPA (out of 10)</Label><Input id="cgpa" type="number" step="0.01" value={edu.cgpa ?? ""} onChange={(e) => setEdu({ cgpa: num(e.target.value) })} /></div>
            <div><Label htmlFor="grad">Graduation year (batch)</Label><Input id="grad" type="number" value={edu.graduation_year ?? ""} onChange={(e) => setEdu({ graduation_year: num(e.target.value) })} /></div>
          </div>
          <div>
            <Label htmlFor="skills">Skills (comma-separated; used for tagging, only literal matches count)</Label>
            <Input id="skills" value={p.skills.join(", ")} onChange={(e) => setP({ ...p, skills: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {(["linkedin", "github", "portfolio"] as const).map((k) => (
              <div key={k}>
                <Label htmlFor={k}>{k[0].toUpperCase() + k.slice(1)} URL</Label>
                <Input id={k} value={p.links[k] ?? ""} onChange={(e) => {
                  const links = { ...p.links };
                  if (e.target.value) links[k] = e.target.value; else delete links[k];
                  setP({ ...p, links });
                }} />
              </div>
            ))}
          </div>
          <Button onClick={saveProfile} disabled={!edu.institution && p.education.length > 0}>Save profile</Button>
        </Card>
      </section>
    </div>
  );
}
