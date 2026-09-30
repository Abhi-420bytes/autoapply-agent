"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Input, SectionTitle, Select, Switch } from "@/components/ui";
import { call } from "@/lib/client";
import type { KBullet } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = { past_resume: "past resume", github: "GitHub", profile: "profile" };

export function BulletBank({ version }: { version: number }) {
  const [bullets, setBullets] = useState<KBullet[]>([]);
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (source) params.set("source", source);
    if (q.trim()) params.set("q", q.trim());
    setBullets(await call<KBullet[]>(`/knowledge/bullets?${params}`));
  }, [source, q]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 250);
    return () => clearTimeout(t);
  }, [load, version]);

  async function toggle(b: KBullet, is_active: boolean) {
    await call(`/knowledge/bullets/${b.id}`, { method: "PATCH", json: { is_active } });
    await load();
  }

  async function remove(b: KBullet) {
    await call(`/knowledge/bullets/${b.id}`, { method: "DELETE" });
    await load();
  }

  return (
    <section>
      <SectionTitle title="Bullet bank" subtitle="Everything the resume writer may draw from. Switch a bullet off to keep it out of generated resumes." />
      <Card>
        <div className="mb-3 flex flex-wrap gap-2">
          <Select className="w-40" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source filter">
            <option value="">All sources</option>
            <option value="past_resume">Past resumes</option>
            <option value="github">GitHub</option>
          </Select>
          <Input className="w-64" placeholder="Filter text…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter bullets" />
          <span className="self-center text-xs text-zinc-500">{bullets.length} shown</span>
        </div>
        {bullets.length === 0 ? (
          <p className="text-sm text-zinc-500">No bullets yet. Sync GitHub or add a past resume.</p>
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {bullets.map((b) => (
              <li key={b.id} className={`flex gap-3 py-2 text-sm ${b.is_active ? "" : "opacity-50"}`}>
                <Switch label={`Use bullet ${b.id}`} checked={b.is_active} onChange={(v) => toggle(b, v)} />
                <div className="min-w-0 flex-1">
                  <p className="break-words">{b.text}</p>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-zinc-500">
                    <Badge>{SOURCE_LABEL[b.source_type] ?? b.source_type}</Badge>
                    {[b.section, b.heading].filter(Boolean).join(" · ")}
                    {b.skills.map((s) => <Badge key={s} tone="good">{s}</Badge>)}
                  </div>
                </div>
                <Button size="sm" variant="ghost" className="text-red-600" onClick={() => remove(b)} aria-label={`Delete bullet ${b.id}`}>✕</Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}
