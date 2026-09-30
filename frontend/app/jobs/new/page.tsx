"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, Input, Label, Notice, Select } from "@/components/ui";
import { call } from "@/lib/client";
import type { JobDetail } from "@/lib/types";

export default function NewJobPage() {
  const router = useRouter();
  const [jd, setJd] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [pageLimit, setPageLimit] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const job = await call<JobDetail>("/jobs/manual", {
        method: "POST",
        json: {
          jd_text: jd,
          company: company.trim() || null,
          role: role.trim() || null,
          page_limit: pageLimit ? Number(pageLimit) : null,
        },
      });
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">New resume from a job description</h1>
        <p className="text-sm text-zinc-500">
          Manual mode, no email or portal involved. The agent reads the JD, picks your most relevant experience, writes a tailored resume in your template,
          and checks it against the page limit and ATS threshold. It usually takes 1–3 minutes.
        </p>
      </header>
      <Card className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor="company">Company (optional)</Label>
            <Input id="company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="role">Role (optional)</Label>
            <Input id="role" value={role} onChange={(e) => setRole(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="pages">Page limit</Label>
            <Select id="pages" value={pageLimit} onChange={(e) => setPageLimit(e.target.value)}>
              <option value="">Default (settings)</option>
              <option value="1">1</option>
              <option value="2">2</option>
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="jd">Job description</Label>
          <textarea
            id="jd"
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the full job description, including eligibility (CGPA, branch, batch) if listed…"
            className="h-80 w-full rounded-md border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
          <p className="text-xs text-zinc-500">{jd.trim().length} characters (at least 50)</p>
        </div>
        {error && <Notice tone="bad">{error}</Notice>}
        <Button onClick={submit} disabled={busy || jd.trim().length < 50}>{busy ? "Starting…" : "Generate resume"}</Button>
      </Card>
    </div>
  );
}
