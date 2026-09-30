"use client";

import { useEffect, useState } from "react";
import { Button, Card, Notice, Switch } from "@/components/ui";
import { call } from "@/lib/client";

type Info = { desktop: boolean; autostart: boolean; platform: string };

/** Shown only in the desktop app (no Docker): start at login, and quit. */
export function DesktopControls() {
  const [info, setInfo] = useState<Info | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    void call<Info>("/desktop").then(setInfo).catch(() => setInfo(null));
  }, []);
  if (!info?.desktop) return null;
  return (
    <section>
      <h2 className="mb-3 text-base font-semibold tracking-tight">Desktop app</h2>
      <Card className="space-y-4 text-sm">
        {msg && <Notice tone="good">{msg}</Notice>}
        <label className="flex items-start gap-3">
          <Switch
            label="Start AutoApply when I log in"
            checked={info.autostart}
            onChange={(v) => void call<Info>("/desktop/autostart", { method: "PUT", json: { enabled: v } }).then(setInfo)}
          />
          <span>
            <b>Start AutoApply when I log in</b>{" "}
            <span className="text-zinc-500">so the agent keeps checking email, job sites and outreach in the background (no window opens).</span>
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-3 border-t border-zinc-100 pt-4 dark:border-zinc-800">
          <Button
            variant="outline"
            onClick={() => {
              if (!window.confirm("Quit AutoApply? The agent stops until you open AutoApply again.")) return;
              void call("/desktop/quit", { method: "POST" }).then(() => setMsg("AutoApply is quitting. Open it again from Applications / the Start menu."));
            }}
          >
            Quit AutoApply
          </Button>
          <span className="text-xs text-zinc-500">Closing the window keeps the agent running; this stops it completely.</span>
        </div>
      </Card>
    </section>
  );
}
