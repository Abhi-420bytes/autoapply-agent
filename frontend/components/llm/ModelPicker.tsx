"use client";

import { useEffect, useId, useState } from "react";
import { Input } from "@/components/ui";
import { call } from "@/lib/client";
import type { ModelList } from "@/lib/types";

const cache = new Map<string, Promise<ModelList>>();

export function loadModels(providerId: number, embedding: boolean): Promise<ModelList> {
  const key = `${providerId}:${embedding}`;
  if (!cache.has(key)) {
    const p = call<ModelList>(`/llm/providers/${providerId}/models?embedding=${embedding}`);
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return cache.get(key)!;
}

export function invalidateModels(providerId: number) {
  for (const k of Array.from(cache.keys())) if (k.startsWith(`${providerId}:`)) cache.delete(k);
}

/** Text input with a dropdown of the provider's models; any custom id can be typed. */
export function ModelPicker({
  providerId,
  embedding,
  value,
  onChange,
  disabled,
  ariaLabel,
}: {
  providerId: number | null;
  embedding: boolean;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const listId = useId();
  const [list, setList] = useState<ModelList | null>(null);

  useEffect(() => {
    setList(null);
    if (providerId == null) return;
    let live = true;
    loadModels(providerId, embedding)
      .then((l) => live && setList(l))
      .catch(() => live && setList({ models: [], source: "suggested", error: "could not load" }));
    return () => {
      live = false;
    };
  }, [providerId, embedding]);

  return (
    <div className="min-w-0">
      <Input
        list={listId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={providerId == null ? "pick a provider first" : list ? "choose or type a model id" : "loading models…"}
        disabled={disabled || providerId == null}
        aria-label={ariaLabel}
      />
      <datalist id={listId}>
        {list?.models.map((m) => <option key={m} value={m} />)}
      </datalist>
      {list?.source === "suggested" && providerId != null && (
        <p className="mt-0.5 truncate text-[11px] text-zinc-500" title={list.error ?? undefined}>
          Live list unavailable{list.error ? ` (${list.error})` : ""}; showing suggestions.
        </p>
      )}
    </div>
  );
}
