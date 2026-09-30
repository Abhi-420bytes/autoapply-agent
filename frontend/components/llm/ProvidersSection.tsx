"use client";

import { useEffect, useState } from "react";
import { Badge, Button, Card, Input, Label, Notice, SectionTitle, Select, Switch } from "@/components/ui";
import { call } from "@/lib/client";
import type { Provider, ProviderSpec, TestResult } from "@/lib/types";
import { invalidateModels, loadModels, ModelPicker } from "./ModelPicker";

export function ProvidersSection({
  catalog,
  providers,
  onChange,
}: {
  catalog: ProviderSpec[];
  providers: Provider[];
  onChange: () => Promise<void>;
}) {
  return (
    <section>
      <SectionTitle
        title="Providers & API keys"
        subtitle="Keys are encrypted at rest and only ever shown masked."
      />
      <div className="space-y-3">
        {providers.length === 0 && <Notice>No providers yet. Add one below to get started.</Notice>}
        {providers.map((p) => (
          <ProviderCard key={p.id} provider={p} spec={catalog.find((c) => c.kind === p.kind)} onChange={onChange} />
        ))}
        <AddProvider catalog={catalog} onAdded={onChange} />
      </div>
    </section>
  );
}

function TestStatus({ p }: { p: Provider }) {
  if (p.last_test_ok === null) return <Badge>not tested</Badge>;
  if (p.last_test_ok) return <Badge tone="good">OK · {p.last_test_latency_ms} ms</Badge>;
  return <Badge tone="bad">test failed</Badge>;
}

function ProviderCard({
  provider: p,
  spec,
  onChange,
}: {
  provider: Provider;
  spec?: ProviderSpec;
  onChange: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(p.label);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(p.base_url ?? "");
  const [testModel, setTestModel] = useState(spec?.suggested_models[0] ?? spec?.suggested_embedding_models[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  // Default the test model to one the key can actually see: a suggestion that's in the
  // live list, else the first live model. Hard-coded suggestions can go stale.
  useEffect(() => {
    let live = true;
    const embedding = !p.supports_chat;
    loadModels(p.id, embedding)
      .then((list) => {
        if (!live || list.source !== "live" || list.models.length === 0) return;
        const suggested = embedding ? spec?.suggested_embedding_models : spec?.suggested_models;
        setTestModel((current) => {
          if (current && list.models.includes(current)) return current;
          return suggested?.find((m) => list.models.includes(m)) ?? list.models[0];
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [p.id, p.supports_chat, spec]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(false);
    }
  }

  const save = () =>
    run(async () => {
      const body: Record<string, unknown> = { label };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      if (spec?.requires_base_url || p.base_url || baseUrl) body.base_url = baseUrl.trim() || null;
      await call(`/llm/providers/${p.id}`, { method: "PATCH", json: body });
      setApiKey("");
      setEditing(false);
      invalidateModels(p.id);
      await onChange();
    });

  const toggle = (enabled: boolean) =>
    run(async () => {
      await call(`/llm/providers/${p.id}`, { method: "PATCH", json: { enabled } });
      await onChange();
    });

  const remove = () =>
    run(async () => {
      if (!window.confirm(`Delete provider "${p.label}"? Its stored key will be erased.`)) return;
      await call(`/llm/providers/${p.id}`, { method: "DELETE" });
      await onChange();
    });

  const test = () =>
    run(async () => {
      const r = await call<TestResult>(`/llm/providers/${p.id}/test`, {
        method: "POST",
        json: { model: testModel.trim() || null },
      });
      setMsg(
        r.ok
          ? { tone: "good", text: `Connected to ${r.model} in ${r.latency_ms} ms — reply: "${r.reply}"` }
          : { tone: "bad", text: `${r.error_kind ?? "error"}: ${r.error}` },
      );
      await onChange();
    });

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{p.label}</span>
        <Badge>{p.display_name}</Badge>
        <TestStatus p={p} />
        {p.used_by_tasks.length > 0 && (
          <span className="text-xs text-zinc-500">used by {p.used_by_tasks.join(", ")}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Switch checked={p.enabled} onChange={toggle} disabled={busy} label={`Enable ${p.label}`} />
          <Button size="sm" variant="ghost" onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel" : "Edit"}
          </Button>
          <Button size="sm" variant="ghost" className="text-red-600" onClick={remove} disabled={busy}>
            Delete
          </Button>
        </div>
      </div>

      <div className="mt-2 grid gap-1 text-sm text-zinc-600 dark:text-zinc-400 sm:grid-cols-2">
        <div className="truncate">
          Key: <code className="font-mono">{p.api_key_masked ?? "none"}</code>
        </div>
        {p.base_url && <div className="truncate">Base URL: <code className="font-mono">{p.base_url}</code></div>}
      </div>
      {p.last_test_ok === false && p.last_test_error && !msg && (
        <p className="mt-1 break-words text-xs text-red-700 dark:text-red-400">{p.last_test_error}</p>
      )}

      {editing && (
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <div>
            <Label>Label</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} />
          </div>
          <div>
            <Label>New API key</Label>
            <Input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={p.api_key_masked ? `leave blank to keep ${p.api_key_masked}` : spec?.key_hint}
            />
          </div>
          <div>
            <Label>Base URL</Label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={spec?.default_base_url ?? "https://…"} />
          </div>
          <div className="sm:col-span-3">
            <Button size="sm" onClick={save} disabled={busy}>Save</Button>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-start gap-2">
        <div className="w-full sm:w-72">
          <ModelPicker
            providerId={p.id}
            embedding={!p.supports_chat}
            value={testModel}
            onChange={setTestModel}
            ariaLabel="Model to test with"
          />
        </div>
        <Button size="sm" variant="outline" onClick={test} disabled={busy}>
          {busy ? "Working…" : "Test connection"}
        </Button>
      </div>
      {msg && <div className="mt-2"><Notice tone={msg.tone}>{msg.text}</Notice></div>}
    </Card>
  );
}

function AddProvider({ catalog, onAdded }: { catalog: ProviderSpec[]; onAdded: () => Promise<void> }) {
  const [kind, setKind] = useState<string>("");
  const spec = catalog.find((c) => c.kind === kind);
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const showBaseUrl = spec && (spec.requires_base_url || spec.default_base_url !== null || spec.kind === "openai_compatible");
  const showKey = spec && spec.kind !== "local_sentence_transformers";

  async function add() {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      await call("/llm/providers", {
        method: "POST",
        json: {
          kind: spec.kind,
          label: label.trim() || spec.display_name,
          api_key: apiKey.trim() || null,
          base_url: baseUrl.trim() || null,
        },
      });
      setKind("");
      setLabel("");
      setApiKey("");
      setBaseUrl("");
      await onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-dashed">
      <p className="mb-3 text-sm font-medium">Add provider</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="new-kind">Provider</Label>
          <Select
            id="new-kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              const s = catalog.find((c) => c.kind === e.target.value);
              setBaseUrl(s?.default_base_url ?? "");
            }}
          >
            <option value="">Choose…</option>
            {catalog.map((c) => (
              <option key={c.kind} value={c.kind}>{c.display_name}</option>
            ))}
          </Select>
        </div>
        {spec && (
          <div>
            <Label htmlFor="new-label">Label</Label>
            <Input id="new-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={spec.display_name} />
          </div>
        )}
        {showKey && (
          <div>
            <Label htmlFor="new-key">API key{spec.requires_api_key ? "" : " (optional)"}</Label>
            <Input id="new-key" type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={spec.key_hint} />
          </div>
        )}
        {showBaseUrl && (
          <div>
            <Label htmlFor="new-url">Base URL{spec.requires_base_url ? "" : " (optional)"}</Label>
            <Input id="new-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…" />
          </div>
        )}
      </div>
      {spec?.notes && <p className="mt-2 text-xs text-zinc-500">{spec.notes}</p>}
      {error && <div className="mt-2"><Notice tone="bad">{error}</Notice></div>}
      <Button className="mt-3" onClick={add} disabled={!spec || busy}>
        {busy ? "Saving…" : "Save provider"}
      </Button>
    </Card>
  );
}
