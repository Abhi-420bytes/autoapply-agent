"use client";

import { useState } from "react";
import { Badge, Button, Card, Input, Label, Notice, SectionTitle, Select } from "@/components/ui";
import { ApiError, call } from "@/lib/client";
import type { Provider, TaskConfig } from "@/lib/types";
import { ModelPicker } from "./ModelPicker";

const TITLES: Record<string, string> = {
  email_classifier: "Email classifier",
  jd_analyzer: "JD analyzer",
  resume_writer: "Resume writer",
  ats_scorer: "ATS scorer",
  final_polish: "Final polish",
  portal_helper: "Portal helper",
  repo_summarizer: "Repo summarizer",
  embedding: "Embeddings",
};

export function TasksSection({
  tasks,
  providers,
  onChange,
}: {
  tasks: TaskConfig[];
  providers: Provider[];
  onChange: () => Promise<void>;
}) {
  return (
    <section>
      <SectionTitle
        title="Model per task"
        subtitle="Each task routes through the gateway to the model you pick. If the primary fails (after one retry), the fallback is used."
      />
      <div className="space-y-3">
        {tasks.map((t) => (
          <TaskRow key={t.task} config={t} providers={providers} onSaved={onChange} />
        ))}
      </div>
    </section>
  );
}

function TaskRow({
  config,
  providers,
  onSaved,
}: {
  config: TaskConfig;
  providers: Provider[];
  onSaved: () => Promise<void>;
}) {
  const eligible = providers.filter((p) => (config.is_embedding ? p.supports_embeddings : p.supports_chat));
  const [providerId, setProviderId] = useState<number | null>(config.provider_id);
  const [model, setModel] = useState(config.model ?? "");
  const [fbProviderId, setFbProviderId] = useState<number | null>(config.fallback_provider_id);
  const [fbModel, setFbModel] = useState(config.fallback_model ?? "");
  const [temperature, setTemperature] = useState(String(config.params.temperature));
  const [maxTokens, setMaxTokens] = useState(String(config.params.max_tokens));
  const [timeout, setTimeoutS] = useState(String(config.params.timeout_s));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "good" | "bad" | "warn"; text: string } | null>(null);

  async function save(confirmReindex = false) {
    setBusy(true);
    setMsg(null);
    const body = {
      provider_id: providerId,
      model: model.trim() || null,
      fallback_provider_id: config.is_embedding ? null : fbProviderId,
      fallback_model: config.is_embedding ? null : fbModel.trim() || null,
      params: config.is_embedding
        ? { timeout_s: Number(timeout) }
        : { temperature: Number(temperature), max_tokens: Number(maxTokens), timeout_s: Number(timeout) },
      confirm_reindex: confirmReindex,
    };
    try {
      const r = await call<{ reindex_scheduled: boolean }>(`/llm/tasks/${config.task}`, { method: "PUT", json: body });
      setMsg(
        r.reindex_scheduled
          ? { tone: "warn", text: "Saved. A full re-index has been scheduled; the worker starts it within a minute." }
          : { tone: "good", text: "Saved." },
      );
      await onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && config.is_embedding && !confirmReindex) {
        setBusy(false);
        if (window.confirm(`${e.message}\n\nVectors from different models are never mixed, so every chunk will be embedded again.`)) {
          return save(true);
        }
        setMsg({ tone: "warn", text: "Not changed." });
        return;
      }
      setMsg({ tone: "bad", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(false);
    }
  }

  const providerSelect = (value: number | null, set: (v: number | null) => void, label: string, allowNone: boolean) => (
    <Select
      aria-label={label}
      value={value ?? ""}
      onChange={(e) => set(e.target.value ? Number(e.target.value) : null)}
    >
      <option value="">{allowNone ? "None" : "Choose provider…"}</option>
      {eligible.map((p) => (
        <option key={p.id} value={p.id} disabled={!p.enabled}>
          {p.label}
          {p.enabled ? "" : " (disabled)"}
        </option>
      ))}
    </Select>
  );

  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">{TITLES[config.task] ?? config.task}</span>
        {config.configured ? <Badge tone="good">configured</Badge> : <Badge tone="warn">not set</Badge>}
        <span className="w-full text-xs text-zinc-500 sm:w-auto">{config.description}</span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label>Primary</Label>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {providerSelect(providerId, (v) => { setProviderId(v); setModel(""); }, `${config.task} provider`, false)}
            <ModelPicker providerId={providerId} embedding={config.is_embedding} value={model} onChange={setModel} ariaLabel={`${config.task} model`} />
          </div>
        </div>
        {!config.is_embedding && (
          <div>
            <Label>Fallback (optional)</Label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {providerSelect(fbProviderId, (v) => { setFbProviderId(v); setFbModel(""); }, `${config.task} fallback provider`, true)}
              <ModelPicker providerId={fbProviderId} embedding={false} value={fbModel} onChange={setFbModel} ariaLabel={`${config.task} fallback model`} />
            </div>
          </div>
        )}
        {config.is_embedding && (
          <Notice tone="warn">
            No fallback for embeddings: another model&apos;s vectors aren&apos;t comparable. Changing this model re-indexes the whole knowledge base.
          </Notice>
        )}
      </div>

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs text-zinc-500">Advanced</summary>
        <div className="mt-2 grid grid-cols-3 gap-2 sm:max-w-md">
          {!config.is_embedding && (
            <>
              <div>
                <Label>Temperature</Label>
                <Input type="number" step="0.1" min="0" max="2" value={temperature} onChange={(e) => setTemperature(e.target.value)} />
              </div>
              <div>
                <Label>Max tokens</Label>
                <Input type="number" min="1" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              </div>
            </>
          )}
          <div>
            <Label>Timeout (s)</Label>
            <Input type="number" min="1" value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
          </div>
        </div>
      </details>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => save()} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </Button>
        {msg && <span className="min-w-0 flex-1"><Notice tone={msg.tone}>{msg.text}</Notice></span>}
      </div>
    </Card>
  );
}
