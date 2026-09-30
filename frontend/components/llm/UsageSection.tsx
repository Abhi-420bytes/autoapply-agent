"use client";

import { useState } from "react";
import { Badge, Button, Card, Input, Label, Notice, SectionTitle } from "@/components/ui";
import { call } from "@/lib/client";
import type { UsageRow, UsageSummary } from "@/lib/types";
import { cn, usd } from "@/lib/utils";

export function UsageSection({ usage, onChange }: { usage: UsageSummary; onChange: () => Promise<void> }) {
  const [budget, setBudget] = useState(usage.budget_usd?.toString() ?? "");
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  async function saveBudget() {
    setMsg(null);
    try {
      const value = budget.trim() === "" ? null : Number(budget);
      await call("/settings", { method: "PATCH", json: { monthly_budget_usd: value } });
      setMsg({ tone: "good", text: value === null ? "Budget limit removed." : "Budget saved." });
      await onChange();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    }
  }

  const pct = usage.budget_usd ? Math.min(100, (usage.month_to_date_usd / usage.budget_usd) * 100) : 0;
  const since = new Date(usage.period_start).toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <section>
      <SectionTitle title="Budget & cost" subtitle={`Spend for ${since}. Every call is recorded with tokens and cost.`} />
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <p className="text-xs text-zinc-500">Month to date</p>
            <p className="text-2xl font-semibold tabular-nums">{usd(usage.month_to_date_usd)}</p>
          </div>
          <div className="w-40">
            <Label htmlFor="budget">Monthly limit (USD)</Label>
            <Input id="budget" type="number" min="0" step="1" placeholder="no limit" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </div>
          <Button size="sm" variant="outline" onClick={saveBudget}>Save limit</Button>
          {usage.budget_exceeded && <Badge tone="bad">limit reached: non-urgent jobs paused</Badge>}
        </div>
        {usage.budget_usd !== null && (
          <div className="mt-3 h-2 w-full overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
            <div className={cn("h-full", pct >= 100 ? "bg-red-600" : pct >= 80 ? "bg-amber-500" : "bg-emerald-600")} style={{ width: `${pct}%` }} />
          </div>
        )}
        {msg && <div className="mt-2"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
      </Card>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <UsageTable title="By task" rows={usage.by_task} />
        <UsageTable title="By model" rows={usage.by_model} />
      </div>

      <Card className="mt-3 overflow-x-auto">
        <p className="mb-2 text-sm font-medium">Recent calls</p>
        {usage.recent.length === 0 ? (
          <p className="text-sm text-zinc-500">No calls yet.</p>
        ) : (
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="text-zinc-500">
              <tr>
                <th className="py-1 pr-2 font-medium">When</th>
                <th className="pr-2 font-medium">Task</th>
                <th className="pr-2 font-medium">Model</th>
                <th className="pr-2 text-right font-medium">Tokens</th>
                <th className="pr-2 text-right font-medium">Cost</th>
                <th className="pr-2 text-right font-medium">Latency</th>
                <th className="font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {usage.recent.slice(0, 15).map((r) => (
                <tr key={r.id} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="py-1 pr-2">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="pr-2">{r.task ?? "connection test"}</td>
                  <td className="max-w-[200px] truncate pr-2" title={`${r.provider_kind}:${r.model}`}>{r.model}</td>
                  <td className="pr-2 text-right">{r.tokens_in + r.tokens_out}</td>
                  <td className="pr-2 text-right">{usd(r.cost_usd)}</td>
                  <td className="pr-2 text-right">{r.latency_ms ?? "–"} ms</td>
                  <td>
                    {r.success ? <Badge tone="good">ok</Badge> : <Badge tone="bad">{r.error_kind ?? "error"}</Badge>}
                    {r.is_fallback && <Badge tone="warn" className="ml-1">fallback</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  );
}

function UsageTable({ title, rows }: { title: string; rows: UsageRow[] }) {
  return (
    <Card className="overflow-x-auto">
      <p className="mb-2 text-sm font-medium">{title}</p>
      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500">Nothing this month.</p>
      ) : (
        <table className="w-full text-left text-xs">
          <thead className="text-zinc-500">
            <tr>
              <th className="py-1 pr-2 font-medium">{title.replace("By ", "")}</th>
              <th className="pr-2 text-right font-medium">Calls</th>
              <th className="pr-2 text-right font-medium">Failed</th>
              <th className="pr-2 text-right font-medium">Tokens</th>
              <th className="text-right font-medium">Cost</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-zinc-100 dark:border-zinc-800">
                <td className="max-w-[180px] truncate py-1 pr-2" title={r.key}>{r.key.replace(/_/g, " ")}</td>
                <td className="pr-2 text-right">{r.calls}</td>
                <td className="pr-2 text-right">{r.failures}</td>
                <td className="pr-2 text-right">{(r.tokens_in + r.tokens_out).toLocaleString()}</td>
                <td className="text-right">{usd(r.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
