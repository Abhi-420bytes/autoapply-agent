"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, Card, Input, Label, Notice } from "@/components/ui";
import { call } from "@/lib/client";

export default function OutreachSettingsPage() {
  const [masked, setMasked] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const load = async () => {
    const s = await call<{ name: string; masked: string | null }[]>("/settings/secrets");
    setMasked(s.find((x) => x.name === "tavily_api_key")?.masked ?? null);
  };
  useEffect(() => {
    void load();
  }, []);
  async function save() {
    try {
      await call("/settings/secrets/tavily_api_key", { method: "PUT", json: { value: value.trim() } });
      setValue("");
      setMsg({ tone: "good", text: "Tavily key saved (encrypted). Web search is on." });
      await load();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }
  return (
    <div className="max-w-3xl space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Outreach &amp; web search</h1>
        <p className="text-sm text-zinc-500">
          Web search lets the agent find a LinkedIn posting on the company&apos;s own hiring site (so it can apply there instead of LinkedIn),
          and find companies and startups for cold emails.
        </p>
      </header>
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <Card className="space-y-3 text-sm">
        <p className="font-medium">
          Tavily API key{" "}
          {masked ? <span className="font-mono text-xs text-zinc-500">({masked})</span> : <span className="text-xs text-zinc-500">(not set)</span>}
        </p>
        <ol className="list-decimal space-y-1 pl-5 text-xs text-zinc-500">
          <li>
            Go to <a className="underline" href="https://app.tavily.com" target="_blank" rel="noreferrer noopener">app.tavily.com</a> and sign up (Google sign-in works).
            The free plan has 1,000 searches a month and doesn&apos;t ask for a card.
          </li>
          <li>Copy your API key (it starts with <code>tvly-</code>) and paste it below.</li>
        </ol>
        <div className="flex gap-2">
          <Label htmlFor="tavily" className="sr-only">Tavily API key</Label>
          <Input id="tavily" type="password" autoComplete="off" placeholder={masked ?? "tvly-..."} value={value} onChange={(e) => setValue(e.target.value)} />
          <Button size="sm" className="h-9" onClick={save} disabled={!value.trim()}>Save</Button>
        </div>
        <p className="text-xs text-zinc-500">
          The key is stored encrypted and only sent to Tavily. Without it the agent tries Gemini&apos;s Google Search, which Google only enables on
          billed Gemini keys. Email addresses found by search are used only after the agent sees them on the company&apos;s own page.
        </p>
      </Card>
      <p className="text-sm">
        Set locations, roles and the daily limit on the <Link className="underline" href="/outreach">Outreach</Link> page.
      </p>
    </div>
  );
}
